import logging
from datetime import datetime
import io
import pandas as pd
import requests
import traceback
import time
import zipfile

from django.contrib.gis.db import models
from django.contrib.gis.geos import Point
from django.db.transaction import atomic
from django.utils.timezone import make_aware

from ebustoolbox.util import get_next_id

logger = logging.getLogger("custom")
OVERPASS_URL = "http://overpass-api.de/api/interpreter"

# Offset needed to translate OpenStreetMap Element IDs from Overpass_API
# https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#By_element_id
OFFSET_CONST = 3600000000


# pylint: disable=W0223
class X(models.functions.Func):
    function = "ST_X"


# pylint: disable=W0223
class Y(models.functions.Func):
    function = "ST_Y"


class Z(models.functions.Func):
    function = "ST_Z"


# https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#By_element_id
class AdminArea(models.Model):
    name = models.CharField(max_length=100)
    admin_level = models.IntegerField(default=4)
    osm_id = models.BigIntegerField(unique=True)
    last_check = models.DateTimeField(null=True)
    upper_admin_area = models.ForeignKey("self", on_delete=models.CASCADE, null=True)


class BusStation(models.Model):
    name = models.CharField(max_length=100)
    osm_id = models.BigIntegerField(unique=True)
    geom = models.PointField(dim=3, srid=4326, null=True)  # without z elevation
    admin_area = models.ForeignKey(AdminArea, on_delete=models.CASCADE)


def get_german_states():
    overpass_query = """
    [out: json];
    area["ISO3166-1" = "DE"][admin_level = 2];
    rel[admin_level = 4][type = boundary]["ISO3166-2"~"DE"](area);
    out geom;
    """
    response = requests.get(OVERPASS_URL, params={"data": overpass_query})
    data = response.json()
    state_bounds = {elem["id"]: elem["tags"] for elem in data["elements"]}
    # https: // wiki.openstreetmap.org / wiki / Overpass_API / Overpass_QL  # By_element_id
    return state_bounds


def get_admin_areas_recursive(
    pk: int,
    admin_level: int,
    area: str = "area['ISO3166-1' = 'DE'][admin_level = 2]",
    upper_admin_area: AdminArea = None,
    osm_id_dict: dict = None,
    completed_searched_osm_ids: set = None,
) -> tuple[list[AdminArea], int]:
    """Return list of Germanys AdminAreas and the next unused primary key

    Recursive search of Germany and its AdminAreas using Overpass API.
    Starting with Germany as a whole as search area, lower AdminAreas (e.g. States) are searched up
    to an admin level of 9 which corresponds with Gemeinden/Bezirken.
    AdminAreas exists in hierarchy but not necessarily without gaps.
    I.e. an AdminArea of level 8 might be part of/be contained by an AdminArea of level 4,
    without being part of an AdminArea of level 6. This means every AdminArea needs to be queried
    for all AdminLevels below its own and not just the next lower level.
    :param pk: next unused primary key
    :param admin_level: admin_level which is searched
    :param area: area within admin_areas are searched in overpass area str format
    :param upper_admin_area (AdminArea): the parent AdminArea of the current search
    :param osm_id_dict: osm_ids already in the database with AdminArea as value
    :param completed_searched_osm_ids: the current recursive search completely searched these
    osm_ids to the lowest level
    :return: list of AdminAreas, next unused primary key of admin areas

    """

    admin_levels = [4, 6, 8, 9]
    suffix = ""
    if admin_level == 4:
        # This is needed since Overpass would retrieve States from other Countries like Switzerland
        # or France which in some way Overlap with Germany
        suffix = """["ISO3166-2"~"^DE"]"""
    overpass_query = f"""
    [out: json];
    {area};
    rel[admin_level = {admin_level}][boundary=administrative][type = boundary]{suffix}(area);
    out tags;
    """
    response = get_or_sleep(overpass_query)
    admin_areas = []
    if response.status_code != 200:
        logger.error(f"Error for {overpass_query} \n with {response.status_code=}")
        return admin_areas, pk
    overpass_json = response.json()

    # These are Ids which have already been searched in this run of the recursive search
    # and do not have to be searched again
    completed_searched_osm_ids = completed_searched_osm_ids or set()
    if osm_id_dict is None:
        osm_id_dict = {x: None for x in set(AdminArea.objects.values_list("osm_id", flat=True))}

    for elem in overpass_json["elements"]:
        # the query returned a list of AdminAreas inside the current AdminArea with the specified
        # admin level. Iterate over this list and add AdminAreas which are not part of the DB yet
        osm_id = elem["id"]
        if osm_id in completed_searched_osm_ids:
            # this id was already recursively searched and can be skipped
            continue
        if osm_id not in osm_id_dict:
            # an osm id was found which is not part of the database yet
            name = elem["tags"].get("name")
            try:
                logger.debug(f"{admin_level=}, {name=}")
                admin_area = AdminArea(
                    id=pk,
                    name=name,
                    osm_id=osm_id,
                    admin_level=admin_level,
                    upper_admin_area=upper_admin_area,
                )
                admin_areas.append(admin_area)
                osm_id_dict[osm_id] = admin_area
                pk += 1
            except:  # noqa
                #
                print("error")
                logger.warning(f"{admin_level=}, {name=}")
                traceback.print_exc()
                continue
        else:
            # The osm_id was already found in the database or is in memory to be commited later
            try:
                admin_area = AdminArea.objects.get(osm_id=osm_id)
            except AdminArea.DoesNotExist:
                # this admin area was created earlier but not yet commited to the db.
                admin_area = osm_id_dict[osm_id]

        # Search the current AdminArea multiple times for child AdminAreas.
        # First for cities(admin_level=6) inside the state(admin_level=4).
        # Then for Gemeinden/Kreise(admin_level=8) and then for Bezirke (admin_level=9)
        # In between each found element is searched in a similar fashion.

        # this is done to find all possible relations in the hierarchy of AdminAreas, e.g.
        # Berlin with level=4 is the direct parent of Friedrichshain-Kreuzberg of level=9
        if admin_level < max(admin_levels):
            # if admin_level == min(admin_levels):
            if admin_level < 7:
                logger.info(f"Searching recursively in {admin_area.name}")
            next_levels = admin_levels[admin_levels.index(admin_level) + 1 :]
            for next_admin_level in next_levels:
                # Search for AdminAreas with higher admin levels inside the current one.
                inside_admin_areas, pk = get_admin_areas_recursive(
                    pk,
                    next_admin_level,
                    f"area({elem['id'] + OFFSET_CONST})",
                    upper_admin_area=admin_area,
                    osm_id_dict=osm_id_dict,
                    completed_searched_osm_ids=completed_searched_osm_ids,
                )
                admin_areas.extend(inside_admin_areas)
            # This admin area was completely searched.
            # Add it to the set to skip searching it multiple times.
            completed_searched_osm_ids.add(admin_area.osm_id)
    return admin_areas, pk


