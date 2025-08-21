import csv
import logging

from typing import List
from django.db.models import QuerySet
from django.db.transaction import atomic
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.contrib.gis.db.models.functions import Distance
from django.utils.timezone import make_aware
import datetime
import zipfile
from io import BytesIO, StringIO
import re
import traceback
from django.http import HttpResponse
from .models import WeatherStation, WeatherData

MIN_DATE = datetime.datetime(2013, 1, 1)
logger = logging.getLogger("custom")


def import_data(uploaded_file: InMemoryUploadedFile):
    station_file = "TU_Stundenwerte_Beschreibung_Stationen.txt"
    zip_pattern = r"stundenwerte_TU_[0-9]{5}_.*\.zip"
    file_pattern = "produkt_tu_stunde_.*_([0-9]{5}).txt"
    zip_file = zipfile.ZipFile(BytesIO(uploaded_file.read()))
    # The file with station names is formatted by fixed charnumber per column.
    # For each column the columns need to be defined for proper parsing
    station_id = 0, 6
    height = 24, 43
    lat = 43, 51
    lon = 53, 61
    name = 61, 100

    assert station_file in zip_file.namelist()
    stations_file = StringIO(zip_file.read(station_file).decode("latin-1"))
    lines = [line for line in stations_file]
    # the first line is a header line which is not in sync with the column spaced format (useless)
    # the second line is a spacer column to differentiate between header and data
    stations = {}
    for line in lines[2:]:
        stations[int(line[station_id[0] : station_id[1]])] = {
            "height": line[height[0] : height[1]].strip(),
            "lat": line[lat[0] : lat[1]].strip(),
            "lon": line[lon[0] : lon[1]].strip(),
            "name": line[name[0] : name[1]].strip(),
        }

    for filename in zip_file.namelist():
        try:
            if re.fullmatch(zip_pattern, filename):
                inner_zip = zipfile.ZipFile(BytesIO(zip_file.read(filename)))
                for filename in inner_zip.namelist():
                    if re.fullmatch(file_pattern, filename):
                        # Turn byte stream into utf8 encoded string file
                        file_stream = StringIO(inner_zip.read(filename).decode("utf-8"))
                        logger.info("handling", filename)
                        handle_file(file_stream, stations)
            elif re.fullmatch(file_pattern, filename):
                logger.info("handling", filename)
                file_stream = StringIO(zip_file.read(filename).decode("utf-8"))
                handle_file(file_stream, stations)
            else:
                logger.info(f"{filename=} not used")
        except Exception:
            traceback.print_exc()
            logger.info(f"{filename=} could not be extracted")
    return HttpResponse("Finished")


@atomic
def handle_file(file: StringIO, stations: dict):
    weatherdata = list()
    reader = csv.DictReader(file, delimiter=";")
    ws = None
    once = True
    for row in reader:
        station_id = int(row["STATIONS_ID"].strip())
        if once:
            once = False
            if station_id not in stations:
                logger.info(f"{station_id} not found in station file")
                break
            else:
                station = stations[station_id]
                WeatherStation.objects.filter(dwd_id=station_id).delete()
                ws = WeatherStation.objects.create(
                    dwd_id=station_id,
                    name=station["name"],
                    geom=Point(
                        float(station["lon"]),
                        float(station["lat"]),
                        float(station["lat"]),
                    ),
                )
        time_str = row["MESS_DATUM"].strip()
        time = datetime.datetime.strptime(time_str, "%Y%M%d%H")
        # Data is to old and wont be stored
        if time < MIN_DATE:
            continue
        air_temperature = float(row["TT_TU"].strip())
        # Measurement errors are stored as -999
        if air_temperature == -999:
            air_temperature = None
        weatherdata.append(
            WeatherData(weatherstation=ws, time=make_aware(time), air_temperature=air_temperature)
        )
    else:
        logger.info(f"Creating {len(weatherdata)} objects")
        WeatherData.objects.filter(weatherstation=ws).delete()
        WeatherData.objects.bulk_create(weatherdata)
    return


def get_weatherdata(
    dwd_id: int, startdate: datetime.datetime, enddate: datetime.datetime
) -> List[WeatherData]:
    """Return weatherdata of weatherstation sorted by temperature"""
    data = (
        WeatherData.objects.exclude(air_temperature__isnull=True)
        .filter(weatherstation__dwd_id=dwd_id, time__gte=startdate)
        .exclude(time__gt=enddate)
    )
    return data


def get_closest_station(lon: float, lat: float) -> QuerySet[WeatherStation]:
    return WeatherStation.objects.annotate(
        distance=Distance("geom", Point(lon, lat, srid=4326))
    ).order_by("distance")
