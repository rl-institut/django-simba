from django.urls import path
from .views import import_view, get_quantile

app_name = "temperatures"

urlpatterns = [
    path("import/", import_view, name="import"),
    path(
        "quantile/<str:lon>/<str:lat>/<str:startdate>/<str:enddate>/<str:temperature>/",
        get_quantile,
        name="quantile",
    ),
]
