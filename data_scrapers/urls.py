"""URLs for map app, including main view and API points."""

from django.urls import path

from . import views

app_name = "data_scrapers"

urlpatterns = [
    path(
        "stations/",
        views.BusStationListView.as_view(),
        name="busstation_station_list",
    )
]
