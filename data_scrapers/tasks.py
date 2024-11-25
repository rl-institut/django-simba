import json
import logging
import math
from functools import reduce
from functools import partial
import operator
import shapely
from shapely.geometry import Point, MultiPoint
from typing import Callable, Iterable

from .models import BusStation, AdminArea

from django.db.models import QuerySet
from geopy.distance import distance as geopy_distance
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

logger = logging.getLogger("custom")
BUS_SYSTEM_MAX_DISTANCE = 10  # km
DISTANCE_THRESHOLD_M = 400  # m

# For Fuzzy Search
SIMILARITY_THRESHOLD_W_ADMIN = 0.5  # Adjust this threshold as needed
SIMILARITY_THRESHOLD_WO_ADMIN = 0.6  # Adjust this threshold as needed

logger = logging.getLogger("custom")


def geom_distance(geom1, geom2):
    """Wrapper for geopy.distance to calculate distance.

    geopy.distance will calculate the distance between two points, expecting (lat,lon)
    coordinates while geom are converted to tuples as (lon, lat, z)
    """
    return geopy_distance((geom1.y, geom1.x), (geom2.y, geom2.x))


def get_upper_bound_distance(station_query: QuerySet):
    # give a simple upper bound for distance between points
    xs = [station.geom.x for station in station_query]
    ys = [station.geom.y for station in station_query]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    geo_distance = geopy_distance((y_min, x_min), (y_max, x_max))
    return geo_distance


def filter_query_distance(query: QuerySet, distance_threshold_m):
    """Filter a object query by finding the max"""
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

    # try again in the admin areas but with a fuzzy search
    # last try with a fuzzy search in all stations
    # Filter entries based on trigram similarity
    found_ids, names = get_station_ids_contained_by_admin_area(possible_admins_names, station_name)
    for name in names:
        base_query = BusStation.objects.filter(id__in=found_ids)
        query = get_fuzzy_stations(base_query, SIMILARITY_THRESHOLD_W_ADMIN, name)
        query = filter_stack(query)
        if not return_all and query.exists():
            return query
        ids.extend(query.values_list("id", flat=True))
    return base_query.filter(id__in=ids)


def search_exact_station(base_query, station_name) -> QuerySet:
    address_translations = get_address_translations()
    address_translations_rev = [[x[1], x[0]] for x in address_translations]
    ids = []
    for first, second in address_translations + address_translations_rev:
        search_name = station_name
        if first is not None:
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
    for part in station_name.split(" "):
        if part in possible_admins_names:
            admin_name = part
            # Admin Area Names are not unique, especially at <= level 9
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
    while children.count() > 0:
        all_children.extend(children)
        children = AdminArea.objects.filter(upper_admin_area__in=children)
    return AdminArea.objects.filter(id__in=set(x.id for x in all_children))


def get_fuzzy_stations(start_query: QuerySet, similarity_threshold, station_name):
    fuzzy_stations = (
        start_query.annotate(similarity=TrigramSimilarity("name", station_name))
        .filter(similarity__gte=similarity_threshold)
        .order_by("-similarity")
    )
    fuzz_station_query_w_admin_ids = list(fuzzy_stations.values_list("id", flat=True))
    fuzzy_stations = BusStation.objects.filter(id__in=fuzz_station_query_w_admin_ids)
    return fuzzy_stations


def get_address_translations():
    from ebusdjango import util

    p = util.get_static_file_path(__package__, "address_translations.json")
    with open(p, "r") as f:
        translations = json.load(f)
    # Add None values to check address without translation
    translations.insert(0, [None, None])
    return translations


