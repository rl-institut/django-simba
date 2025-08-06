from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from temperatures.tasks import import_data, get_closest_station
import datetime
from .models import WeatherData, WeatherStation
from typing import List
from django.core.cache import cache

# Create your views here.


def import_view(request):
    if request.method == "GET":
        if not request.user.is_superuser:
            return HttpResponseForbidden("Sie haben keinen Zugriff auf diese Seite.")

        return render(request, "temperatures/import.html")
    elif request.method == "POST":
        import_data(request.FILES["zipfile"])
        return HttpResponse("finished")
    return HttpResponseBadRequest("Method not allowed")


def get_weatherdata(
    weatherstation: WeatherStation, startdate: datetime.datetime, enddate: datetime.datetime
) -> List[WeatherData]:
    print("not cached")
    data = list(
        WeatherData.objects.exclude(air_temperature__isnull=True)
        .filter(weatherstation=weatherstation, time__gte=startdate)
        .exclude(time__gt=enddate)
    )
    return data


def get_quantile(request, lon: str, lat: str, startdate: str, enddate: str, temperature: float):
    CACHE_TIMEOUT = 10 * 60
    lon = float(lon)
    lat = float(lat)
    startdate = datetime.datetime.fromisoformat(startdate)
    enddate = datetime.datetime.fromisoformat(enddate)
    station = cache.get_or_set((lon, lat), lambda: get_closest_station(lon, lat), CACHE_TIMEOUT)
    data = cache.get_or_set(
        (id(get_weatherdata), station.id, startdate, enddate),
        lambda: get_weatherdata(station, startdate, enddate),
        CACHE_TIMEOUT,
    )
    data = [x.air_temperature for x in data]
    return JsonResponse(
        {
            "air_temperature": sum(data) / len(data),
            "weather_station": str((station.name, station.geom.x, station.geom.y)),
            "found_data_points": len(data),
            # we expect one data point every hour, since that is the data we upload from dwd
            "missing_data_points": int(
                ((enddate - startdate).total_seconds() / 3600) + 1 - len(data)
            ),
        }
    )
