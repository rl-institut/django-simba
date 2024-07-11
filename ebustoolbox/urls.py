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
    path("result/<uuid:task_id>/", views.result_view, name="result"),
    path("", views.home_view, name="home"),
    path("input/schedule/<uuid:task_id>/<str:finished>", views.schedule, name="schedule"),
    path(
        "input/schedule/", views.schedule, {"task_id": None, "finished": "false"}, name="schedule"
    ),
    path(
        "input/simulation_parameters/<uuid:task_id>",
        views.get_simulation_parameters,
        name="simulation_parameters",
    ),
    path("input/home_prototype/", views.home_prototype, name="home_prototype"),
    path(
        "input/get_options/<uuid:task_id>/<int:reader_num>", views.get_options, name="get_options"
    ),
    path("input/vehicle_types/<uuid:task_id>", views.get_vehicle_types, name="vehicle_types"),
    path("input/depots/<uuid:task_id>", views.get_depots, name="depots"),
    path("input/stations/<uuid:task_id>", views.get_stations, name="stations"),
    path(
        "input/scenario_overview/<uuid:task_id>/<str:finished>",
        views.scenario_overview_view,
        name="scenario_overview",
    ),
    path(
        "input/scenario_overview/<uuid:task_id>",
        views.scenario_overview_view,
        {"finished": "false"},
        name="scenario_overview",
    ),
    path("set_station_values/<uuid:task_id>", views.set_station_values, name="set_station_values"),
    path("upload_trips/<uuid:task_id>/<int:reader_num>", views.upload_trips, name="upload_trips"),
    path("cancel_upload/<uuid:task_id>", views.cancel_upload, name="cancel_upload"),
    path("generate_zip/<uuid:task_id>", views.generate_zip, name="generate_zip"),
    path("download_scenario/<uuid:task_id>/", views.download_scenario, name="download_scenario"),
    path("progress/<uuid:progress_id>/<str:progress_type>", views.progress, name="progress"),
    path("run_simulation/<uuid:task_id>/", views.run_simulation, name="run_simulation"),
]
