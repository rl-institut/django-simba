from functools import partial
from functools import reduce
import json
import logging
import operator

import numpy as np
import shapely
from shapely.geometry import Point, MultiPoint
from typing import Callable, Iterable, List

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

DELIMITING_CHARACTERS = [" ", ",", ":", "/", "(", ")", "[", "]"]


def geom_distance(geom1, geom2):
    """Wrapper for geopy.distance to calculate distance.

    geopy.distance will calculate the distance between two points, expecting (lat,lon) coordinates,
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
    p1, p2 = rotating_caliper(xys)
    distance_m = geopy_distance((p1[1], p1[0]), (p2[1], p2[0])).meters
    if distance_m < distance_threshold_m:
        return query
    return query.model.objects.none()


def search_station(
    station_name: str,
    possible_admins_names: Iterable[str],
    filter_stack: Callable[[QuerySet], QuerySet],
    return_all: bool = False,
) -> QuerySet:
    """Return a QuerySet with matching BusStations.

    Searches for a given station name and returns a query with found BusStations.
    Several steps for searching a station name exists with increasing complexity.
    The flag return_all= True will return a query containing all found BusStations of all steps.
    return_all=False will return a QuerySet as soon as a step returned any BusStation.
    The filter_stack is a function which filters the QuerySet. The filter is applied in each step.
    possible_admins_names is an iterable of strings which are connected to administrative areas.
    The 2nd and 3rd search step remove these names from the searched name and restricts the search
    to these admin areas
    E.g. the station_name "Berlin, Alexanderplatz" might not exist in the Database.
    Passing "Berlin" in possible_admins_names removes "Berlin" from the search_name,
    e.g. "Alexanderplatz" is searched but the pool of BusStations is reduced to BusStations which
    are contained in an AdminArea named "Berlin"
    """
    # Search directly for the name, if the coordinates are close to each other
    # i.e. closer than DISTANCE_THRESHOLD, their ids are returned
    ids = []
    base_query = BusStation.objects.all()
    query = search_exact_station(base_query, station_name)
    query = filter_stack(query)
    if not return_all and query.exists():
        logger.debug(
            f"Found {query.count()} Stations for {station_name} searching with the exact name"
        )
        return query
    ids.extend(query.values_list("id", flat=True))

    before = len(ids)
    all_stations_count = BusStation.objects.all().count()
    logger.debug(f"Found {before} stations searching with the exact name")

    # no stations where found, or stations with exact name are further apart than
    # DISTANCE_THRESHOLD
    # Check if filtering by possible admin areas gives a clear result
    found_ids, names = get_station_ids_contained_by_admin_area(possible_admins_names, station_name)
    for name in names:
        logger.debug(f"Searching for {name}")
        base_query = BusStation.objects.filter(id__in=found_ids)
        query = search_exact_station(base_query, name)
        query = filter_stack(query)
        if not return_all and query.exists():
            logger.debug(
                f"Found {query.count()} Stations for {name=} searching in a pool of "
                f"{len(found_ids)}/{all_stations_count} Stations"
            )
            return query
        ids.extend(query.values_list("id", flat=True))

    logger.debug(
        f"Found {len(ids)-before} Stations for {station_name} searching in a pool of "
        f"{len(found_ids)}/{all_stations_count} Stations"
    )
    before = len(ids)

    # Search for stations if the search name contains an admin_name
    # Filter entries based on trigram similarity
    for name in names:
        base_query = BusStation.objects.filter(id__in=found_ids)
        query = get_fuzzy_stations(base_query, SIMILARITY_THRESHOLD_W_ADMIN, name)
        query = filter_stack(query)
        if not return_all and query.exists():
            logger.debug(
                f"Found {query.count()} Stations for {name=} searching fuzzily in a pool of "
                f"{len(found_ids)}/{all_stations_count} Stations.\n"
                "Similarity Threshold={SIMILARITY_THRESHOLD_W_ADMIN} "
            )
            return query
        ids.extend(query.values_list("id", flat=True))

    logger.debug(
        f"Found {len(ids)-before} Stations for {station_name} searching fuzzily in a pool of "
        f"{len(found_ids)}/{all_stations_count} Stations.\n Similarity Threshold={SIMILARITY_THRESHOLD_W_ADMIN} "
    )
    before = len(ids)

    # Ambiguous result: try again everywhere. This can be slow.
    # Possible optimization by indexing names or searching only unique names via a database view.
    # Querying a subset of ids with unique names does not work,
    # since large sets of ids like ID in [...] are slow, when mixed with trigram search.
    base_query = BusStation.objects.all()
    query = get_fuzzy_stations(
        base_query, SIMILARITY_THRESHOLD_WO_ADMIN, station_name, filter_best=(not return_all)
    )
    query = filter_stack(query)
    if not return_all and query.exists():
        logger.debug(
            f"Found {query.count()} Stations for {station_name} searching fuzzily in all Stations."
            f"\n Similarity Threshold={SIMILARITY_THRESHOLD_WO_ADMIN} "
        )

        # Log this since it might make sense to remove this part, if it rarely finds stations
        logger.info("Found a station via slow fuzzy search over all stations")
        return query
    ids.extend(query.values_list("id", flat=True))

    logger.debug(
        f"Found {len(ids)-before} Stations for {station_name} searching fuzzily in all Stations."
        f"\n Similarity Threshold={SIMILARITY_THRESHOLD_WO_ADMIN} "
    )
    # Resolve ids to query with a "simple" indexed query
    base_query = base_query.filter(id__in=ids)
    logger.debug(f"Found {base_query.count()} Stations in total.")
    return base_query


def search_exact_station(base_query, station_name) -> QuerySet:
    """Search if the exact name of the search query is found in the database.

    Since some parts of addresses can be ambiguous, e.g. "MainSt." and "MainStreet", some
    translations are used to check both instances.
    The translation Table Looks like this:
    [(St., Street),
    ...
    ]
    and is read from a file earlier.
    """
    address_translations = get_address_translations()
    address_translations_rev = [[x[1], x[0]] for x in address_translations]
    ids = []
    # Search for the name without changes
    exact_station_query = base_query.filter(name__iexact=station_name)
    ids.extend(exact_station_query.values_list("id", flat=True))

    # Apply some conversions of the name, e.g. "MainSt." becomes "MainStreet"
    for first, second in address_translations + address_translations_rev:
        if second in station_name:
            search_name = station_name.replace(second, first)
        else:
            continue
        exact_station_query = base_query.filter(name__iexact=search_name)
        ids.extend(exact_station_query.values_list("id", flat=True))
    return base_query.filter(id__in=ids)


def filter_for_search_area(query, search_area: shapely.geometry.base.BaseGeometry):
    ids = query.filter(geom__within=search_area).values_list("id", flat=True)
    query = query.model.objects.filter(id__in=ids)
    return query


def get_station_ids_contained_by_admin_area(
    possible_admins_names: Iterable[str], station_name: str
) -> tuple[List[int], List[str]]:
    """
    Return a list of BusStation ids that are contained in a found AdminArea inside the station_name.

    The station name is searched for a substring of the possible_admins_names.
    If a match is found, station ids which are inside this AdminArea are returned,
    as well as the name of the BusStation stripped of this indicator.
    Example:
    possible_admin_names= ["Prenzlauer Berg"}
    station_name = "(Prenzlauer Berg), Am Bahnhof"

    The station name is matched with the admin_name.
    BusStation ids inside "Prenzlauer Berg" are returned.


    :param possible_admins_names: Names of AdminAreas which are compared with the station_name
    :param station_name: Name, which is checked if it contains a name of an AdminArea
    :return:List of BusStation ids inside matched AdminAreas,
        the station name stripped of the AdminArea name
    """

    found_ids = []
    names = []
    for admin_name in possible_admins_names:
        if admin_name not in station_name:
            # The admin_name was not found in the station_name
            continue

        # Make sure the admin name is delimited from the rest of the station name in some way
        # Berliner Strasse should not match with the AdminArea "Berlin"
        if not is_delimited(station_name, admin_name):
            continue

        # AdminArea names are not unique, especially below level 9 (Gemeinden, Bezirke etc.)
        admin_areas = AdminArea.objects.filter(name__iexact=admin_name)
        all_children = get_lower_admin_areas(admin_areas)
        logger.debug(
            f"Found {admin_areas.count()} admin Areas with the name {admin_name} and the ids "
            f"{admin_areas.values_list('id', flat=True)}. These Areas contain "
            f"{all_children.count()} AdminAreas which are also searched."
        )
        name_without_admin = station_name.replace(admin_name, "")
        # Remove special characters which might have separated the admin area name from the actual BusStation name
        # e.g. "Mitte, Alexanderplatz" -> ", Alexanderplatz" should further be reduced to "Alexanderplatz"
        stripped_name = strip_delimiters(name_without_admin)
        names.append(stripped_name)
        found_ids.extend(
            BusStation.objects.filter(admin_area__in=all_children).values_list("id", flat=True)
        )
    return found_ids, names


def get_lower_admin_areas(admin_areas: QuerySet[AdminArea]) -> QuerySet[AdminArea]:
    """Get all children admin areas of the given admin areas.

    The admin_areas are resolved at a admin_level of at least 8 if possible.
    This behavior improves search results for inaccurate station names in real world data.
    """
    # Return the empty QuerySet if an empty Queryset was passed
    if not admin_areas.exists():
        return admin_areas

    parents = admin_areas.distinct()
    parent_ids = set()
    # Make sure all parents are at least level 8
    for parent in parents:
        while parent.admin_level >= 9 and parent.upper_admin_area is not None:
            parent = parent.upper_admin_area
        parent_ids.add(parent.id)
    all_children = [*(AdminArea.objects.filter(id__in=parent_ids))]
    children = AdminArea.objects.filter(upper_admin_area__in=all_children)
    while children.exists():
        all_children.extend(children)
        children = AdminArea.objects.filter(upper_admin_area__in=children)
    return AdminArea.objects.filter(id__in=set(x.id for x in all_children))


def get_fuzzy_stations(
    base_query: QuerySet, similarity_threshold, station_name, filter_best: bool = True
):
    fuzzy_stations = (
        base_query.annotate(similarity=TrigramSimilarity("name", station_name))
        .filter(similarity__gte=similarity_threshold)
        .order_by("-similarity")
    )
    if fuzzy_stations.exists() and filter_best:
        best_similarity = fuzzy_stations.first().similarity
        # Without delta lookup fails at times
        fuzzy_stations = fuzzy_stations.filter(similarity__gte=best_similarity - 0.01)
        similarities = fuzzy_stations.values_list("similarity", flat=True)
        logger.debug(
            "Found Stations have the following similaritis:\n" + "\n".join(map(str, similarities))
        )
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
    """Get the two points of convex points, which are furthest away

    https://en.wikipedia.org/wiki/Rotating_calipers
    https://codeforces.com/blog/entry/133763
    Convex hull must be given in clock or anti-clockwise fashion.
    Algorithm searches for all antipodals of the edges. The maximum distance is a distance between
    a point of the edge and an antipodal.

    :param xys:
    :return:
    """
    if len(xys) > 1 and xys[-1] == xys[0]:
        # remove duplicate point at start and end
        xys = [xy for xy in xys[:-1]]
    if len(xys) == 0:
        # No element
        return Point(0, 0), Point(0, 0)
    elif len(xys) == 1:
        # 1 element Maximum distance is zero with single point twice
        return xys[0], xys[0]
    elif len(xys) == 2:
        # 2 elements maximum_distance is their distance
        return xys[0], xys[1]

    # all points in a perfect line.
    first_edge = np.array(xys[1]) - np.array(xys[0])
    direction = None
    for i in range(1, len(xys)):
        next_edge = np.array(xys[(i + 1) % len(xys)]) - np.array(xys[i])
        next_direction = np.cross(first_edge, next_edge)
        if direction is None:
            direction = next_direction
            continue
        if next_direction != direction:
            break
    else:
        # All elements in a perfect line. the furthest points are the edges of the line
        xys_sorted = list(sorted(xys, key=lambda x: (x[0], x[1])))
        return xys_sorted[0], xys[-1]

    # Non-Trivial case
    # Find the the antipodals
    edge_antipodals = get_antipodals(xys)

    def edge_distance(edge, vertex):
        """Get the max. distance squared between an edge and a vertex.

        :param edge: iterable of two 2d-points building an edge
        :param vertex: tuple of a single 2d-point
        :return: maximum distance and point of edge with the maximum distance
        """

        dist1_squared = (edge[0][0] - vertex[0]) ** 2 + (edge[0][1] - vertex[1]) ** 2
        dist2_squared = (edge[1][0] - vertex[0]) ** 2 + (edge[1][1] - vertex[1]) ** 2
        if dist1_squared > dist2_squared:
            return dist1_squared, edge[0]
        return dist2_squared, edge[1]

    max_distance = 0
    furthest_points = None, None
    for point1, point2, antipodals in edge_antipodals:
        for antipodal in antipodals:
            dist_squared, point = edge_distance((point1, point2), antipodal)
            if dist_squared > max_distance:
                max_distance = dist_squared
                furthest_points = point, antipodal
    return furthest_points


def get_antipodals(xys):
    """Get all the antipodals for each edge.

     The anti podal is the node where the caliper would touch. In other words, a parallel line
     can be constructed. The condition for that is that the edge before and after have a switch
     in the cross-product with the original edge, with the special case when the cross-product is
     exactly 0, which means both edges are parallel

    :param xys: points of a convex hull
    :return: list of lists with both edge points and the antipodals
    """
    edge_antipodals = []
    # iterate over each edge
    for i in range(len(xys)):
        # handle last edge which goes back to index 0
        point2 = xys[(i + 1) % len(xys)]
        point1 = xys[i]
        if point1 == point2:
            # skip duplicates
            continue
        edge = np.array(point2) - np.array(point1)
        direction = None
        # Iterate over all the elements minus the current edge
        num_elements = (0, len(xys) - 1)
        first_antipodal = None
        if edge_antipodals:
            # first antipodal found in the last iteration
            prev_antipodal = edge_antipodals[-1][2][0]
            if prev_antipodal != (i + 1) % len(xys):
                # initiate the loop a single index before the last switch was found
                # handle negative values, by cycling back / using modulo
                first_node = (prev_antipodal - 2 - i) % len(xys)
                num_elements = (first_node, first_node + len(xys) - 1)
        direction_zero_at_start = True
        for ii in range(*num_elements):
            # handle cycling
            next_edge_i = (i + 1 + ii) % len(xys)
            next_edge = np.array(xys[(next_edge_i + 1) % len(xys)]) - np.array(xys[next_edge_i])
            if sum(next_edge) == 0:
                # skip elements with 0 length
                continue

            # initialize the direction with the first next edge
            # We only care about the sign
            direction = direction or np.sign(np.cross(edge, next_edge))
            if direction == 0 and direction_zero_at_start:
                # ignore elements which are parallel but next to the current edge.
                # we need to find a direction != 0 first before we care about parallel edges
                continue
            direction_zero_at_start = False
            new_direction = np.sign(np.cross(edge, next_edge))
            if new_direction == 0 and first_antipodal is None:
                first_antipodal = next_edge_i
                continue
            if new_direction == -direction:
                # Switch in directions found
                if first_antipodal is None:
                    first_antipodal = next_edge_i
                antipodals = [first_antipodal]
                i = int(first_antipodal)
                assert isinstance(next_edge_i, int)
                assert next_edge_i < len(xys)
                while i != next_edge_i:
                    i += 1
                    i = i % len(xys)
                    antipodals.append(i)

                edge_antipodals.append([point1, point2, antipodals])
                break
        else:
            raise AssertionError("Convex hull needs an antipodal for each edge")
    # replace indices with points
    for i, vals in enumerate(edge_antipodals):
        edge_antipodals[i][2] = [xys[ind] for ind in vals[2]]

    return edge_antipodals


def replace_german_chars(text: str) -> str:
    """Fix some german characters that get send when using the API directly from the browser window."""
    encoding_issues = [
        ("Ã¤", "ä"),
        ("Ã¼", "ü"),
        ("ã¶", "ö"),
        ("ãÿ", "ß"),
    ]
    for search, replace in encoding_issues:
        text = text.replace(search, replace)
    return text


def remove_chars(text: str, chars: Iterable[str]) -> str:
    for char in chars:
        text = text.replace(char, "")
    return text


def remove_delimiters(text: str) -> str:
    return remove_chars(text, DELIMITING_CHARACTERS)


def strip_chars(text: str, chars: Iterable[str]) -> str:
    len_before = float("inf")
    while len_before > len(text):
        len_before = len(text)
        for char in chars:
            text = text.strip(char)
    return text


def strip_delimiters(text: str) -> str:
    return strip_chars(text, DELIMITING_CHARACTERS)


def is_delimited(text: str, substring: str) -> bool:
    """Returns True if the substring is surrounded by the end of the string or a delimiter"""
    i = 0
    while i < len(text) - len(substring):
        ii = text.find(substring, i)
        if ii < 0:
            return False
        left_delimited = ii == 0 or text[ii - 1] in DELIMITING_CHARACTERS
        right_delimited = (
            ii + len(substring) == len(text) or text[ii + len(substring)] in DELIMITING_CHARACTERS
        )
        if left_delimited and right_delimited:
            return True
        i = ii + 1
    return False


def search_stations(search_station_names: Iterable[str], use_filter: bool):
    names = list()
    for station_name in search_station_names:
        search_name = replace_german_chars(station_name)
        # Split the station name respecting only whitespaces.
        # Delimiting characters are removed afterwards, e.g. Brackets
        search_name_list = [
            remove_delimiters(substring) for substring in search_name.replace(",", " ").split(" ")
        ]
        names.extend(search_name_list)
    names_set = set(names)
    # Names often contain short parts like Am, Zu, Im, Ch, An and also some one-letter abbreviations.
    # This hinders the filtering capability of name__contains below, which would produce too many
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
    # Some stations were not found.
    # Search these stations again, but this time, use information about previously found stations.
    # Stations usually form clusters, so stations are searched within some buffer zone of found stations.
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
        avg_y = (convex_hull.bounds[1] + convex_hull.bounds[3]) / 2
        lat_lon_distance = approximate_lat_lon_distance(avg_y)
        delta_lat_lon = BUS_SYSTEM_MAX_DISTANCE / lat_lon_distance
        area = convex_hull.buffer(delta_lat_lon)
    else:
        m_point = multi_point_from_query(query)
        avg_y = (m_point.bounds[1] + m_point.bounds[3]) / 2
        lat_lon_distance = approximate_lat_lon_distance(avg_y)
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
            search_query, SIMILARITY_THRESHOLD_W_ADMIN, station_name, filter_best=True
        )
        if fuzzy_stations_in_hit_admin_areas.exists():
            query = filter_inner_distance(fuzzy_stations_in_hit_admin_areas)
            if query.exists():
                found_stations[station_name] = query
                continue
        else:
            still_not_found.add(station_name)
    logger.info(f"Found {len(found_stations)} of {len(search_station_names)}. ")
    if still_not_found:
        logger.info(
            "The following station names could not be found "
            "or were not specific enough to determine a single location, "
            "even when filtering for an estimated system area.\n"
            "Not found Stations: \n"
            "\n".join(sorted(still_not_found))
        )
    return found_stations


def get_convex_hull_from_query(query):
    """Return a convex hull from a query with a geom field

    Uses BLOCK_SIZE to create a convex hull in batches.
    This is faster than creating a convex hull in one step.
    """
    ids = list(query.values_list("id", flat=True))
    BLOCK_SIZE = 1000
    convex_hull = shapely.Polygon()
    while ids:
        pop_ids = ids[:BLOCK_SIZE]
        ids = ids[BLOCK_SIZE:]
        query = query.model.objects.filter(pk__in=pop_ids)
        convex_hull = multi_point_from_query(query).convex_hull
        convex_hull = convex_hull.union(convex_hull)
    return convex_hull
