"""
URL configuration for ebusdjango project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include


from ebustoolbox.views import result_view, long_running_task_status_view, \
    get_chart, home_view, download_scenario, generate_zip

urlpatterns = [
    path('get_chart/', get_chart, name='get_chart'),
    path('long_running_task_status/', long_running_task_status_view, name='long_running_task_status_view'),


    path('result/', result_view, name='result'),
    path('', home_view, name='home'),
    path('admin/', admin.site.urls),
    path('django_plotly_dash/', include('django_plotly_dash.urls')),
    path("map/", include("django_mapengine.urls")),
    # ToDo move stuff to ebustoolbox app
    path('generate_zip/<str:task_id>', generate_zip, name='generate_zip'),
    path("download_scenario/<str:task_id>/", download_scenario, name='download_scenario'),
    # Map urls
    path('', include("ebus_map.urls")),

]
