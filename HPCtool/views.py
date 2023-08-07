from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.generic import TemplateView

from django_mapengine import views

from celery.result import AsyncResult
from celery import uuid
from .tasks import calculate_chargers


# Create your views here.
class HomePageView(TemplateView, views.MapEngineMixin):
    template_name = "minimal_mapengine.html"

    # def get(self, request, **kwargs):
    #     return render(request, self.template_name)

    def post(self, request, **kwargs):
        print("POST POST PSOT", request.POST)

        result = calculate_chargers.apply_async()

        response = JsonResponse({"error": "there was an error",
                                 "message": "Recalculating with new Polygon... Please wait" + result.get()})
        response.status_code = 200  # oder 400 # To announce that the user isn't allowed to publish

        return response
