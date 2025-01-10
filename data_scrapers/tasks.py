from functools import partial
from functools import reduce
import json
import logging
import math
import operator
import shapely
from shapely.geometry import Point, MultiPoint
from typing import Callable, Iterable

from django.db.models import QuerySet
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q
from geopy.distance import distance as geopy_distance

from ebusdjango import util
from .models import BusStation, AdminArea

logger = logging.getLogger("custom")
BUS_SYSTEM_MAX_DISTANCE = 10  # km
DISTANCE_THRESHOLD_M = 400  # m

# For Fuzzy Search
# Value between [0,1]. Higher values mean that words must be more similar to be considered a match.
SIMILARITY_THRESHOLD_W_ADMIN = 0.5  # Adjust this threshold as needed
SIMILARITY_THRESHOLD_WO_ADMIN = 0.5  # Adjust this threshold as needed


def geom_distance(geom1, geom2):
    """Wrapper for geopy.distance to calculate distance.

    geopy.distance will calculate the distance between two points, expecting (lat,lon) coordinates
    while geom are converted to tuples as (lon, lat, z)
    """
    return geopy_distance((geom1.y, geom1.x), (geom2.y, geom2.x))


def get_upper_bound_distance(station_query: QuerySet):
    """Give a simple upper bound for distance between points

    Finds the minimal longitude and latitude to create a helper point.
    Does the same for the maximum longitude and latitude and calculates the distance between these
    two points.
    This fails in regions around long 180,-180
    """

    xs = [station.geom.x for station in station_query]
    ys = [station.geom.y for station in station_query]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    geo_distance = geopy_distance((y_min, x_min), (y_max, x_max))
    return geo_distance


def filter_query_distance(query: QuerySet, distance_threshold_m):
    """Return the QuerySet if the maximum found distance is below the distance threshold.

    The max distance is found using the 2d convex hull of a QuerySet.
    Only hull elements are checked for their distance by applying a rotating caliper.
    The distance might be inaccurate if the points span more than half of the globe.
    """
    if not query.exists():
        return query
    multi_point = multi_point_from_query(query)
    convex_hull = multi_point.convex_hull
    if "exterior" not in (convex_hull.__dir__()):
        # LineString has no exterior
        x, y = convex_hull.coords.xy
    else:
        x, y = convex_hull.exterior.coords.xy
    xys = tuple(list(zip(x, y)))
    max_distance, p1, p2 = rotating_caliper(xys)
    distance_m = geopy_distance((p1[1], p1[0]), (p2[1], p2[0])).meters
    if distance_m < distance_threshold_m:
        return query
    return query.model.objects.none()


def search_station(
    station_name: str,
    possible_admins_names,
    filter_stack: Callable[[QuerySet], QuerySet],
    return_all=False,
) -> QuerySet:

    # Search directly for the name, if the coordinates are close to each other
    # i.e. closer than DISTANCE_THRESHOLD, their ids are returned
    ids = []
    base_query = BusStation.objects.all()
    query = search_exact_station(base_query, station_name)
    query = filter_stack(query)
    if not return_all and query.exists():
        return query
    ids.extend(query.values_list("id", flat=True))

    # no stations where found, or stations with exact name are further apart than
    # DISTANCE_THRESHOLD
    # Check if filtering by possible admin areas gives a clear result

    found_ids, names = get_station_ids_contained_by_admin_area(possible_admins_names, station_name)
    for name in names:
        base_query = BusStation.objects.filter(id__in=found_ids)
        query = search_exact_station(base_query, name)
        query = filter_stack(query)
        if not return_all and query.exists():
            return query
        ids.extend(query.values_list("id", flat=True))

    # Search for stations if the search name contains an admin_name
    # Filter entries based on trigram similarity
    found_ids, names = get_station_ids_contained_by_admin_area(possible_admins_names, station_name)
    for name in names:
        base_query = BusStation.objects.filter(id__in=found_ids)
        query = get_fuzzy_stations(base_query, SIMILARITY_THRESHOLD_W_ADMIN, name)
        query = filter_stack(query)
        if not return_all and query.exists():
            return query
        ids.extend(query.values_list("id", flat=True))

    # Ambiguous result: try again everywhere. This can be slow.
    # Possible optimization by indexing names or searching only unique names via a database view.
    # Querying a subset of ids with unique names does not work,
    # since large sets of ids like ID in [...] are slow, when mixed with trigram search.
    base_query = BusStation.objects.all()
    query = get_fuzzy_stations(base_query, SIMILARITY_THRESHOLD_WO_ADMIN, station_name)
    query = filter_stack(query)
    if not return_all and query.exists():
        # Log this since it might make sense to remove this part, if it rarely finds stations
        logger.info("Found a station via slow fuzzy search over all stations")
        return query
    ids.extend(query.values_list("id", flat=True))

    return base_query.filter(id__in=ids)


