"""URLs for map app, including main view and API points."""


from django.urls import path
from .views import *

app_name = "HPCtool"

urlpatterns = [
    path("hpctool", HomePageView.as_view(), name="hpctool"),
    path("hpctool/delete", delete_bus, name="delete_bus"),
    path("hpctool/create", create_station, name="create_station"),
    path("hpctool/settings", get_settings, name="get_settings"),
    path("hpctool/getstationlist", get_stationlist, name="get_stationlist"),
    path("hpctool/stationpopup/<int:id>", get_station_popup, name="popup"),
    path("hpctool/export", export_data, name="export_data"),
]

