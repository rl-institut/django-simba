"""URLs for map app, including main view and API points."""

from django.urls import path
from django.views.generic.base import TemplateView
from ebustoolbox.views import (
    TripsView,
    VehiclesView,
    get_notifications,
    progress_scenario,
    StationsView,
    CostsView,
    FilterView,
    DepotsView,
    SummaryView,
    model_export_json,
    merge_and_run,
    run_simulation,
    get_dashboard,
    compare,
    export_scenario,
    export_scenario_tree,
    import_scenario,
    import_scenario_tree,
)

from ebustoolbox import views

# from . import views

app_name = "simba"

urlpatterns = [
    path("compare/", compare, name="compare"),
    path(
        "costs/<uuid:task_id>/",
        CostsView.as_view(),
        name="costs",
    ),
    path("dashboard/", get_dashboard, name="dashboard"),
    path(
        "depots/<uuid:task_id>/",
        DepotsView.as_view(),
        name="depots",
    ),
    path("progress2/<uuid:progress_id>/<str:template_name>/", progress_scenario, name="progress"),
    path(
        "result/<uuid:task_id>/",
        views.result_view,  # only way I get MapengineMixin to work
        name="result",
    ),
    path(
        "stations/<uuid:task_id>/",
        StationsView.as_view(),
        name="stations",
    ),
    path(
        "summary/<uuid:task_id>/",
        SummaryView.as_view(),
        name="summary",
    ),
    path(
        "notifications/<uuid:task_id>/<str:view>/",
        get_notifications,
        name="notifications",
    ),
    path(
        "trips/",
        TripsView.as_view(),
        name="trips",
    ),
    path(
        "trips/<uuid:task_id>/",
        TripsView.as_view(),
        name="trips",
    ),
    path(
        "trips/<uuid:task_id>/<int:first>/",
        TripsView.as_view(),
        name="trips",
    ),
    path(
        "export/<str:model_str>/<uuid:task_id>/",
        model_export_json,
        name="model_export_json",
    ),
    path(
        "import_tree/",
        import_scenario_tree,
        name="JSON_import_scenario_tree",
    ),
    path(
        "export_tree/<uuid:task_id>/",
        export_scenario_tree,
        name="JSON_export_scenario_tree",
    ),
    path(
        "export/",
        export_scenario,
        name="JSON_export_scenario",
    ),
    path(
        "import/",
        import_scenario,
        name="JSON_import_scenario",
    ),
    path(
        "filter_scenario/<uuid:task_id>/",
        FilterView.as_view(),
        name="filter_scenario",
    ),
    path(
        "filter_scenario/<uuid:task_id>/get_count/",
        FilterView.get_rotation_count,
        name="filter_scenario_get_count",
    ),
    path(
        "vehicles/<uuid:task_id>/",
        VehiclesView.as_view(),
        name="vehicles",
    ),
    path("run_simulation/<uuid:task_id>/", run_simulation, name="run_simulation"),
    # path("merge_scenario/<uuid:task_id>/", merge_scenario, name="merge_scenario"),
    path("merge_and_run/<uuid:task_id>/", merge_and_run, name="merge_and_run"),
    # superfluous templates, just for show
    path("DEMO/", TemplateView.as_view(template_name="ebustoolbox/dashboard_DEMO.html")),
    # results endpoints
    path("result/<uuid:task_id>/soc/", views.get_soc_data, name="soc_data"),
    path("result/<uuid:task_id>/power-draw/", views.get_power_draw, name="power_draw_data"),
    path("result/<uuid:task_id>/stats/", views.get_stats, name="stats"),
    path("result/<uuid:task_id>/dist_histogram/", views.get_dist_hist, name="dist_hist_data"),
    path("result/<uuid:task_id>/speed_histogram/", views.get_speed_hist, name="speed_hist_data"),
    path("result/<uuid:task_id>/tco/", views.get_tco, name="tco_data"),
    path(
        "result/<uuid:task_id>/critical_rotations/",
        views.render_critical_rotations,
        name="critical_rotations",
    ),
    path(
        "result/<uuid:task_id>/cumulative_coverage/",
        views.get_cumulative_energy,
        name="get_cumulative_energy",
    ),
    path(
        "result/<uuid:task_id>/rotation_table/",
        views.get_rotation_table_data,
        name="get_rotation_table_data",
    ),
    path("result/<uuid:task_id>/bustype/", views.render_bustype, name="bustype"),
    path("result/<uuid:task_id>/soc_hist/", views.get_binned_soc_data, name="soc_hist"),
    path("result/<uuid:task_id>/depot_power/", views.get_power_draw_and_occ, name="eflips_power"),
    path(
        "result/<uuid:task_id>/depot_power/<int:depot_id>/",
        views.get_power_draw_and_occ,
        name="eflips_power",
    ),
    path("result/<uuid:task_id>/gantt/", views.get_gantt, name="gantt"),
    path("load_test/<uuid:task_id>/", views.loadTester, name="loadtest"),
    path("delete/<uuid:task_id>/", views.delete_scenario, name="delete"),
]
