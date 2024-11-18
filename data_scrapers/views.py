from django.views.generic import ListView
from django.http import Http404
from data_scrapers.models import BusStation
from data_scrapers.tasks import search_stations


# Create your views here.
class BusStationListView(ListView):
    model = BusStation
    template_name = "leaflet.html"

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        search_stations_request = self.request.GET.get("search_stations").split(",")
        found_stations = search_stations(search_stations_request)
        if not len(found_stations) > 0:
            raise Http404("No stations found")
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
