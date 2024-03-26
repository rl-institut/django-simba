"""URLs for map app, including main view and API points."""

from django.urls import path

from . import views

app_name = "simba"

urlpatterns = [
    path(
        "long_running_task_status/",
        views.long_running_task_status_view,
        name="long_running_task_status_view",
    ),
    path("scenarios/", views.scenarios, name="scenarios"),
    path("usergroups/", views.usergroups, name="usergroups"),
    path("result/", views.result_view, name="result"),
    path("", views.home_view, name="home"),
    path("input/schedule/", views.home_prototype, name="home_prototype"),
    path("input/get_options/<int:reader_num>", views.get_options, name="get_options"),
    # path("input/vehicle_types/", views.vehicle_types, name="vehicle_types"),
    path("input/vehicle_types/<uuid:task_id>", views.get_vehicle_types, name="vehicle_types"),
    path("input/stations/", views.stations, name="name"),
    path("upload_trips/<uuid:task_id>", views.upload_trips, name="upload_trips"),
    path("check_trips_file/<uuid:task_id>", views.check_trips_file, name="check_trips_file"),
    path("continue_trips/<uuid:task_id>", views.continue_trips, name="continue_trips"),
    path("generate_zip/<uuid:task_id>", views.generate_zip, name="generate_zip"),
    path("download_scenario/<uuid:task_id>/", views.download_scenario, name="download_scenario"),
    path("progress/<uuid:task_id>/", views.progress, name="progress"),
]
