from django.conf import settings
from django.http import JsonResponse, HttpResponse
from .api import get_elevation


def elevation_token_view(request, lat_long_query: str = None):
    if lat_long_query is None:
        lat_long_query = request.GET.get("locations", None)

    if "|" in lat_long_query:
        lat_longs = lat_long_query.split("|")
    else:
        lat_longs = [lat_long_query]
    lats = []
    longs = []
    assert len(lat_longs[0].split(",")) == 2
    for lat_longs in lat_longs:
        lats.append(float(lat_longs.split(",")[0]))
        longs.append(float(lat_longs.split(",")[1]))

    elevations, errors = get_elevation(lats, longs)

    results = []
    for lat, long, ele, error in zip(lats, longs, elevations, errors):
        result = {"latitude": lat, "longitude": long, "elevation": ele}
        if error is None:
            result["error"] = False
        else:
            result["error"] = True
            result["error_text"] = error
        results.append(result)

    results = {"results": results}
    return JsonResponse(results)


def elevation_view(request, lat_long_query: str = None):
    token = request.GET.get("token")
    if token != settings.DJANGO_ELEVATION_TOKEN and settings.DJANGO_ELEVATION_TOKEN:
        return HttpResponse("Invalid token", 403)

    if lat_long_query is None:
        lat_long_query = request.GET.get("locations", None)

    if "|" in lat_long_query:
        lat_longs = lat_long_query.split("|")
    else:
        lat_longs = [lat_long_query]
    lats = []
    longs = []
    assert len(lat_longs[0].split(",")) == 2
    for lat_longs in lat_longs:
        lats.append(float(lat_longs.split(",")[0]))
        longs.append(float(lat_longs.split(",")[1]))

    elevations, errors = get_elevation(lats, longs)

    results = []
    for lat, long, ele, error in zip(lats, longs, elevations, errors):
        result = {"latitude": lat, "longitude": long, "elevation": ele}
        if error is None:
            result["error"] = False
        else:
            result["error"] = True
            result["error_text"] = error
        results.append(result)

    results = {"results": results}
    return JsonResponse(results)
