import traceback
from django.http import JsonResponse
from .api import get_elevation


def elevation_view(request, lat_long_query: str = None):
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

    error = False
    error_text = ""
    try:
        elevations = get_elevation(lats, longs)
    except Exception as e:
        error_text = str(e)
        traceback.print_exc()
        error = True
        elevations = [0 for _ in range(len(lats))]

    results = [
        {"latitude": lat, "longitude": long, "elevation": ele}
        for lat, long, ele in zip(lats, longs, elevations)
    ]
    if error:
        for result in results:
            result["error"] = True
            result["error_text"] = error_text
    results = {"results": results}
    return JsonResponse(results)
