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

import json



class HomePageView(TemplateView, views.MapEngineMixin):
    template_name = "hpctool.html"

    def post(self, request, **kwargs):
        # change json object to list of lists:
        PL = ast.literal_eval(list(request.POST.dict())[0])

        settings_object = Settings.objects.get(scenario_ID="neu")
        bl = settings_object.bus_length
        pd = settings_object.park_distance

        result = calculate_chargers.apply_async(polygon=[PL], buslength=bl, parkdistance=pd)

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

    dictresult = {"name": Station.name,
                  "id": Station.id,
                  "pwr": Station.charge_pwr,
                  "buses": buslist}

    return response.JsonResponse(dictresult)  # , "chart": chart}

def edit_station(request):

    edit_id = request.POST.get('data')["id"]

    stat = Station.objects.get(id=int(edit_id))

    stat.name = request.POST.get('data')["name"]
    stat.charge_pwr = request.POST.get('data')["charge_pwr"]

    stat.save()

    return JsonResponse({"message":"Success=1"})


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

        settings_object = Settings.objects.get(scenario_ID="neu")
        bl = settings_object.bus_length
        pd = settings_object.park_distance

        if not result["area"] == "FromAlkis":

            PL = result["area"]['features'][0]['geometry']['coordinates'][0]

        else:
            PL = [(result["latlon"][0], result["latlon"][1])]

        print("HERE")

        async_result = calculate_chargers.apply_async(([PL, bl, pd]), polygon=[PL], buslength=bl, parkdistance=pd)

        _, buslist = async_result.get()

        stat = Station.objects.create(geom=Point(list(result['latlon'])), name=result['name'], scenario_ID="neu",
                                      charge_pwr=result['power_station'])

        stat.flurstück.add(Flurstueck.objects.order_by('id').reverse()[0])

        for busid in buslist:
            bus = BusOutline.objects.get(id=int(busid))
            stat.busses.add(bus)









    except ValueError as e:
        print(f"Error: {e}")
        return response.JsonResponse({"message": "Upps, da lief was schief :("})
    return response.JsonResponse({"message": "Jo is erstellt, "})


def get_stationlist(request):
    stations_with_geom = Station.objects.values_list('geom', flat=True)

    # Convert the queryset to a list
    geom_values_list = list(stations_with_geom)
    coordinates_list = [(point.x, point.y) for point in geom_values_list]
    print(coordinates_list)


    return JsonResponse({'message': coordinates_list})


def get_settings(request):


    if request.method == 'GET':
        try:
            settings_object = Settings.objects.get(scenario_ID="neu")
        except Settings.DoesNotExist:
            settings_object = Settings.objects.create(name="Settings", scenario_ID="neu", bus_length=18,
                                                      park_distance=5, max_curvature=1)
        except Settings.MultipleObjectsReturned:
            print("Error")

        print(settings_object)

        return JsonResponse({"bus_length": settings_object.bus_length,
                             "parkdistance": settings_object.park_distance,
                             "maxcurvature": 1})

    elif request.method == 'POST':
        # Handle POST request here
        data = request.POST.get('data')

        key = next(iter(request.POST))
        values_dict = json.loads(key)

        # Access the values
        bus_length = values_dict.get('buslength')
        park_distance = values_dict.get('parkdistance')

        settings_object = Settings.objects.get(scenario_ID="neu")
        settings_object.bus_length = bus_length
        settings_object.park_distance = park_distance
        settings_object.max_curvature = 2

        # Save the changes to the database
        settings_object.save()
        #
        return HttpResponse(f'This is a POST request with data: {data}')


def crit(request):

    if request.method == 'GET':
        settingse = Settings.objects.get(scenario_ID="neu")

        all_crit = settingse.criteria.all()

        if settingse.criteria.count() < 1:
            crit = Criterion.objects.create(scenario_ID="neu", name="Bäume", layer_name="Bäume", geom_type="point", link="""https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_wfs_baumbestand""", dist_green=5, dist_red=10)
            settingse.criteria.add(crit)
            crit = Criterion.objects.create(scenario_ID="neu", name="Wohngebäude", layer_name='extractWohngebiet', geom_type="poly", link="""https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_wfs_alkis_tatsaechlichenutzungflaechen""", dist_green=30, dist_red=60)
            settingse.criteria.add(crit)
            crit = Criterion.objects.create(scenario_ID="neu", name="Radwege", layer_name="Radweg", geom_type="point", link="""https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_Radweg""", dist_green=5, dist_red=10)
            settingse.criteria.add(crit)


        criterialist = []

        for criteria_obj in all_crit:
            new_dict = {"name": criteria_obj.name,
                        "layer_name": criteria_obj.layer_name,
                        "geom_type": criteria_obj.geom_type,
                        "link": criteria_obj.link,
                        "dist_red": criteria_obj.dist_red,
                        "dist_green": criteria_obj.dist_green,
                        }
            print(criteria_obj.name)
            criterialist.append(new_dict)

        return response.JsonResponse(criterialist, safe=False)

    elif request.method == 'POST':
        # Handle POST request here
        data = request.POST.get('data')
        #

        #

        return HttpResponse(f'This is a POST request with data: {data}')

