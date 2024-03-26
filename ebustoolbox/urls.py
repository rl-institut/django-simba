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
    path("generate_zip/<uuid:task_id>", views.generate_zip, name="generate_zip"),
    path("download_scenario/<uuid:task_id>/", views.download_scenario, name="download_scenario"),
    # path("popup/<str:lookup>/<int:id>", views.get_popup, name="popup"),
]
