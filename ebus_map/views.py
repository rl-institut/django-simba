"""Views for map app.

As map app is SPA, this module contains main view and various API points.
"""
from collections import namedtuple
from typing import Optional, Iterable

from django.apps import apps

# from django.conf import settings
from django.http import HttpRequest, response
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.views.generic import TemplateView
from django_mapengine import views

LookupFunctions = namedtuple("PopupData", ("data_fct", "chart_fct", "choropleth_fct"))
# from . import config
# from .results import core
#
# from . import forms, map_config, popups, utils
# from .results import calculations


class MinimalMapengineView(TemplateView, views.MapEngineMixin):
    template_name = "minimal_mapengine.html"


def get_popup(request: HttpRequest, lookup: str, id: int) -> response.JsonResponse:  # noqa: ARG001
    """Return popup as html and chart options to render chart on popup.

    Parameters
    ----------
    request : HttpRequest
        Request from app, can hold option for different language
    lookup: str
        Name is used to lookup data and chart functions
    id: int
        ID of region selected on map. Data and chart for popup is calculated for related region.

    Returns
    -------
    JsonResponse
        containing HTML to render popup and chart options to be used in E-Chart.
    """
    data = apps.get_model(app_label="ebus_map", model_name=lookup).get_popup_data(id)
    print(data, lookup)
    try:
        html = render_to_string(f"popups/{lookup}.html", context=data)
    except TemplateDoesNotExist:
        html = render_to_string("popups/default.html", context=data)
    return response.JsonResponse({"html": html})  # , "chart": chart}


def create_chart(lookup: str, chart_data: Optional[Iterable[tuple[str, float]]] = None) -> dict:
    """Create chart based on given lookup and municipality ID or result option

    Parameters
    ----------
    lookup: str
        Looks up related chart function in charts folder.
    chart_data: list[tuple[str, float]]
        Chart data separated into tuples holding key and value
        If no data is given, data is expected to be set via lookup JSON

    Returns
    -------
    dict
        Containing chart filled with data

    """
    chart = {}
    if chart_data:
        chart["series"][0]["data"] = [{"key": key, "value": value} for key, value in chart_data]
    return chart
