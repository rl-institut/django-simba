"""URLs for map app, including main view and API points."""

from django.urls import path
from django.views.generic.base import TemplateView

from ebustoolbox import views

# from . import views

app_name = "simba"

urlpatterns = [
    path(
        "compare/",
        TemplateView.as_view(template_name="ebustoolbox/compare.html"),
        name="compare",
    ),
    path("costs/", TemplateView.as_view(template_name="ebustoolbox/costs.html"), name="costs"),
    path(
        "dashboard/",
        TemplateView.as_view(template_name="ebustoolbox/dashboard-empty-state.html"),
        name="dashboard",
    ),
    path(
        "depots/",
        TemplateView.as_view(template_name="ebustoolbox/depots.html"),
        name="depots",
    ),
    path(
        "results/<uuid:task_id>/",
        views.result_view,
        name="results",
    ),
    path(
        "stations/",
        TemplateView.as_view(template_name="ebustoolbox/stations.html"),
        name="stations",
    ),
    path(
        "summary/",
        TemplateView.as_view(template_name="ebustoolbox/summary.html"),
        name="summary",
    ),
    path(
        "trips/",
        TemplateView.as_view(template_name="ebustoolbox/trips.html"),
        name="trips",
    ),
    path(
        "vehicles/",
        TemplateView.as_view(template_name="ebustoolbox/vehicles.html"),
        name="vehicles",
    ),
    path("DELETE-ME/", TemplateView.as_view(template_name="ebustoolbox/signup.html")),
    path("DEMO/", TemplateView.as_view(template_name="ebustoolbox/dashboard.html")),

    path("result/<uuid:task_id>/", views.result_view_old, name="result"),

    # Done:
    path('results/<uuid:task_id>/soc/', views.get_soc_data, name="soc-data"),
    path('results/<uuid:task_id>/power-draw/', views.get_power_draw, name="power-draw"),
    path('results/<uuid:task_id>/station-occupation/', views.get_station_occupation, name="station-occupation"),
    path('results/<uuid:task_id>/gantt/', views.get_gantt_data, name='gantt_data'),
    path('results/<uuid:task_id>/stats/', views.get_stats, name='stats'),

    path('results/<uuid:task_id>/dist_histogram/', views.get_dist_hist, name='get_dist_hist'),
    path('results/<uuid:task_id>/speed_histogram/', views.get_speed_hist, name='get_speed_hist'),
    # Define URL patterns for each of the views
    path('results/<uuid:task_id>/critical_rotations/', views.render_critical_rotations, name='critical_rotations'),
    path('results/<uuid:task_id>/bustype/', views.render_bustype, name='bustype'),

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