def get_or_sleep(overpass_query) -> requests.Response:
    retry = True
    while retry:
        response = requests.get(OVERPASS_URL, params={"data": overpass_query})
        if response.status_code == 429:
            # Rate limited
            sleep_duration = 120
            logger.info(f"Getting rate limited by overpass_api. Waiting {sleep_duration}s.")
            time.sleep(sleep_duration)
        else:
            retry = False
    return response


def search_in_area_id(area_id, search_query) -> dict | None:
    """
    Executes a query on the Overpass API within a specific area.

    This function constructs and sends a query to the Overpass API using the given `area_id`
    and `search_query`. It handles cases where the `area_id` is too small by adding an
    offset constant. Additionally, it retries the request if rate-limited by the API using
    the get_or_sleep function.

    :param area_id (int): The numeric identifier for the area to search in. If smaller than
                       `OFFSET_CONST`, the offset is automatically added.
    :param search_query (str): The Overpass QL query to execute within the specified area.
    :return: response.json() if status_code is 200, or None if status is any other than ok or rate limited.
    """

    if area_id < OFFSET_CONST:
        logger.warning(
            f"Warning: area id is too small. {OFFSET_CONST} is added automatically."
            " See https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#By_element_id"
        )
        area_id += OFFSET_CONST
    overpass_query = f"""
    [out: json];
    area({area_id});
    {search_query}(area);
    out tags;
    """
    response = get_or_sleep(overpass_query)
    if response.status_code == 200:
        return response.json()
    logger.warning(f"{search_query}  resulted in the following response:\n {response.status_code}")
    return None


