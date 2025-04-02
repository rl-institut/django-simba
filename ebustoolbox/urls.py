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
    DashboardView,
    get_dashboard,
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
    path("dashboard/", get_dashboard, name="dashboard"),
    path(
        "dashboard/",
        DashboardView.as_view(),
        name="dashboard2",
    ),
    path(
        "depots/<uuid:task_id>",
        DepotsView.as_view(),
        name="depots",
    ),
    path("progress2/<uuid:progress_id>/<str:template_name>/", progress2, name="progress"),
    path(
        "result/<uuid:task_id>",
        ResultView.as_view(),
        name="result",
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
    path(
        "export/<str:model_str>/<uuid:task_id>",
        model_export_json,
        name="model_export_json",
    ),
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
    path("DEMO/", TemplateView.as_view(template_name="ebustoolbox/dashboard_DEMO.html")),
]
