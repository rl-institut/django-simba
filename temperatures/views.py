from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from temperatures.tasks import import_data, get_closest_station, get_weatherdata
import datetime
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


CACHE_TIMEOUT = 1 * 1


def get_quantile_from_geo(
    request, lon: str, lat: str, startdate: str, enddate: str, temperature: float
):
    lon = float(lon)
    lat = float(lat)
    station = cache.get_or_set((lon, lat), lambda: get_closest_station(lon, lat), CACHE_TIMEOUT)
    return get_quantile_from_station(request, station.dwd_id, startdate, enddate, temperature)


def get_quantile_from_station(request, dwd_id: int, startdate: str, enddate: str, temperature: str):
    startdate = datetime.datetime.fromisoformat(startdate)
    enddate = datetime.datetime.fromisoformat(enddate)
    temperature = float(temperature)
    data = cache.get_or_set(
        (id(get_weatherdata), dwd_id, startdate, enddate),
        lambda: get_weatherdata(dwd_id, startdate, enddate),
        CACHE_TIMEOUT,
    )
    if len(data) == 0:
        return JsonResponse(
            {
                "error": "No data found",
            }
        )
    data = [x.air_temperature for x in data]

    for i, temp in enumerate(data):
        if temperature < temp:
            break
    else:
        i += 1
    quantile = i / len(data)
    return JsonResponse(
        {
            "quantile": quantile,
            "air_temperature_avg": sum(data) / len(data),
            "weather_station_dwd_id": dwd_id,
            "found_data_points": len(data),
            # we expect one data point every hour, since that is the data we upload from dwd
            "missing_data_points": int(
                ((enddate - startdate).total_seconds() / 3600) + 1 - len(data)
            ),
        }
    )
