import io
import logging
import pandas as pd
import zipfile

from django.contrib.gis.geos import Point
from django.db.transaction import atomic
from django.contrib.gis.db import models

from data_scrapers.models import AdminArea, BusStation

logger = logging.getLogger("custom")


# pylint: disable=W0223
class X(models.functions.Func):
    function = "ST_X"


# pylint: disable=W0223
class Y(models.functions.Func):
    function = "ST_Y"


class Z(models.functions.Func):
    function = "ST_Z"


def model_list_to_df(model_list: list[models.Model]) -> pd.DataFrame:
    """Return a dataframe for a list of instances of the same Model.

    The dataframe contains data of the model fields.
    """
    model_type = type(model_list[0])
    fields = [x.column for x in model_type._meta.fields]
    data = list(map(lambda x: {field: getattr(x, field) for field in fields}, model_list))
    return pd.DataFrame(data)


def get_admin_areas_df():
    """Return a dataframe for all AdminAreas of the Database"""
    admin_areas = AdminArea.objects.all()
    columns = ["id", "name", "osm_id", "admin_level", "upper_admin_area"]
    data = admin_areas.values_list(*columns)
    df = pd.DataFrame(columns=columns, data=data)
    return df


def get_bus_stations_df():
    """Return a dataframe for all BusStations of the Database"""
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
def import_data(df_areas: pd.DataFrame, df_stations: pd.DataFrame):
    """Import DataFrames of AdminAreas and BusStations into the database"""
    df = df_areas
    admin_areas = []
    missing_osm_id = df.osm_id.isna()
    if missing_osm_id:
        logger.info(f"{sum(missing_osm_id)} AdminAreas have no OSM ID and are not imported")
    # Remove areas without an osm_id
    ids_with_no_osm_id = df[missing_osm_id].index
    df = df.loc[~missing_osm_id, :]
    for row in df.itertuples():
        try:
            upper_admin_area = int(row.upper_admin_area)
        except (TypeError, ValueError):
            upper_admin_area = None
        admin_area = AdminArea(
            id=row.id,
            name=row.name,
            osm_id=row.osm_id,
            admin_level=row.admin_level,
            upper_admin_area_id=upper_admin_area,
        )
        admin_areas.append(admin_area)

    AdminArea.objects.bulk_create(admin_areas)
    logger.info(f"{len(admin_areas)} AdminAreas were created.")
    df = df_stations
    bus_stations = []
    missing_admin_area_stations = df.admin_area.isna()
    if missing_admin_area_stations:
        logger.info(
            f"{len(missing_admin_area_stations)} Stations have no admin area and are not imported"
        )
    # Remove stations with no associated admin_area
    df = df[~missing_admin_area_stations]
    for row in df.itertuples():
        if row.admin_area in ids_with_no_osm_id:
            continue
        bus_station = BusStation(
            id=row.id,
            name=row.name,
            osm_id=row.osm_id,
            geom=Point(row.geom_x, row.geom_y, row.geom_z),
            admin_area_id=row.admin_area,
        )
        bus_stations.append(bus_station)
    BusStation.objects.bulk_create(bus_stations)
    logger.info(f"{len(bus_stations)} BusStations were created.")


def create_export_buffer():
    """
    Write admin areas and busstations as .csv to a buffer of a deflated zip.
    """
    df_areas = get_admin_areas_df()
    df_stations = get_bus_stations_df()
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write dataframe to buffer and then to deflated zip.
        csv_buffer = io.StringIO()
        df_areas.to_csv(csv_buffer, index=False)
        zf.writestr("admin_areas.csv", csv_buffer.getvalue())

        csv_buffer = io.StringIO()
        df_stations.to_csv(csv_buffer, index=False)
        zf.writestr("bus_stations.csv", csv_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer
