from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.generic import TemplateView

from django_mapengine import views

from celery.result import AsyncResult
from celery import uuid
from .tasks import calculate_chargers

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
