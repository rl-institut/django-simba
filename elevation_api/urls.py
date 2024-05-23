"""URLs for map app, including main view and API points."""
from django.urls import path

from . import views

app_name = "elevation_api"

urlpatterns = [
    path(
        "api/v1/lookup",
        views.elevation_view,
        name="elevation_view",
    ),
    path(
        "<str:lat_long_query>",
        views.elevation_view,
        name="elevation_view",
    ),
]
