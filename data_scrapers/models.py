import traceback
from datetime import datetime
import pandas as pd
from django.contrib.gis.geos import Point
from django.contrib.gis.db import models
import requests
from django.utils.timezone import make_aware

from ebustoolbox.util import get_next_id

OVERPASS_URL = "http://overpass-api.de/api/interpreter"
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
    state_bounds = {ele["id"]: ele["tags"] for ele in data["elements"]}
    # https: // wiki.openstreetmap.org / wiki / Overpass_API / Overpass_QL  # By_element_id
    return state_bounds


def get_admin_areas_recursive(
    pk,
    admin_level,
    area="area['ISO3166-1' = 'DE'][admin_level = 2]",
    upper_admin_area=None,
    osm_id_set=None,
):
    admin_levels = [4, 6, 8, 9]
    suffix = ""
    if admin_level == 4:
        suffix = """["ISO3166-2"~"^DE"]"""
    overpass_query = f"""
    [out: json];
    {area};
    rel[admin_level = {admin_level}][boundary=administrative][type = boundary]{suffix}(area);
    out tags;
    """
    response = requests.get(OVERPASS_URL, params={"data": overpass_query})
    if response.status_code != 200:
        print("Error for ", overpass_query)
    data2 = response.json()
    admin_areas = []
    if osm_id_set is None:
        osm_id_set = set(AdminArea.objects.values_list("osm_id", flat=True))
    for ele in data2["elements"]:
        osm_id = ele["id"]
        if osm_id not in osm_id_set:
            name = ele["tags"].get("name")
            try:
                print(admin_level, name)
                admin_area = AdminArea(
                    id=pk,
                    name=name,
                    osm_id=osm_id,
                    admin_level=admin_level,
                    upper_admin_area=upper_admin_area,
                )
                admin_areas.append(admin_area)
                osm_id_set.add(ele["id"])
                pk += 1
            except:  # noqa
                traceback.print_exc()
                continue
        else:
            admin_area = AdminArea.objects.get(osm_id=osm_id)

        # Search twice, First for Cities inside the state which might have kreise as well
        # after that search again for kreise inside the state. This will consist of many duplicates
        # but also some kreise (admin_level=8) which do not have a (admin_level=6)
        if admin_level < max(admin_levels):
            next_admin_level = admin_levels[admin_levels.index(admin_level) + 1]
            inside_admin_areas, pk = get_admin_areas_recursive(
                pk,
                next_admin_level,
                f"area({ele['id'] + OFFSET_CONST})",
                upper_admin_area=admin_area,
                osm_id_set=osm_id_set,
            )
            admin_areas.extend(inside_admin_areas)
    return admin_areas, pk


def search_in_area_id(area_id, search_query):
    if area_id < OFFSET_CONST:
        print(
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
    response = requests.get(OVERPASS_URL, params={"data": overpass_query})
    data = response.json()
    if response.status_code != 200:
        print(response.status_code)
    return data, response


def fill_db_with_bus_stations():
    # AdminArea.objects.all().delete()
    pk = get_next_id(AdminArea)
    admin_areas, _ = get_admin_areas_recursive(
        pk, 4, area="area['ISO3166-1' = 'DE'][admin_level = 2]", upper_admin_area=None
    )
    AdminArea.objects.bulk_create(admin_areas)

    # this is slow since a lot of requests are fired, but it gets the job done. Only needs to be run
    # once. A faster way could be to get all geographic info from above including the boundary shapes
    # request all stations at once and then filter them into the right administrations.
    osm_id_set = set(BusStation.objects.all().values_list("osm_id", flat=True))
    update_stations = []
    for level in [9, 8, 6, 4]:
        for admin_area in AdminArea.objects.filter(admin_level=level):
            print(f"Searching bus stations in {admin_area.name}")
            search_query = """node["highway"="bus_stop"]"""
            data, response = search_in_area_id(admin_area.osm_id + OFFSET_CONST, search_query)
            if response.status_code != 200:
                print(
                    f"{search_query}  resulted in the following response: \n {response.status_code}"
                )
                continue
            print(f"Found {len(data['elements'])} bus stations")
            first_id = get_next_id(BusStation)
            bus_stations = []
            for elem in data["elements"]:
                osm_id = elem["id"]
                if osm_id in osm_id_set:
                    busstation = BusStation.objects.get(osm_id=osm_id)
                    busstation.admin_area = admin_area
                    update_stations.append(busstation)
                    continue
                osm_id_set.add(osm_id)
                try:
                    busstation = BusStation(
                        id=first_id,
                        name=elem.get("tags", {}).get("name", "NoName"),
                        osm_id=osm_id,
                        geom=Point(x=elem["lon"], y=elem["lat"], z=0),
                        admin_area=admin_area,
                    )
                except Exception:
                    traceback.print_exc()
                    continue
                bus_stations.append(busstation)
                first_id += 1
            try:
                BusStation.objects.bulk_create(bus_stations)
            except Exception:
                traceback.print_exc()
            BusStation.objects.bulk_update(update_stations, fields=["admin_area"])
            admin_area.last_check = make_aware(datetime.now())
            admin_area.save()


def export_admin_areas(path, encoding="utf-8"):
    admin_areas = AdminArea.objects.all()
    columns = ["id", "name", "osm_id", "admin_level", "upper_admin_area"]
    data = admin_areas.values_list(*columns)
    df = pd.DataFrame(columns=columns, data=data)
    df.to_csv(path, index=False, encoding=encoding)


def export_bus_stations(path, encoding="utf-8"):
    bus_stations = BusStation.objects.all()
    columns = ["id", "name", "osm_id", "geom_x", "geom_y", "geom_z", "admin_area"]
    data = bus_stations.annotate(
        geom_x=X("geom", output_field=models.DecimalField()),
        geom_y=Y("geom", output_field=models.DecimalField()),
        geom_z=Z("geom", output_field=models.DecimalField()),
    ).values_list(*columns)
    df = pd.DataFrame(columns=columns, data=data)
    df.loc[:, ["geom_x", "geom_y", "geom_z"]] = df.loc[:, ["geom_x", "geom_y", "geom_z"]].astype(
        float
    )
    df.to_csv(path, index=False, encoding=encoding)


def import_admin_data(admin_data_path, bus_stations_path):
    df = pd.read_csv(admin_data_path)
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

    df = pd.read_csv(bus_stations_path)
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