def search_exact_station(base_query, station_name) -> QuerySet:
    """Search if the exact name of the search query is found in the database.

    Since some parts of addresses can be ambigous, e.g. "MainSt." and "MainStreet", some
    translations are used to check both instances.
    The translation Table Looks like this:
    (St., Street),
    ...
    ]
    and is read from a file earlier.
    """
    address_translations = get_address_translations()
    address_translations_rev = [[x[1], x[0]] for x in address_translations]
    ids = []
    # Search for the name without changes
    exact_station_query = base_query.filter(name=station_name)
    if exact_station_query.exists():
        ids.extend(exact_station_query.values_list("id", flat=True))

    # Apply some conversions of the name, e.g. "MainSt." becomes "MainStreet"
    for first, second in address_translations + address_translations_rev:
        if second in station_name:
            search_name = station_name.replace(second, first)
        else:
            continue
        exact_station_query = base_query.filter(name=search_name)
        if exact_station_query.exists():
            ids.extend(exact_station_query.values_list("id", flat=True))
    return base_query.filter(id__in=ids)


def filter_for_search_area(query, search_area: shapely.area):
    ids = []
    for element in query:
        if search_area.contains(Point(element.geom)):
            ids.append(element.id)
    query = query.model.objects.filter(id__in=ids)
    return query


def get_station_ids_contained_by_admin_area(possible_admins_names, station_name):
    found_ids = []
    names = []
    for part in station_name.replace(",", " ").split(" "):
        if part in possible_admins_names:
            admin_name = part
            # AdminArea names are not unique, especially below level 9 (Gemeinden, Bezirke etc.)
            admin_areas = AdminArea.objects.filter(name=admin_name)
            all_children = get_lower_admin_areas(admin_areas)

            names.append(station_name.replace(part, "").strip())
            found_ids.extend(
                BusStation.objects.filter(admin_area__in=all_children).values_list("id", flat=True)
            )
    return found_ids, names


def get_lower_admin_areas(admin_areas):
    parents = list(admin_areas.distinct())
    # Make sure all parents are at least level 8
    for i in range(len(parents)):
        parent = parents[i]
        while parent.admin_level >= 9:
            parent = parent.upper_admin_area
        parents[i] = parent
    parent_ids = {p.id for p in parents}
    all_children = [*(AdminArea.objects.filter(id__in=parent_ids))]
    children = AdminArea.objects.filter(upper_admin_area__in=all_children)
    while children.exists():
        all_children.extend(children)
        children = AdminArea.objects.filter(upper_admin_area__in=children)
    return AdminArea.objects.filter(id__in=set(x.id for x in all_children))


def get_fuzzy_stations(base_query: QuerySet, similarity_threshold, station_name):
    fuzzy_stations = (
        base_query.annotate(similarity=TrigramSimilarity("name", station_name))
        .filter(similarity__gte=similarity_threshold)
        .order_by("-similarity")
    )
    if fuzzy_stations.exists():
        best_similarity = fuzzy_stations.first().similarity
        # Without delta lookup fails at times
        fuzzy_stations = fuzzy_stations.filter(similarity__gte=best_similarity - 0.01)
    fuzz_station_query_w_admin_ids = list(fuzzy_stations.values_list("id", flat=True))
    fuzzy_stations = BusStation.objects.filter(id__in=fuzz_station_query_w_admin_ids)
    return fuzzy_stations


def get_address_translations():
    p = util.get_static_file_path(__package__, "address_translations.json")
    with open(p, "r") as f:
        translations = json.load(f)
    return translations


def approximate_lat_lon_distance(latitude):
    """Approximate a distance by averaging lat and lng difference in km.

    The distance of 1° change in longitude depends on the latitude.

    :param latitude: latitude where the average distance is approximated
    :return: average distance in km of a 1° change in lat or lon (float)
    """

    lat_distance = geopy_distance((latitude, 0), (latitude + 1, 0))
    lon_distance = geopy_distance((latitude, 0), (latitude, +1))
    return (lat_distance + lon_distance).km / 2


def multi_point_from_query(query):
    return MultiPoint([[geom.x, geom.y] for geom in query.values_list("geom", flat=True)])


def get_all_bus_stations_of_admin_areas_in_query(
    query: QuerySet, found_stations: QuerySet
) -> QuerySet:
    all_admin_area_ids = found_stations.distinct("admin_area__id").values_list(
        "admin_area__id", flat=True
    )
    all_areas = get_lower_admin_areas(AdminArea.objects.filter(id__in=all_admin_area_ids))
    return query.filter(admin_area__in=all_areas)


