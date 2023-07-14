from django.conf import settings
from django.core import signing
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from celery.result import AsyncResult

from decimal import Decimal
from pathlib import Path
import plotly.graph_objects as go
from shutil import copy as file_copy
import warnings
from django_mapengine.views import MapEngineMixin
# Unused import needed to register app
from . import dash_app, tasks, util
from .forms import UploadFileForm
from django.http import JsonResponse
from pathlib import Path
from celery.result import AsyncResult
from celery import uuid
# from .tasks import run_ebus_toolbox, generate_zipped_scenario
from django.views.decorators.http import require_GET
# Imaginary function to handle an uploaded file.
# from somewhere import handle_uploaded_file
from shutil import copy as file_copy
import time
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario, UploadedFile

import plotly.express as px
import plotly.graph_objects as go
from ebustoolbox.forms import ChartForm
from django.db.models import Q

from .util import get_unique_task_id


def get_map(request):
    pass


def get_chart(request):
    task_id = request.GET.get("task_id")
    print("get is :", task_id)
    get_vehicles = request.GET.getlist("vehicles")
    print("vehicles  are :", get_vehicles)

    scenario = Scenario.objects.get(task_id=task_id)
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
        print(task_id, Scenario.objects.filter(task_id=task_id).exists())
        if Scenario.objects.get(task_id=task_id).finished:
            return SuccessView.as_view()(request)
        else:
            return wait_view(request)
    except Scenario.DoesNotExist:
        html = "<html><body>task_id is not valid</body></html>"
        return HttpResponse(html)

def wait_view(request):
    """View while waiting for results. Will trigger success view as soon as long running task returns pending"""
    print("Ebustoolbox is calculating. Showing wait view")
    return render(request, "wait.html")


class resultView(TemplateView):
    result_template = "result.html"


class SuccessView(TemplateView, MapEngineMixin):
    template_name = "result.html"
    def get_context_data(self, **kwargs):
        context = super(SuccessView, self).get_context_data(**kwargs)
        context["task_id"] = self.request.GET["task_id"]
        return context



@require_GET
def long_running_task_status_view(request):
    task_id = request.GET.get('task_id')
    task_result = AsyncResult(task_id)
    if task_result.ready() or Scenario.objects.filter(task_id=task_id, finished__isnull=False).exists():
        print("Task is finished")
        return JsonResponse({'success': True})
    print('Task is pending')
    return JsonResponse({'success': True})


def home_view(request):
    # ToDo needs different implementation since it uses same list for
    # different users

    if request.method == "GET":
        form = UploadFileForm()
    elif request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "index.html", {"form": form})

        scenario = Scenario.objects.create(name=form.cleaned_data["title"])
        args = dict(form.cleaned_data)
        args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(',')))
        # decimal -> float
        for k, v in args.items():
            if type(v) == Decimal:
                args[k] = float(v)
        # set default files if not given
        for k,v in {
                "input_schedule": "trips_example.csv",
                "electrified_stations": "electrified_stations.json",
                "vehicle_types": "vehicle_types.json",
                "station_data_path": "all_stations.csv",
                "outside_temperature_over_day_path": "default_temp_summer.csv",
                "level_of_loading_over_day_path": "default_level_of_loading_over_day.csv",
                "cost_parameters_file": "cost_params.json",
        }.items():
            if args[k]:
                # uploaded file: store in upload folder
                f = UploadedFile.objects.create(scenario=scenario, file=request.FILES[k])
                args[k] = f.file.path
                continue
            p = Path(settings.STATIC_URL, __package__, "examples", v)
            if settings.DEBUG:
                # use app static folder
                if p.is_absolute():
                    # remove first slash
                    p = Path(str(p)[1:])
                p = Path(settings.BASE_DIR, __package__, p)
            if not p.exists():
                print(f"FILE ERROR: {k} COULD NOT BE SET ({str(p)})")
                continue
            args[k] = str(p)

        scenario.options = args
        scenario.save()

        response = redirect('result')
        # start computation
        task_id = get_unique_task_id()
        scenario.task_id = task_id
        scenario.save()
        tasks.run_ebus_toolbox(args, task_id)

        response['Location'] += '?task_id=' + task_id
        return response
    else:
        return HttpResponse("Method not allowed", status=405)
    return render(request, "index.html", {"form": form})


def download_scenario(request, task_id):
    file_path = settings.MEDIA_ROOT / (task_id + ".zip")
    if file_path.exists():
        with file_path.open('rb') as fh:
            response = HttpResponse(fh.read(), content_type='application/octet-stream')
            response['Content-Disposition'] = 'attachment; filename=' + file_path.name
            return response
    return HttpResponse("Zip not ready yet")

def generate_zip(request, task_id):
    if settings.CELERY_BROKER_URL:
        tasks.generate_zipped_scenario.apply_async((task_id,), task_id=(task_id))
    else:
        util.generate_zipped_scenario(task_id)
        return download_scenario(request, task_id)
    return HttpResponse("zip generated for ", task_id)
