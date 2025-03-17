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
)

from . import views

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
    path("dashboard/", views.get_dashboard, name="dashboard"),
    path(
        "depots/<uuid:task_id>",
        DepotsView.as_view(),
        name="depots",
    ),
    path("progress2/<uuid:progress_id>", progress2, name="progress"),
    path(
        "results/",
        TemplateView.as_view(template_name="ebustoolbox/results.html"),
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
        "vehicles/<uuid:task_id>",
        VehiclesView.as_view(),
        name="vehicles",
    ),
    # superfluous templates, just for show
    path("DELETE-ME/", TemplateView.as_view(template_name="ebustoolbox/signup.html")),
    path("DEMO/", TemplateView.as_view(template_name="ebustoolbox/dashboard_DEMO.html")),
]
