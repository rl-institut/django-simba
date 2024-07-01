import traceback

from django.conf import settings
from django.http import JsonResponse, HttpResponse, Http404
from .api import get_elevation_pseudo


def elevation_token_view(request, lat_long_query: str = None):
    """Token is embedded"""
    if lat_long_query is None:
        lat_long_query = request.GET.get("locations", None)
    return elevation_json(request, lat_long_query)


def elevation_view(request, lat_long_query: str = None):
    token = request.GET.get("token")
    if token != settings.DJANGO_ELEVATION_TOKEN and settings.DJANGO_ELEVATION_TOKEN:
        return HttpResponse("Invalid token", 403)
    return elevation_json(request, lat_long_query)


def elevation_json(request, lat_long_query: str = None):
    if lat_long_query is None:
        lat_long_query = request.GET.get("locations", None)
        if lat_long_query is None:
            raise Http404("No latitude and longitude provided")

    if "|" in lat_long_query:
        lat_longs = lat_long_query.split("|")
    else:
        lat_longs = [lat_long_query]
    lats = []
    longs = []
    if not len(lat_longs[0].split(",")) == 2:
        raise Http404("Number of latitudes and longitudes must match")
    try:
        for lat_longs in lat_longs:
            lats.append(float(lat_longs.split(",")[0]))
            longs.append(float(lat_longs.split(",")[1]))
    except ValueError:
        raise Http404("Latitudes and longitudes must be numbers")
    try:
        elevations, errors = get_elevation_pseudo(lats, longs)
    except:  # noqa
        traceback.print_exc()
        raise Http404("Elevation error")
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
