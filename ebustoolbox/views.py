import os
import warnings

from celery.contrib.migrate import task_id_eq
from django.contrib.gis.db import models
from django.views.generic import TemplateView
from django.http import FileResponse, Http404
from django.conf import settings
import os
import django_mapengine
from ebus_map.views import MinimalMapengineView
# Unused import needed to register app
from . import dash_app
from django.http import HttpResponseRedirect, FileResponse, HttpResponseNotFound, HttpResponse
from django.shortcuts import render, redirect
from .forms import UploadFileForm
from django.http import JsonResponse
from pathlib import Path
from celery.result import AsyncResult
from celery import uuid
from .tasks import run_ebus_toolbox, generate_zipped_scenario
from django.views.decorators.http import require_GET
# Imaginary function to handle an uploaded file.
# from somewhere import handle_uploaded_file
from shutil import copy as file_copy
import time
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario, TaskRun

import plotly.express as px
import plotly.graph_objects as go
from ebustoolbox.forms import ChartForm
from django.db.models import Q


def get_map(request):
    pass


def get_chart(request):
    task_id = request.GET.get("task_id")
    print("get is :", task_id)
    get_vehicles = request.GET.getlist("vehicles")
    print("vehicles  are :", get_vehicles)

    scenario = Scenario.objects.get(id=task_id)
    vehicles = Vehicle.objects.filter(scenario=scenario)
    if get_vehicles is None:
        pass
    else:
        my_filter_qs = Q()
        for v in get_vehicles:
            my_filter_qs = my_filter_qs | Q(id=int(v))
        vehicles = vehicles.filter(my_filter_qs)

    plot_vehicles = []
    for search_vehicle in vehicles:
        plot_data = VehicleProperties.objects.filter(vehicle=search_vehicle)
        time_data = [c.date for c in plot_data]
        y_data = [c.soc for c in plot_data]
        plot_vehicles.append({'x': time_data, 'y': y_data, 'name': search_vehicle.name})

    fig = go.Figure()
    for v in plot_vehicles:
        fig.add_trace(go.Scatter(x=v["x"], y=v['y'], name=v["name"],
                                 line=dict(width=4)))

    fig.update_layout(title={
        'font_size': 22,
        'xanchor': 'center',
        'x': 0.5
    })
    chart = fig.to_html()

    context = {'chart': chart, "form": ChartForm(scenario=scenario), 'result_id': task_id}

    return render(request, 'chart.html', context)


def show_uploads_view(request, filename):
    file = open("uploads/" + filename, 'rb')
    response = FileResponse(file)
    return response


def result_view(request):
    task_id = request.GET['task_id']
    try:
        if TaskRun.objects.get(task_id=task_id).finished:
            return SuccessView.as_view()(request)
        else:
            return wait_view(request)
    except models.ObjectDoesNotExist:
        html = "<html><body>task_id is not valid</body></html>"
        return HttpResponse(html)

def wait_view(request):
    """View while waiting for results. Will trigger success view as soon as long running task returns pending"""
    print("Ebustoolbox is calculating. Showing wait view")
    return render(request, "wait.html")


class resultView(TemplateView):
    result_template = "result.html"


class SuccessView(TemplateView, django_mapengine.views.MapEngineMixin):
    template_name = "result.html"
    def get_context_data(self, **kwargs):
        context = super(SuccessView, self).get_context_data(**kwargs)
        context["task_id"] = self.request.GET["task_id"]
        return context



@require_GET
def long_running_task_status_view(request):
    task_id = request.GET.get('task_id')
    task_result = AsyncResult(task_id)
    if task_result.ready() or TaskRun.objects.get(task_id=task_id).finished:
        print("Task is finished")
        return JsonResponse({'success': True})
    print('Task is pending')
    return JsonResponse({'success': True})


def create_config(model_object):
    warnings.warn("This function is deprecated")
    default_config_path = "uploads/default_config.cfg"
    destination = "uploads/spec_config.cfg"
    file_copy(default_config_path, destination)
    property_names = [field.name for field in model_object._meta.fields]
    model_object.output_directory += str(model_object.id) + "/"

    print(property_names)
    with open(default_config_path, "r") as f:
        with open(default_config_path, "r") as f:
            text = ""
            for line in f:
                possible_variable = line.split(" ")[0]
                print(possible_variable, end="")
                if (possible_variable in property_names and
                        str(model_object.__getattribute__(possible_variable))):
                    line = possible_variable + " = " + \
                           str(model_object.__getattribute__(possible_variable)) + "\n"
                    print(str(model_object.__getattribute__(possible_variable)))
                text += line

    with open(destination, "w") as f:
         f.write(text)

    return destination

def home_view(request, mode_list=["sim", "report"]):
    # ToDo needs different implementation since it uses same list for
    # different users

    if request.method == "GET":
        mode_list[:] = ["sim", "report"]
    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if (request.POST.get("add_mode")):
            if request.POST.get("add_mode") == "remove":
                if len(mode_list) > 1:
                    mode_list.pop(-1)
            else:
                mode_list.append(request.POST.get("add_mode"))

        print("Title is :", request.POST.get("title", "Nuffin"))
        if form.is_valid():
            print("valid")
            form.modes = mode_list
            object = form.save()
            mode_list[:] = ["sim", "report"]

            args = object.to_args()

            task_id_not_unique=True
            task_id = None
            # Create unique ids for as long as needed, so no duplicate ids exist
            while task_id_not_unique:
                try:
                    task_id = uuid()
                    TaskRun.objects.get(task_id=task_id)
                except models.ObjectDoesNotExist:
                    task_id_not_unique=False
            ebus_result = run_ebus_toolbox.apply_async((args, object.id), task_id=task_id)
            task_id = ebus_result.id
            task, _ = TaskRun.objects.get_or_create(task_id=task_id)
            response = redirect('result')
            # Add the scenario as get request
            response['Location'] += '?task_id=' + task_id
            return response
    else:
        form = UploadFileForm()
    return render(request, "index.html", {"form": form, "mode_list": mode_list})

def download_scenario(request, task_id):
    task_id = str(task_id)
    file_path = settings.BASE_DIR / "media" / (task_id+ ".zip")
    if os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type='application/octet-stream')
            response['Content-Disposition'] = 'attachment; filename=' + os.path.basename(file_path)
            return response
    print( "Zip file not found")
    raise Http404

def generate_zip(request, task_id):
    result = generate_zipped_scenario.apply_async((task_id,), task_id=(task_id))
    return HttpResponse("zip generated for ", task_id)