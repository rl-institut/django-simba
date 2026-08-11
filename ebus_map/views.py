"""Views for map app.

As map app is SPA, this module contains main view and various API points.
"""
from collections import namedtuple
from typing import Optional, Iterable

from django.apps import apps

# from django.conf import settings
from django.http import HttpRequest, response
from django.shortcuts import get_object_or_404
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
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


def get_popup(
    request: HttpRequest, task_id: str, lookup: str, id: int
) -> response.JsonResponse:
    """Return popup as html and chart options to render chart on popup.

    The scenario's task_id is part of the URL and is checked, like it is for the vector tiles this
    popup belongs to. Without it the route took a sequential primary key and answered for any
    object in the database, so the popups of every scenario could be walked by counting upwards.

    Parameters
    ----------
    request : HttpRequest
        Request from app, can hold option for different language
    task_id: str
        Task id of the scenario the map is showing. Authorises the request and scopes the lookup.
    lookup: str
        Name is used to lookup data and chart functions
    id: int
        ID of region selected on map. Data and chart for popup is calculated for related region.

    Returns
    -------
    JsonResponse
        containing HTML to render popup and chart options to be used in E-Chart.
    """
    # Imported here because ebustoolbox.views imports ebus_map.managers at module scope
    from ebustoolbox.views import AuthorizedMixIn

    if not AuthorizedMixIn.get_permission(request.user, task_id):
        return response.HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))

    scenario_model = apps.get_model(app_label="ebustoolbox", model_name="scenario")
    scenario = get_object_or_404(scenario_model, task_id=task_id)
    # The tiles are drawn from the sizing scenario where there is one (see django_mapengine.mvt),
    # so that is where a feature id on the map has to be resolved.
    displayed_scenario = scenario.get_sizing_scenario() or scenario

    model = apps.get_model(app_label="ebustoolbox", model_name=lookup)
    obj = get_object_or_404(model, id=id, scenario=displayed_scenario)

    data = model.get_popup_data(obj.id)
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
