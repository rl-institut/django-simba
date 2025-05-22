import logging
import pandas as pd

from django.shortcuts import render
from django.views.generic import ListView
from django.http import (
    HttpRequest,
    HttpResponseNotAllowed,
    JsonResponse,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseBadRequest,
)

from data_scrapers.models import BusStation, AdminArea
from data_scrapers.import_export import create_export_buffer, import_data
from data_scrapers.tasks import search_stations

logger = logging.getLogger("custom")

QUERY_PARAM = "search_stations"


def is_request_valid(request: HttpRequest) -> bool:
    search_stations_request = request.GET.get(QUERY_PARAM)
    if search_stations_request is None or search_stations_request == "":
        return False
    return True


def get_station_search(request: HttpRequest) -> list[str]:
    search_stations_request = request.GET.get(QUERY_PARAM)
    return search_stations_request.split("|")


class BusStationListView(ListView):
    model = BusStation
    template_name = "data_scrapers/minimal_leaflet_map_w_content.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        found_stations = dict()
        if not is_request_valid(self.request):
            # Let the user know that the query was bad
            context["error"] = f"The Station search API needs the non empty param '{QUERY_PARAM}'."
        else:
            search_stations_request = get_station_search(self.request)
            logger.info(f"Searching for {len(search_stations_request)} stations")
            use_filter = self.request.GET.get("filter", "false").lower() == "true"
            found_stations = search_stations(search_stations_request, use_filter)
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
        logger.info(
            f"Found {len(found_stations)} stations with {sum([len(x) for x in found_stations.values()])}"
            f" separate stops"
        )

        context["center_lat"] = 0
        context["center_lon"] = 0
        if geoms:
            context["center_lat"] = sum(geom["lat"] for geom in geoms) / len(geoms)
            context["center_lon"] = sum(geom["lon"] for geom in geoms) / len(geoms)
        context["geoms"] = geoms

        return context


def json_view(request):
    search_stations_request = get_station_search(request)
    use_filter = request.GET.get("filter", "false").lower() == "true"
    if len(search_stations_request) == 0:
        return HttpResponseBadRequest(
            "search_stations must be part of the query."
            "If searching for multiple stations use | as seperator."
            "If all found stations should be returned, add &filter=False to the query"
        )
    results = {"results": dict()}
    logger.info(f"Searching for {len(search_stations_request)} stations")
    stations = search_stations(search_stations_request, use_filter=use_filter)
    for search_name, stats in stations.items():
        station_values = list(stats.values("name", "geom", "admin_area__name"))
        for stat in station_values:
            stat["latitude"] = stat["geom"].y
            stat["longitude"] = stat["geom"].x
            del stat["geom"]
        results["results"][search_name] = station_values
    res = results["results"]
    logger.info(
        f"Found {len(res)} stations with {sum([len(x) for x in stations.values()])}"
        f" separate stops"
    )
    return JsonResponse(results, safe=True)


def import_view(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only admins can import data")
    if request.method == "GET":
        return render(request, "data_scrapers/import.html")

    if request.method == "POST":
        if AdminArea.objects.all().exists() or BusStation.objects.all().exists():
            return HttpResponseForbidden(
                f"Data can only be imported in empty database.\n"
                f"There are {AdminArea.objects.count()} AdminAreas.\n"
                f"There are {BusStation.objects.count()} BusStations."
            )

        assert request.FILES["file_stations"]
        assert request.FILES["file_admin_areas"]

        df_areas = pd.read_csv(request.FILES["file_admin_areas"])
        df_stations = pd.read_csv(request.FILES["file_stations"])
        logger.info("Starting import of admin areas and bus stations.")
        import_data(df_areas, df_stations)
        area_count = AdminArea.objects.count()
        station_count = BusStation.objects.count()
        return HttpResponse(
            f"Success. {area_count} AdminAreas and {station_count} Stations imported"
        )
    return HttpResponseNotAllowed("Only GET and POST are allowed")


def export_view(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only Admins can export data")
    zip_buffer = create_export_buffer()
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=export.zip"
    return response