def fill_db_with_bus_stations() -> None:
    """
    Use overpass API to search Germany for BusStations and store them in the database.

    Queries overpass for admin areas, which are then searched for BusStations. This allows
    differentiating between BusStations with identical names by using the AdminArea they are located
    in, e.g. the BusStation "MainStreet" might exist multiple times in different cities.
    Storing this relation between Station and AdminArea allows for queries of a BusStation name but
    only in specified AdminAreas (e.g. a specific city or district)
    :return:
    """
    pk = get_next_id(AdminArea)
    admin_areas, _ = get_admin_areas_recursive(
        pk, 4, area="area['ISO3166-1' = 'DE'][admin_level = 2]", upper_admin_area=None
    )
    try:
        AdminArea.objects.bulk_create(admin_areas)
    except:  # noqa
        if admin_areas:
            df = model_list_to_df(admin_areas)

            df.to_csv("admin_areas_dump.csv", index=False)
    # This is slow, since many requests are fired, but it works. Only needs to be run once.
    # A faster way could be to get all geographic info from above including the boundary shapes.
    # Request all stations at once and then filter them into the right administrations.
    assert (
        BusStation.objects.exists() is False
    ), "Filling db with stations is only supported for an empty Table"

    osm_id_set = set(BusStation.objects.all().values_list("osm_id", flat=True))
    failed_id_set = set()
    for level in [9, 8, 6, 4]:
        for admin_area in AdminArea.objects.filter(admin_level=level):
            logger.info(f"Searching bus stations in {admin_area.name}")
            search_query = "node['highway'='bus_stop']"
            response_json = search_in_area_id(admin_area.osm_id + OFFSET_CONST, search_query)
            if response_json is None:
                # Some error occurred. Continue with other Stations.
                continue
            logger.info(f"Found {len(response_json['elements'])} bus stations")
            first_id = get_next_id(BusStation)
            bus_stations = []
            for element in response_json["elements"]:
                osm_id = element["id"]
                if osm_id in osm_id_set or osm_id in failed_id_set:
                    continue
                # If the BusStation does not exist yet, a new one is created and added to a list
                # for bulk creation
                try:
                    busstation = BusStation(
                        id=first_id,
                        name=element.get("tags", {}).get("name", "NoName"),
                        osm_id=osm_id,
                        geom=Point(x=element["lon"], y=element["lat"], z=0),
                        admin_area=admin_area,
                    )
                    osm_id_set.add(osm_id)
                except Exception:
                    failed_id_set.add(osm_id)
                    traceback.print_exc()
                    continue
                bus_stations.append(busstation)
                first_id += 1
            try:
                BusStation.objects.bulk_create(bus_stations)
            except Exception:
                # For Debugging
                if bus_stations:
                    df = model_list_to_df(bus_stations)
                    df.to_csv("bus_station_dump.csv", index=False)
                traceback.print_exc()
            admin_area.last_check = make_aware(datetime.now())
            admin_area.save()


def model_list_to_df(model_list):
    model_type = type(model_list[0])
    fields = [x.column for x in model_type._meta.fields]
    data = list(map(lambda x: {field: getattr(x, field) for field in fields}, model_list))
    return pd.DataFrame(data)


def get_admin_areas_df():
    admin_areas = AdminArea.objects.all()
    columns = ["id", "name", "osm_id", "admin_level", "upper_admin_area"]
    data = admin_areas.values_list(*columns)
    df = pd.DataFrame(columns=columns, data=data)
    return df


def get_bus_stations_df():
    bus_stations = BusStation.objects.all()
    columns = ["id", "name", "osm_id", "geom_x", "geom_y", "geom_z", "admin_area"]
    data = bus_stations.annotate(
        geom_x=X("geom", output_field=models.DecimalField()),
        geom_y=Y("geom", output_field=models.DecimalField()),
        geom_z=Z("geom", output_field=models.DecimalField()),
    ).values_list(*columns)
    df = pd.DataFrame(columns=columns, data=data)
    # cast geometry columns to float
    df.loc[:, ["geom_x", "geom_y", "geom_z"]] = df.loc[:, ["geom_x", "geom_y", "geom_z"]].astype(
        float
    )
    return df


@atomic()
def import_data(df_areas, df_stations):
    df = df_areas
    admin_areas = []
    for row in df.itertuples():
        try:
            upper_admin_area = int(row.upper_admin_area)
        except TypeError:
            upper_admin_area = None
        admin_area = AdminArea(
            id=row.id,
            name=row.name,
            osm_id=row.osm_id,
            admin_level=row.admin_level,
            upper_admin_area=upper_admin_area,
        )
        admin_areas.append(admin_area)
    AdminArea.objects.bulk_create(admin_areas)

    df = df_stations
    bus_stations = []
    for row in df.itertuples():
        bus_station = BusStation(
            id=row.id,
            name=row.name,
            osm_id=row.osm_id,
            geom=Point(row.geom_x, row.geom_y, row.geom_z),
            admin_area=row.admin_area,
        )
        bus_stations.append(bus_station)
    BusStation.objects.bulk_create(bus_stations)


def create_export_buffer():
    """
    Write admin areas and busstations as .csv to a buffer of a deflated zip.
    """
    df_areas = get_admin_areas_df()
    df_stations = get_bus_stations_df()
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write the dataframe to a buffer. use this buffer to write zo a deflated zip
        # Write dataframe to buffer and then to deflated zip.
        csv_buffer = io.StringIO()
        df_areas.to_csv(csv_buffer, index=False)
        zf.writestr("admin_areas.csv", csv_buffer.getvalue())

        csv_buffer = io.StringIO()
        df_stations.to_csv(csv_buffer, index=False)
        zf.writestr("bus_stations.csv", csv_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer
