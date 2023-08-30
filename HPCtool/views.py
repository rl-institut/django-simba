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
    #print(to_delete)

    for id in to_delete:
        # print("Objects in Database before: ", BusOutline.objects.all().count())
        # print("Deleting ID ", int(id))
        BusOutline.objects.get(id=int(id)).delete()
        # ("Objects in Database after: ", BusOutline.objects.all().count(), "\n\n")
    return response.JsonResponse({"message": "Successfully deleted! I should implement something "
                                             "to remove the busses from view......"})  # , "chart": chart}
