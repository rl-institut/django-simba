from django.shortcuts import render
from django.views.generic import ListView
from django.http import Http404, JsonResponse, HttpResponse

import data_scrapers.models
from data_scrapers.models import BusStation, AdminArea
from data_scrapers.tasks import search_stations
import pandas as pd


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


def import_view(request):
    if not request.user.is_superuser:
        raise Http404("Only admins can import data")
    if request.method == "GET":
        return render(request, "data_scrapers/import.html")

    if request.method == "POST":
        if AdminArea.objects.all().count() > 0 or BusStation.objects.all().count() > 0:
            return Http404(
                f"Data can only be imported in empty Database. "
                f"There are AdminAreas {AdminArea.objects.all().count()} \n"
                f"There are BusStations {BusStation.objects.all().count()}"
            )

        assert request.FILES["file_stations"]
        assert request.FILES["file_admin_areas"]

        df_areas = pd.read_csv(request.FILES["file_admin_areas"])
        df_stations = pd.read_csv(request.FILES["file_stations"])
        data_scrapers.models.import_data(df_areas, df_stations)
        area_count = AdminArea.objects.all().count()
        station_count = BusStation.objects.all().count()
        return HttpResponse(
            f"Success. {area_count} AdminAreas and {station_count} Stations imported"
        )
    return Http404("Something went wrong")


def export_view(request):
    if not request.user.is_superuser:
        raise Http404("Admins can only export data")
    zip_buffer = data_scrapers.models.create_export_buffer()
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=export.zip"
    return response
