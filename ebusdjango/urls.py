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

urlpatterns = [
    path("simba/", include("ebustoolbox.urls")),
    path("elevation_api/", include("elevation_api.urls")),
    path("", include("core.urls")),
    path("django_plotly_dash/", include("django_plotly_dash.urls")),
    path("map/", include("django_mapengine.urls")),
    # Admin and authentification urls
    path("", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
    # Map urls
    path("", include("ebus_map.urls")),
]
