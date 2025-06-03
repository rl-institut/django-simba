"""URLs for map app, including main view and API points."""

from django.urls import path

from . import views

app_name = "data_scrapers"

urlpatterns = [
    path(
        "stations/map/",
        views.BusStationListView.as_view(),
        name="busstation_station_list",
    ),
    path("stations/api/", views.json_view, name="busstation_api"),
    path("stations/import/", views.import_view, name="busstation_import"),
    path("stations/export/", views.export_view, name="busstation_export"),
]
