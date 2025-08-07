from django.urls import path
from .views import import_view, get_quantile_from_geo, get_quantile_from_station

app_name = "temperatures"

urlpatterns = [
    path("import/", import_view, name="import"),
    path(
        "quantile/<str:lon>/<str:lat>/<str:startdate>/<str:enddate>/<str:temperature>/",
        get_quantile_from_geo,
        name="quantile_geo",
    ),
    path(
        "quantile/<int:dwd_id>/<str:startdate>/<str:enddate>/<str:temperature>/",
        get_quantile_from_station,
        name="quantile_station",
    ),
]
