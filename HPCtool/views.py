from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.generic import TemplateView

from django_mapengine import views


# Create your views here.
class HomePageView(TemplateView, views.MapEngineMixin):

    template_name = "minimal_mapengine.html"

   # def get(self, request, **kwargs):
   #     return render(request, self.template_name)

    def post(self, request, **kwargs):
        print("POST POST PSOT", request.POST)

        response = JsonResponse({"error": "there was an error",
                                 "message": "yeeeey"})
        response.status_code = 400 #oder 200 # To announce that the user isn't allowed to publish
        return response

        #return HttpResponse("{'result': 'IS_PASS'}", content_type="application/json") #render(request, self.template_name)
