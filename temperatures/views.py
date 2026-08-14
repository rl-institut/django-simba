from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from temperatures.tasks import import_data, get_closest_station, get_weatherdata
import datetime
from django.conf import settings
from django.core.cache import cache

# Create your views here.


def import_view(request):
    if request.method == "GET":
        if not request.user.is_superuser:
            return HttpResponseForbidden("Du hast keinen Zugriff auf diese Seite.")

        return render(request, "temperatures/import.html")
    elif request.method == "POST":
        import_data(request.FILES["zipfile"])
        return HttpResponse("finished")
    return HttpResponseBadRequest("Method not allowed")


TIMEOUT_MINUTES = 2
CACHE_TIMEOUT = TIMEOUT_MINUTES * 60


def get_quantile_from_geo(
    request, lon: str, lat: str, startdate: str, enddate: str, temperature: float
):
    lon = float(lon)
    lat = float(lat)
    if settings.REDIS_URL:
        station = cache.get_or_set((lon, lat), lambda: get_closest_station(lon, lat), CACHE_TIMEOUT)
    else:
        station = get_closest_station(lon, lat)
    return get_quantile_from_station(request, station.dwd_id, startdate, enddate, temperature)


def get_temperature_statistics(
    dwd_id: int, startdate: datetime.datetime, enddate: datetime.datetime
) -> dict:
    step = 0.5
    data = list(get_weatherdata(dwd_id, startdate, enddate).order_by("air_temperature"))
    if len(data) == 0:
        return {}
    histogram = list()
    min_temp = data[0].air_temperature
    count = 0
    for d in data:
        if d.air_temperature < min_temp + step:
            count += 1
        else:
            histogram.append((min_temp, count))
            count = 0
            min_temp = min_temp + step

    return {
        "histogram": histogram,
        "air_temperature_avg": sum([d.air_temperature for d in data]) / len(data),
        "weather_station_dwd_id": dwd_id,
        "found_data_points": len(data),
        # we expect one data point every hour, since that is the data we upload from dwd
        "missing_data_points": int(((enddate - startdate).total_seconds() / 3600) + 1 - len(data)),
    }


def get_quantile_from_station(request, dwd_id: int, startdate: str, enddate: str, temperature: str):
    startdate = datetime.datetime.fromisoformat(startdate)
    enddate = datetime.datetime.fromisoformat(enddate)
    temperature = float(temperature)
    # // Caching speeds up data fetching but for 100_000 datapoints its still taking half a second
    # Using a hash is more flexible with possibly varying backends
    # memcache does not like special characters
    if settings.REDIS_URL:
        stats = cache.get_or_set(
            (
                hash(
                    str(
                        (
                            id(get_temperature_statistics),
                            dwd_id,
                            startdate.isoformat(),
                            enddate.isoformat(),
                        )
                    )
                )
            ),
            lambda: get_temperature_statistics(dwd_id, startdate, enddate),
            CACHE_TIMEOUT,
        )
    else:
        stats = get_temperature_statistics(dwd_id, startdate, enddate)
    if len(stats) == 0:
        return JsonResponse(
            {
                "error": "No data found",
            }
        )

    i = 0
    for temp, count in stats["histogram"]:
        if temperature < temp:
            break
        else:
            i += count
    stats["quantile"] = i / stats["found_data_points"]
    return JsonResponse(stats)
