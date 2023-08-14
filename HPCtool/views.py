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
    template_name = "minimal_mapengine.html"

    # def get(self, request, **kwargs):
    #     return render(request, self.template_name)

    def post(self, request, **kwargs):
        print("POST POST PSOT", request.POST)

        # change json object to list of lists:
        PL = ast.literal_eval(list(request.POST.dict())[0])

        print([PL])

        result = calculate_chargers.apply_async(([PL]),polygon=[PL])

        response = JsonResponse({"error": "there was an error",
                                 "message": "Recalculating with new Polygon... Please wait" + result.get()})
        response.status_code = 200  # oder 400 # To announce that the user isn't allowed to publish

        return response


def delete_bus(request: HttpRequest) -> response.JsonResponse:
    print("Trying to delete this bus: ")

    ToDelete = ast.literal_eval(list(request.POST.dict())[0])

    print(ToDelete)

    for id in ToDelete:
        print("Objects in Database before: ", BusOutline.objects.all().count())
        print("Deleting ID ", int(id))

        BusOutline.objects.get(id=int(id)).delete()

        print("Objects in Database after: ", BusOutline.objects.all().count(), "\n\n")




    #data = apps.get_model(app_label="ebustoolbox", model_name=lookup).get_popup_data(id)
    #try:
    #    html = render_to_string(f"popups/{lookup}.html", context=data)
    #except TemplateDoesNotExist:
    #    html = render_to_string("popups/default.html", context=data)
    return response.JsonResponse({"message": "Successfully deleted! I should implement something to remove the busses from view......"})  # , "chart": chart}