def rotating_caliper(xys):
    """Calculate the maximum distance between points of a convex hull by using a rotating caliper.

    https://en.wikipedia.org/wiki/Rotating_calipers
    Convex hull must be given in clock or anti-clockwise fashion.
    Algorithm searches for a single local maximum. The global maximum belongs to a vertice which
    does not produce two local maximums per the nature of the convex hull. Finding the biggest
    local maximum therefore produces the global maximum.
    """

    ii = 0
    if len(xys) == 0:
        # No element. Maximum distance is zero with no found points
        return 0, None, None
    if len(xys) == 1:
        # 1 element Maximum distance is zero with single point twice
        return 0, xys[0], xys[0]

    def edge_distance(edge, vertex):
        """Get the max. distance squared between an edge and a vertex.

        :param edge: iterable of two 2d-points building an edge
        :param vertex: tuple of a single 2d-point
        :return: maximum distance and point of edge with the maximum distance
        """

        dist1_squared = math.pow(edge[0][0] - vertex[0], 2) + math.pow(edge[0][1] - vertex[1], 2)
        dist2_squared = math.pow(edge[1][0] - vertex[0], 2) + math.pow(edge[1][1] - vertex[1], 2)
        if dist1_squared > dist2_squared:
            return dist1_squared, edge[0]
        return dist2_squared, edge[1]

    point1 = xys[0]
    point2 = xys[1]
    max_distance = 0
    for i in range(len(xys) - 1):
        current_point1 = None
        current_point2 = None
        current_max_distance = 0
        # iterate over all edges if the convex hull and calculate the distance to the point(xys[ii])
        # this point dynamically changes if a positive gradient in distance is detected.
        # this finds a local maximum for each edge. Some points can have multiple local maxima of
        # distance when paired with vertices around the circumference. The vertice which is part of
        # the maximum distance only has a single maximum which is found.
        edge = (xys[i], xys[i + 1])
        ii = (ii - 1) % len(xys)
        # distances are only compared with each other. edge_distance is not extracting the root
        cur_edge_distance, edge_point = edge_distance(edge, xys[ii])
        while cur_edge_distance >= current_max_distance:
            # move the point as long as distance increases
            current_point1 = edge_point
            current_point2 = xys[ii]
            current_max_distance = cur_edge_distance
            ii = (ii - 1) % len(xys)

            cur_edge_distance, edge_point = edge_distance(edge, xys[ii])

        ii = (ii + 1) % len(xys)
        cur_edge_distance, edge_point = edge_distance(edge, xys[ii])
        while cur_edge_distance >= current_max_distance:
            # move the point in the other direction as long as distance increases
            current_point1 = edge_point
            current_point2 = xys[ii]
            current_max_distance = cur_edge_distance
            ii = (ii + 1) % len(xys)
            cur_edge_distance, edge_point = edge_distance(edge, xys[ii])
        # The dynamic reference point is at a local maximum in reference to the current edge.
        # As the edge moves in one direction, the dynamic reference point will be moved
        # slightly if this leads to an increase of the current maximum distance.
        if current_max_distance > max_distance:
            max_distance = current_max_distance
            point1 = current_point1
            point2 = current_point2
    return max_distance, point1, point2


def replace_german_chars(text: str) -> str:
    """Fix some german characters that get send when using the API directly from the browser window."""
    encoding_issues = [
        ("Ã¤", "ä"),
        ("Ã¼", "ü"),
        ("ã¶", "ö"),
        ("ãÿ", "ß"),
        ("Ã–", "Ö"),
        ("Ã„", "Ä"),
        ("Ãœ", "Ü"),
    ]
    for search, replace in encoding_issues:
        text = text.replace(search, replace)
    return text


