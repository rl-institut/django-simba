from django.contrib.gis.geos import Point
from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.http import HttpRequest, response

from django.views.generic import TemplateView
from shapely import Polygon

from django_mapengine import views

from celery.result import AsyncResult
from celery import uuid
from .tasks import calculate_chargers

from .models import *

import ast


class HomePageView(TemplateView, views.MapEngineMixin):
    template_name = "hpctool.html"

    def post(self, request, **kwargs):
        # change json object to list of lists:
        PL = ast.literal_eval(list(request.POST.dict())[0])

        result = calculate_chargers.apply_async(([PL]), polygon=[PL])

        response = JsonResponse({"error": "there was an error",
                                 "message": "Recalculating with new Polygon... Please wait" + result.get()[0]})
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

    Area = Flurstueck.objects.get(id=int(id))
    # Get all books by the author
    print(Area)

    Station = Area.station_set.first()

    print(Station)

    buslist = Station.busses.count()

    print(buslist)

    html = "<h1> " + str(Station.name)+ "</h1> and ID is " + str(id) + " <br> LADELEISTUNG: 123 "+ str(Station.charge_pwr)+" <br> Number of busses: " + str(buslist)# + str(Area.scenario_ID) + "  "

    return response.JsonResponse({"html": html})  # , "chart": chart}


import json


def generate_json_data():
    # TODO: Put this somewhere else
    # Fetch and process your data here
    #data = [{'hello': 'world'}]  # Your data as a list of dictionaries

    all_instances = Station.objects.all()

    data_list = [{instance.name: {
            "type": "opps",
            "voltage_level": instance.charge_pwr,
            "n_charging_stations": instance.busses.count()}} for instance in all_instances]

    # Initialize an empty dictionary
    dict_of_dicts = {}

    for d in data_list:
        dict_of_dicts.update(d)


    # Convert data to JSON
    json_data = json.dumps(dict_of_dicts)

    return json_data


def export_data(request):
    json_data = generate_json_data()

    # Create an HTTP response with the JSON data as a downloadable file
    response_json = HttpResponse(json_data, content_type='application/json')
    response_json['Content-Disposition'] = 'attachment; filename="electrified_stations.json"'

    return response_json


def create_station(request):
    post_dict = request.POST.dict()
    try:
        # Use ast.literal_eval to evaluate the dictionary
        result = ast.literal_eval(list(request.POST.dict())[0])

        if not result["area"] == "fromAlkis":
            PL = result["area"]['features'][0]['geometry']['coordinates'][0]

            async_result = calculate_chargers.apply_async(([PL]), polygon=[PL])

            stat = Station.objects.create(geom=Point(list(result['latlon'])), name=result['name'], scenario_ID="neu",
                                   charge_pwr=result['power'])

            result, buslist = async_result.get()

            #print("BUUUUUUUUUUUUUSSSSSSSSSSSSSSSSSSSSSS", buslist)

            for busid in buslist:
                bus = BusOutline.objects.get(id=int(busid))
                stat.busses.add(bus)


            stat.flurstück.add(Flurstueck.objects.order_by('id').reverse()[0])
        else:
            print("NÜOT IMPLEMENTED YET :)")
    except ValueError as e:
        print(f"Error: {e}")
        return response.JsonResponse({"message": "Upps, da lief was schief :("})
    return response.JsonResponse({"message": "Jo is erstellt, "+ result})


def get_stationlist(request):
    stations_with_geom = Station.objects.values_list('geom', flat=True)

    # Convert the queryset to a list
    geom_values_list = list(stations_with_geom)
    coordinates_list = [(point.x, point.y) for point in geom_values_list]
    print(coordinates_list)


    return JsonResponse({'message': coordinates_list})
