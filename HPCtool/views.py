from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.http import HttpRequest, response

from django.views.generic import TemplateView

from django_mapengine import views

from celery.result import AsyncResult
from celery import uuid
from .tasks import calculate_chargers

from .models import BusOutline

import ast


class HomePageView(TemplateView, views.MapEngineMixin):
    template_name = "hpctool.html"

    def post(self, request, **kwargs):
        # change json object to list of lists:
        PL = ast.literal_eval(list(request.POST.dict())[0])

        result = calculate_chargers.apply_async(([PL]), polygon=[PL])

        response = JsonResponse({"error": "there was an error",
                                 "message": "Recalculating with new Polygon... Please wait" + result.get()})
        response.status_code = 200  # oder 400 # To announce that the user isn't allowed to publish

        return response


def delete_bus(request: HttpRequest) -> response.JsonResponse:
    # print("Trying to delete this bus: ")

    to_delete = ast.literal_eval(list(request.POST.dict())[0])
    # print(to_delete)

    for id in to_delete:
        # print("Objects in Database before: ", BusOutline.objects.all().count())
        # print("Deleting ID ", int(id))
        BusOutline.objects.get(id=int(id)).delete()
        # ("Objects in Database after: ", BusOutline.objects.all().count(), "\n\n")
    return response.JsonResponse({"message": "Successfully deleted! I should implement something "
                                             "to remove the busses from view......"})  # , "chart": chart}


def get_station_popup(request: HttpRequest, id: int) -> response.JsonResponse:  # noqa: ARG001
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
    # data = apps.get_model(app_label="ebustoolbox", model_name=lookup).get_popup_data(id)
    # try:
    #    html = #render_to_string(f"popups/{lookup}.html", context=data)
    # except TemplateDoesNotExist:
    #    html = render_to_string("popups/default.html", context=data)

    html = "<h1> LADELEISTUNG: 123 kW</h1> and ID is " + str(id)

    return response.JsonResponse({"html": html})  # , "chart": chart}


import json


def generate_json_data():
    # Fetch and process your data here
    data = [{'hello': 'world'}]  # Your data as a list of dictionaries

    # Convert data to JSON
    json_data = json.dumps(data)

    return json_data


def export_data(request):
    json_data = generate_json_data()

    # Create an HTTP response with the JSON data as a downloadable file
    response_json = HttpResponse(json_data, content_type='application/json')
    response_json['Content-Disposition'] = 'attachment; filename="exported_data.json"'

    return response_json