def search_stations(search_station_names: Iterable, use_filter: bool):
    names = list()
    for station_name in search_station_names:
        search_name = replace_german_chars(station_name)
        names.extend(search_name.replace(",", " ").split(" "))
    names_set = set(names)
    # Names often contain short parts like Am, Zu, Im, Ch, An and also some one-letter abbreviations.
    # This destroys the filtering capability of name__contains below, which would produce to many
    # false positives.
    names_set_filtered = set(x for x in names_set if len(x) > 2)
    possible_admins = AdminArea.objects.filter(
        reduce(operator.or_, (Q(name__contains=x) for x in names_set_filtered))
    )
    possible_admins_names = list(possible_admins.values_list("name", flat=True))
    found_stations = dict()
    not_found_stations = set()

    for station_name in search_station_names:
        search_name = replace_german_chars(station_name)

        if use_filter:
            # filters stations, so that if multiple stations are found, they are only returned if
            # they are within the distance threshold to each other. In general a "single" busstation
            # consists of multiple bus stops, for example at different corners of the same crossing.
            f1 = partial(filter_query_distance, distance_threshold_m=DISTANCE_THRESHOLD_M)
        else:
            f1 = lambda x: x  # noqa
        query = search_station(
            search_name, possible_admins_names, filter_stack=f1, return_all=False
        )
        if query.exists():
            found_stations[search_name] = query
        else:
            not_found_stations.add(search_name)

    # Everything was found
    if not not_found_stations:
        return found_stations

    # Possibly more stations can be found when applying a project specific filter,
    # which searches for stations close to previously found stations
    if not use_filter or not found_stations:
        return found_stations

    # Some stations where not found repeat the process of searching for the station, but this time
    # leverage information about previously found stations. Its expected that stations form
    # clusters so stations are searched within some buffer zone of found stations.
    ids = [x for q in found_stations.values() for x in q.values_list("id", flat=True)]
    query = BusStation.objects.filter(pk__in=ids)
    if query.count() >= 3:
        convex_hull = get_convex_hull_from_query(query)
        max_y = max(abs(convex_hull.bounds[1]), convex_hull.bounds[3])
        max_x = max(abs(convex_hull.bounds[0]), convex_hull.bounds[2])
        min_x = min(abs(convex_hull.bounds[0]), convex_hull.bounds[2])
        if max_y > 80:
            logger.warning("Station lookup does not work properly at high latitudes>80.")
        if max_x - min_x > 180:
            # Convex hull does not work properly for "big" areas on a sphere.
            # Assumptions about distance would get violated
            logger.warning(
                "Station lookup does not work properly if stations cover more than "
                "half of the globe or are situated around +-180° longitude."
            )
        lat_lon_distance = approximate_lat_lon_distance(max_y)
        delta_lat_lon = BUS_SYSTEM_MAX_DISTANCE / lat_lon_distance
        area = convex_hull.buffer(delta_lat_lon)
    else:
        m_point = multi_point_from_query(query)
        max_y = max(abs(m_point.bounds[1]), m_point.bounds[3])
        lat_lon_distance = approximate_lat_lon_distance(max_y)
        delta_lat_lon = BUS_SYSTEM_MAX_DISTANCE / lat_lon_distance
        area = m_point.buffer(delta_lat_lon)
    # Create filter which only returns stations within the buffer area
    f1 = partial(filter_for_search_area, search_area=area)
    # only return multiple stations if they are close to each other.
    filter_inner_distance = partial(
        filter_query_distance, distance_threshold_m=DISTANCE_THRESHOLD_M
    )
    filter_stack = partial(reduce, lambda arg, f: f(arg), [f1, filter_inner_distance])

    still_not_found = set()
    # Use found stations to create a filter for the admin areas the stations are contained within
    ids = [x for q in found_stations.values() for x in q.values_list("id", flat=True)]
    found_station_query = BusStation.objects.filter(id__in=ids)
    fuzzy_filter = partial(
        get_all_bus_stations_of_admin_areas_in_query, found_stations=found_station_query
    )
    for station_name in not_found_stations:
        query = search_station(
            station_name,
            possible_admins_names,
            filter_stack=filter_stack,
            return_all=False,
        )
        if query.exists():
            found_stations[station_name] = query
            continue
        # last try with a fuzzy search in all stations
        # Filter entries based on trigram similarity
        start_query = BusStation.objects.all()
        search_query = fuzzy_filter(start_query)
        fuzzy_stations_in_hit_admin_areas = get_fuzzy_stations(
            search_query, SIMILARITY_THRESHOLD_W_ADMIN, station_name
        )
        if fuzzy_stations_in_hit_admin_areas.exists():
            query = filter_inner_distance(fuzzy_stations_in_hit_admin_areas)
            if query.exists():
                found_stations[station_name] = query
                continue
        else:
            still_not_found.add(station_name)
    newl = "\n"
    logger.info(
        f"Found {len(found_stations)} of {len(search_station_names)}. "
        f"The following station names could not be found "
        f"or were not specific enough to determine a single location, "
        f"even when filtering for an estimated system area."
        f"Not found Stations: \n"
        f"{newl.join(sorted(still_not_found))}"
    )

    return found_stations


def get_convex_hull_from_query(query):
    ids = list(query.values_list("id", flat=True))
    BLOCK_SIZE = 1000
    convex_hull = shapely.Polygon()
    while len(ids) > 0:
        pop_ids = ids[:BLOCK_SIZE]
        ids = ids[BLOCK_SIZE:]
        query = query.model.objects.filter(pk__in=pop_ids)
        convex_hull = multi_point_from_query(query).convex_hull
        convex_hull = convex_hull.union(convex_hull)
    return convex_hull
