from django.conf import settings
from django.db.transaction import atomic
from django.http import FileResponse, HttpResponse, JsonResponse, HttpRequest
from django.shortcuts import render, redirect
from django.views.generic import TemplateView
from django.views.decorators.http import require_GET

from django_mapengine.views import MapEngineMixin
from django.db.models import Q

from celery.result import AsyncResult
import plotly.graph_objects as go

# Unused import of dash_app needed to register app
from . import dash_app, tasks  # noqa: F401
from .forms import UploadFileForm
from .util import get_unique_task_id

import ebustoolbox
from ebustoolbox.forms import ChartForm
from ebustoolbox.models import VehicleProperties, Vehicle, Scenario

from django.apps import apps

from django.http import HttpRequest, response
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.views.generic import TemplateView

def get_map(request):
    pass


def get_chart(request):
    """Get a rendered chart of vehicle data

    :param request: django.http.HttpRequest
    :return: django.http.HttpResponse
    """
    task_id = request.GET.get("task_id")
    print("get is :", task_id)
    get_vehicles = request.GET.getlist("vehicles")
    print("vehicles  are :", get_vehicles)

    scenario = Scenario.objects.get(task_id=task_id)
    vehicles = Vehicle.objects.filter(vehicle_type__scenario=scenario)

    # Does the request ask for specific vehicles? If not, don't filter and show all vehicles
    if get_vehicles is None:
        pass
    else:
        my_filter_qs = Q()
        for v in get_vehicles:
            my_filter_qs = my_filter_qs | Q(id=int(v))
        vehicles = vehicles.filter(my_filter_qs)

    plot_vehicles = get_vehicle_plot_data(vehicles)

    fig = go.Figure()
    for v in plot_vehicles:
        fig.add_trace(go.Scatter(x=v["x"], y=v["y"], name=v["name"], line=dict(width=4)))

    fig.update_layout(title={"font_size": 22, "xanchor": "center", "x": 0.5})
    chart = fig.to_html()

    context = {"chart": chart, "form": ChartForm(scenario=scenario), "result_id": task_id}

    return render(request, "chart.html", context)

def get_vehicle_plot_data(vehicles):
    plot_vehicles = []
    for search_vehicle in vehicles:
        plot_data = VehicleProperties.objects.filter(vehicle=search_vehicle)
        time_data = [c.date for c in plot_data]
        y_data = [c.soc for c in plot_data]
        plot_vehicles.append({"x": time_data, "y": y_data, "name": search_vehicle.name})
    return plot_vehicles


def show_uploads_view(request: HttpRequest, filename):
    file = open("uploads/" + filename, "rb")
    response = FileResponse(file)
    return response


def result_view(request: HttpRequest):
    """View controlling if the wait or success view should be shown"""
    task_id = request.GET["task_id"]
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
    """View while waiting for results. Will trigger success view as soon as long-running task
    returns pending"""
    print("SimBA is calculating. Showing wait view")
    return render(request, "wait.html")


class resultView(TemplateView):
    result_template = "result.html"


class SuccessView(TemplateView, MapEngineMixin):
    """View which generates the page containing simulation results"""

    template_name = "result.html"

    def get_context_data(self, **kwargs):
        context = super(SuccessView, self).get_context_data(**kwargs)
        context["task_id"] = self.request.GET["task_id"]
        ##Dash context here????
        context["dash_context"] = {"userid": {"value": "123"}}
        return context


@require_GET
def long_running_task_status_view(request):
    """Returns a Json with a success field. The field is True if the task has finished and
    False if it is still pending"""
    task_id = request.GET.get("task_id")
    task_result = AsyncResult(task_id)
    if (
        task_result.ready()
        or Scenario.objects.filter(task_id=task_id, finished__isnull=False).exists()
    ):
        print("Task is finished")
        return JsonResponse({"success": True})
    print("Task is pending")
    return JsonResponse({"success": False})


def home_view(request: HttpRequest):
    """Generate the home view of the tool chain with input forms"""

    if request.method == "GET":
        form = UploadFileForm()
    elif request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "index.html", {"form": form})

        django_scenario = save_and_simulate(form, request)
        if "ebus_map" in settings.INSTALLED_APPS:
            create_stations_for_map(django_scenario)

        response = redirect("simba:result")
        response["Location"] += "?task_id=" + django_scenario.task_id
        return response
    else:
        return HttpResponse("Method is not allowed", status=405)
    return render(request, "index.html", {"form": form})


@atomic()
def create_stations_for_map(django_scenario: Scenario):
    from ebus_map.models import Station as MapStation

    stations = ebustoolbox.models.Station.objects.filter(scenario=django_scenario)
    map_stations = []
    for station in stations:
        map_stat = MapStation()
        map_stat.__dict__.update(station.__dict__)
        map_stations.append(map_stat)
        map_stat.save()


def save_and_simulate(
    form: UploadFileForm | None = None, request: HttpRequest | None = None
) -> Scenario:
    if form is None:
        new_form = UploadFileForm()
        # If this function is called without a request and a form,  use the initial values as
        # cleaned data
        cleaned_data = {field: new_form[field].initial for field in new_form.fields}
    else:
        cleaned_data = form.cleaned_data

    django_scenario, simba_schedule, args = tasks.input_files_to_database(cleaned_data, request)
    # start computation
    task_id = get_unique_task_id()
    django_scenario.task_id = task_id
    django_scenario.save()
    tasks.run_ebus_toolchain(simba_schedule, args, task_id)
    return django_scenario


def download_scenario(request: HttpRequest, task_id: str):
    file_path = settings.MEDIA_ROOT / (str(task_id) + ".zip")
    if file_path.exists():
        with file_path.open("rb") as fh:
            response = HttpResponse(fh.read(), content_type="application/octet-stream")
            response["Content-Disposition"] = "attachment; filename=" + file_path.name
            return response
    return HttpResponse("Zip not ready yet")


def generate_zip(request: HttpRequest, task_id: str):
    tasks.generate_zipped_scenario(task_id)
    return download_scenario(request, task_id)


def get_popup(request: HttpRequest, lookup: str, id: int) -> response.JsonResponse:  # noqa: ARG001
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

    print("POPUP-", lookup)

    data = apps.get_model(app_label="ebustoolbox", model_name=lookup).get_popup_data(id)
    try:
        html = render_to_string(f"popups/busstop.html", context=data)
    except TemplateDoesNotExist:
        html = render_to_string("popups/default.html", context=data)
    return response.JsonResponse({"html": html})  # , "chart": chart}
