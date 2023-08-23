"""URLs for map app, including main view and API points."""


from django.urls import path

from .views import *

app_name = "simba"

urlpatterns = [
    path('get_chart/', get_chart, name='get_chart'),
    path('long_running_task_status/', long_running_task_status_view, name='long_running_task_status_view'),
    path('result/', result_view, name='result'),
    path('', home_view, name='home'),
    path('generate_zip/<str:task_id>', generate_zip, name='generate_zip'),
    path("download_scenario/<uuid:task_id>/", download_scenario, name='download_scenario'),
]
