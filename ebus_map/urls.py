"""URLs for map app, including main view and API points."""


from django.urls import path

from . import views
from HPCtool.views import HomePageView, delete_bus

app_name = "map"

urlpatterns = [
    # path("", views.MapGLView.as_view(), name="map"),
    # path("", views.MapGLView.as_view(), name="map"),
    ##path("minimal", views.MinimalMapengineView.as_view(), name="minimal"),
    path("hpctool", HomePageView.as_view(), name="hpctool"),
    path("hpctool/delete", delete_bus, name="delete_bus"),
    # path("choropleth/<str:lookup>/<str:scenario>", views.get_choropleth, name="choropleth"),
    # path("visualization", views.get_visualization, name="visualization"),
    path("popup/<str:lookup>/<int:id>", views.get_popup, name="popup"),
]
