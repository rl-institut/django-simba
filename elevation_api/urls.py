"""URLs for map app, including main view and API points."""
from django.urls import path
from django.conf import settings
from . import views

app_name = "elevation_api"

urlpatterns = [
    path(
        f"{settings.DJANGO_ELEVATION_TOKEN}/api/v1/lookup/",
        views.elevation_token_view,
        name="elevation_view",
    ),
    path(
        "<str:lat_long_query>",
        views.elevation_view,
        name="elevation_view_with_get_token",
    ),
]
