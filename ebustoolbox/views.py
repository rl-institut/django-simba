from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.views.generic import TemplateView
from django.views.decorators.http import require_GET

from django_mapengine.views import MapEngineMixin
from django.db.models import Q

from celery.result import AsyncResult
import plotly.graph_objects as go

# Unused import of dash_app needed to register app
from . import dash_app, tasks
from .forms import UploadFileForm
from .util import get_unique_task_id

import ebustoolbox
from ebustoolbox.forms import ChartForm
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario


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
    """View while waiting for results. Will trigger success view as soon as long running task
    returns pending"""
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
    if task_result.ready() or Scenario.objects.filter(task_id=task_id,
                                                      finished__isnull=False).exists():
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

        django_scenario, simba_schedule, args = \
            tasks.fill_db_with_input_files(form.cleaned_data, request)
        # start computation
        task_id = get_unique_task_id()
        django_scenario.task_id = task_id
        django_scenario.save()
        tasks.run_ebus_toolbox(simba_schedule, args, task_id)
        if "ebus_map" in settings.INSTALLED_APPS:
            from ebus_map.models import Station as MapStation
            stations = ebustoolbox.models.Station.objects.filter(scenario=django_scenario)
            # obj_id = 1 if MapStation.objects.last() is None else MapStation.objects.last().id + 1
            map_stations = []
            for station in stations:
                map_stat = MapStation()
                map_stat.__dict__.update(station.__dict__)
                # map_stat.id = obj_id
                map_stations.append(map_stat)
                map_stat.save()
            # Bulk creation is more efficient but doe not work with multi tabled inherited models
            # MapStation.objects.bulk_create(map_stations)

        response = redirect('simba:result')
        response['Location'] += '?task_id=' + task_id
        return response
    else:
        return HttpResponse("Method not allowed", status=405)
    return render(request, "index.html", {"form": form})


def download_scenario(request, task_id):
    file_path = settings.MEDIA_ROOT / (str(task_id) + ".zip")
    if file_path.exists():
        with file_path.open('rb') as fh:
            response = HttpResponse(fh.read(), content_type='application/octet-stream')
            response['Content-Disposition'] = 'attachment; filename=' + file_path.name
            return response
    return HttpResponse("Zip not ready yet")


def generate_zip(request, task_id):
    tasks.generate_zipped_scenario(task_id)
    return download_scenario(request, task_id)
