"""URLs for map app, including main view and API points."""


from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.LandingPageView.as_view(), name="landing_page"),
]