def approximate_lat_lon_distance(max_y):
    # Approximate a distance as delta in lat/long coordinates
    lat_distance = geopy_distance((max_y, 0), (max_y + 1, 0))
    lon_distance = geopy_distance((max_y, 0), (max_y, +1))
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
    ii = 0
    if len(xys) == 0:
        return 0, None, None
    if len(xys) == 1:
        return 0, xys[0], xys[0]

    def edge_distance(edge, vertex):
        dist1_squared = math.pow(edge[0][0] - vertex[0], 2) + math.pow(edge[0][1] - vertex[1], 2)
        dist2_squared = math.pow(edge[1][0] - vertex[0], 2) + math.pow(edge[1][1] - vertex[1], 2)
        if dist1_squared > dist2_squared:
            return dist1_squared, edge[0]
        return dist1_squared, edge[1]

    point1 = xys[0]
    point2 = xys[1]
    max_distance = math.pow(point1[0] - point2[0], 2) + math.pow(point1[1] - point2[1], 2)
    for i in range(len(xys) - 1):
        edge = (xys[i], xys[i + 1])
        ii += 1
        cur_edge_distance, edge_point = edge_distance(edge, xys[ii])
        while cur_edge_distance > max_distance:
            point1 = edge_point
            point2 = xys[ii]
            max_distance = cur_edge_distance
            ii += 1
            cur_edge_distance, edge_point = edge_distance(edge, xys[ii])

        ii -= 1
        cur_edge_distance, edge_point = edge_distance(edge, xys[ii])
        while cur_edge_distance > max_distance:
            point1 = edge_point
            point2 = xys[ii]
            max_distance = cur_edge_distance
            ii -= 1
            cur_edge_distance, edge_point = edge_distance(edge, xys[ii])
    return max_distance, point1, point2


def search_stations(search_station_names: Iterable, use_filter: bool):
    names = list()
    for station_name in search_station_names:
        names.extend(station_name.split(" "))
    names_set = set(names)
    names_set_filtered = set(x for x in names_set if len(x) > 2)
    possible_admins = AdminArea.objects.filter(
        reduce(operator.or_, (Q(name__contains=x) for x in names_set_filtered))
    )
    possible_admins_names = list(possible_admins.values_list("name", flat=True))
    found_stations = dict()
    not_found_stations = set()
    for station_name in search_station_names:
        station_name = (
            station_name.replace("Ã¤", "ä").replace("Ã¼", "ü").replace("Ã¶", "ö").replace("ÃŸ", "ß")
        )
        if use_filter:
            f1 = partial(filter_query_distance, distance_threshold_m=DISTANCE_THRESHOLD_M)
        else:
            f1 = lambda x: x  # noqa
        query = search_station(
            station_name, possible_admins_names, filter_stack=f1, return_all=False
        )
        if query.exists():
            found_stations[station_name] = query
        else:
            not_found_stations.add(station_name)

    # Everything was found
    if not not_found_stations:
        return found_stations

    # Possibly more stations can be found when applying a project specific filter,
    # which searches for stations close to previously found stations
    if not use_filter:
        return found_stations

    # Some stations where not found repeat the
    ids = [x for q in found_stations.values() for x in q.values_list("id", flat=True)]
    query = BusStation.objects.filter(pk__in=ids)

    convex_hull = get_convex_hull_from_query(query)
    max_y = max(abs(convex_hull.bounds[1]), convex_hull.bounds[3])
    if max_y > 80:
        logger.warning("Station lookup does not work properly at high latitudes>80.")
    lat_lon_distance = approximate_lat_lon_distance(max_y)
    delta_lat_lon = BUS_SYSTEM_MAX_DISTANCE / lat_lon_distance
    area = convex_hull.buffer(delta_lat_lon)
    f1 = partial(filter_for_search_area, search_area=area)
    filter_inner_distance = partial(
        filter_query_distance, distance_threshold_m=DISTANCE_THRESHOLD_M
    )
    filter_stack = partial(reduce, lambda arg, f: f(arg), [f1, filter_inner_distance])

    still_not_found = set()
    # Use the found stations to create a filter for the admin areas the stations are
    # contained within
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
        if query.count() > 0:
            found_stations[station_name] = query
            continue
        # last try with a fuzzy search in all stations
        # Filter entries based on trigram similarity
        start_query = BusStation.objects.all()
        search_query = fuzzy_filter(start_query)
        fuzzy_stations_in_hit_admin_areas = get_fuzzy_stations(
            search_query, SIMILARITY_THRESHOLD_WO_ADMIN, station_name
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
