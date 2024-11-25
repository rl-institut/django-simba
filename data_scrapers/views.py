from django.views.generic import ListView
from django.http import Http404, JsonResponse
from data_scrapers.models import BusStation
from data_scrapers.tasks import search_stations


# Create your views here.
class BusStationListView(ListView):
    model = BusStation
    template_name = "leaflet.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        search_stations_request = self.request.GET.get("search_stations").split("|")
        use_filter = self.request.GET.get("filter", "false").lower() == "true"
        found_stations = search_stations(search_stations_request, use_filter)
        if not len(found_stations) > 0:
            raise Http404(
                "No stations found. If searching for multiple stations use '|' as seperator."
                " If the name contains '+' signs they need to be replaced by"
                "'%2B'"
            )
        geoms = []
        for key, stations in found_stations.items():
            for station in stations:
                geoms.append(
                    {
                        "lat": station.geom.y,
                        "lon": station.geom.x,
                        "popup": f"searched for {key}. Found: {station.name}",
                    }
                )
        context["center_lat"] = sum(geom["lat"] for geom in geoms) / len(geoms)
        context["center_lon"] = sum(geom["lon"] for geom in geoms) / len(geoms)
        context["geoms"] = geoms

        return context


# Create your views here.
def json_view(request):
    search_stations_request = request.GET.get("search_stations", "").split("|")
    use_filter = request.GET.get("filter", "false").lower() == "true"
    if len(search_stations_request) == 0:
        raise Http404(
            "search_stations must be part of the query."
            "If searching for multiple stations use | as seperator."
            "If all found stations should be returned, add &filter=False to the query"
        )
    results = {"results": dict()}
    stations = search_stations(search_stations_request, use_filter=use_filter)
    for search_name, stations in stations.items():
        station_values = list(stations.values("name", "geom", "admin_area__name"))
        for stat in station_values:
            stat["latitude"] = stat["geom"].y
            stat["longitude"] = stat["geom"].x
            del stat["geom"]
        results["results"][search_name] = station_values

    return JsonResponse(results, safe=True)
