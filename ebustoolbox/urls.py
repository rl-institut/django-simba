"""URLs for map app, including main view and API points."""

from django.urls import path
from django.views.generic.base import TemplateView
from ebustoolbox.views import (
    TripsView,
    VehiclesView,
    progress2,
    StationsView,
    CostsView,
    DepotsView,
    SummaryView,
    model_export_json,
    merge_and_run,
    run_simulation,
    ResultView,
)

# from . import views

app_name = "simba"

urlpatterns = [
    path(
        "compare/",
        TemplateView.as_view(template_name="ebustoolbox/compare.html"),
        name="compare",
    ),
    path(
        "costs/<uuid:task_id>",
        CostsView.as_view(),
        name="costs",
    ),
    path(
        "dashboard/",
        TemplateView.as_view(template_name="ebustoolbox/dashboard-empty-state.html"),
        name="dashboard",
    ),
    path(
        "depots/<uuid:task_id>",
        DepotsView.as_view(),
        name="depots",
    ),
    path("progress2/<uuid:progress_id>/<str:template_name>/", progress2, name="progress"),
    path(
        "results/<uuid:task_id>",
        ResultView.as_view(),
        name="results",
    ),
    path(
        "stations/",
        TemplateView.as_view(template_name="ebustoolbox/stations.html"),
        name="stations",
    ),
    path(
        "stations/<uuid:task_id>",
        StationsView.as_view(),
        name="stations",
    ),
    path(
        "summary/<uuid:task_id>",
        SummaryView.as_view(),
        name="summary",
    ),
    path(
        "trips/",
        TripsView.as_view(),
        name="trips",
    ),
    path(
        "trips/<uuid:task_id>",
        TripsView.as_view(),
        name="trips",
    ),
    path(
        "trips/<uuid:task_id>/<int:first>",
        TripsView.as_view(),
        name="trips",
    ),
    path("export/<str:model_str>/<uuid:task_id>", model_export_json, name="model_export_json"),
    # path("export/<str:model>/<uuid:task_id>", ModelListView.as_view(), name="model_export_json"),
    path(
        "vehicles/<uuid:task_id>",
        VehiclesView.as_view(),
        name="vehicles",
    ),
    path("run_simulation/<uuid:task_id>/", run_simulation, name="run_simulation"),
    # path("merge_scenario/<uuid:task_id>/", merge_scenario, name="merge_scenario"),
    path("merge_and_run/<uuid:task_id>/", merge_and_run, name="merge_and_run"),
    # superfluous templates, just for show
    path("DELETE-ME/", TemplateView.as_view(template_name="ebustoolbox/signup.html")),
    path("DEMO/", TemplateView.as_view(template_name="ebustoolbox/dashboard.html")),
]

"""
# ToDo Append "/" to all url paths
urlpatterns = [
    path(
        "long_running_task_status/",
        views.long_running_task_status_view,
        name="long_running_task_status_view",
    ),
    path("scenarios/", views.scenarios, name="scenarios"),
    path("usergroups/", views.usergroups, name="usergroups"),
    path("copy/<uuid:task_id>/", views.copy_scenario, name="copy_scenario"),
    path("result/<uuid:task_id>/", views.result_view, name="result"),
    path("input/schedule/<uuid:task_id>/<str:finished>", views.schedule, name="schedule"),
    path(
        "input/schedule/", views.schedule, {"task_id": None, "finished": "false"}, name="schedule"
    ),
    path(
        "input/simulation_parameters/<uuid:task_id>",
        views.get_simulation_parameters,
        name="simulation_parameters",
    ),
    path(
        "input/get_options/<uuid:task_id>/<int:reader_num>", views.get_options, name="get_options"
    ),
    path("input/vehicle_types/<uuid:task_id>", views.get_vehicle_types, name="vehicle_types"),
    path("input/depots/<uuid:task_id>", views.get_depots, name="depots"),
    path("input/electrification/<uuid:task_id>", views.get_electrification, name="electrification"),
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
    # path("set_station_values/<uuid:task_id>", views.set_station_values, name="set_station_values"),
    path("upload_trips/<uuid:task_id>/<int:reader_num>", views.upload_trips, name="upload_trips"),
    path("cancel_upload/<uuid:task_id>", views.cancel_upload, name="cancel_upload"),
    path("generate_zip/<uuid:task_id>", views.generate_zip, name="generate_zip"),
    path("download_scenario/<uuid:task_id>/", views.download_scenario, name="download_scenario"),
    path("progress/<uuid:progress_id>/<str:progress_type>", views.progress, name="progress"),
    path("run_simulation/<uuid:task_id>/", views.run_simulation, name="run_simulation"),
]
"""
