from datetime import datetime

from celery.result import AsyncResult
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing, mail
from django.core.exceptions import ObjectDoesNotExist
from django.db.transaction import atomic
from django.http import FileResponse, HttpResponse, JsonResponse, HttpRequest, Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView
from django.views.decorators.http import require_GET, require_POST
from eflips.depot.api import simulate_scenario  # noqa

from core.models import Progress

import ebustoolbox

# Unused import of dash_app needed to register app
from dash_app import dash_app, ids  # noqa: F401
from django_mapengine.views import MapEngineMixin
from . import tasks
from .forms import UploadFileForm, ChargingStationDefaultsForm
from .tasks import create_db_url  # noqa
from .util import get_unique_task_id

from ebustoolbox.models import (
    Scenario,
    UserGroup,
    UploadedFile,
    VehicleType,
    DefaultScenario,
    Station,
)


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


class SuccessView(TemplateView, MapEngineMixin):
    """View which generates the page containing simulation results"""

    template_name = "result.html"

    def get_context_data(self, **kwargs):
        context = super(SuccessView, self).get_context_data(**kwargs)
        task_id = self.request.GET["task_id"]
        context["task_id"] = task_id

        session = self.request.session
        from dash_app.dash_app import create_app

        # By creating a specific app for this task ID, the app "knows" which data to load
        # ToDO make sure only authorized users can view this
        create_app(task_id=task_id)
        # the dictionary in "django_plotly_dash" appears in the session_state of the app, which
        # is an optional kwarg in app.callbacks
        session["django_plotly_dash"] = {"task_id": task_id}

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


def home_prototype(request: HttpRequest):
    """Generate the home view of the tool chain with input forms"""
    task_id = get_unique_task_id()
    return render(request, "home_prototype.html", {"task_id": task_id})


def get_vehicle_types(request: HttpRequest, task_id):
    context = {"task_id": task_id}
    try:
        scenario = Scenario.objects.get(task_id=task_id)
    except Scenario.DoesNotExist:
        raise Http404("Scenario with this task_id does not exist")
    default_scenario = DefaultScenario.objects.first().scenario
    vehicle_types = VehicleType.objects.filter(scenario=scenario)
    default_vehicle_types = VehicleType.objects.filter(scenario=default_scenario)
    context["vehicle_types"] = vehicle_types
    context["default_vehicle_types"] = default_vehicle_types
    return render(request, "vehicle_types.html", context)


def get_stations(request: HttpRequest | None, task_id, form=None):
    if form is None:
        form = ChargingStationDefaultsForm()
    context = {
        "task_id": task_id,
        "form": form
    }
    try:
        scenario = Scenario.objects.get(task_id=task_id)
    except Scenario.DoesNotExist:
        raise Http404("Scenario with this task_id does not exist")
    stations = Station.objects.filter(scenario=scenario)
    context["stations"] = stations
    return render(request, "stations.html", context)


def set_station_values(request: HttpRequest, task_id):

    if request.method == "POST":
        form = ChargingStationDefaultsForm(request.POST)
        if not form.is_valid():
            return get_stations(None, task_id, form)
        station_id_list = request.POST.getlist(key="station_id")
        scenario = Scenario.objects.get(task_id=task_id)
        tasks.electrify_db_stations(scenario, station_id_list)
        # redirect to "simulation overview" page which can start a simulation
        response = redirect(reverse("simba:scenario_overview", args=[str(task_id)]))
        return response
    else:
        return HttpResponse("Method is not allowed", status=405)


def scenario_overview(request: HttpRequest, task_id):
    return render(request, "scenario_overview.html", {"task_id": task_id})


def progress(request: HttpRequest, task_id):
    context = {"progress_id": task_id, "status": "", "current_progress": 0}
    try:
        progress = Progress.objects.get(task_id=task_id)
    except ObjectDoesNotExist:
        response = render(request, "progress.html", context)
        return response
    # context["task_id"] = progress.scenario.task_id
    context["current_progress"] = progress.get_progress()
    context["status"] = progress.status
    status_code = 200
    if progress.success or not progress.running or len(progress.errors) != 0:
        context["errors"] = progress.errors
        # End polling
        status_code = 286
        context["finished"] = True
        # hx_trigger = "notRunning"
    response = render(request, "progress.html", context)
    if context["finished"] and len(context["errors"]) == 0:
        response["HX-Redirect"] = reverse("simba:vehicle_types", args=[str(progress.scenario.task_id)])
    response.status_code = status_code
    return response


@require_POST
def upload_trips(request: HttpRequest, task_id: str):
    try:
        assert len(request.FILES) == 1, "Error: Please provide a single file"
        assert request.FILES["file"].readable(), "Error: File cannot be read"
        file = request.FILES["file"]
        s, _ = Scenario.objects.get_or_create(task_id=task_id)
        uploaded_file = UploadedFile.objects.create(scenario=s, file=file)
        # what kind of file is uploaded
        # errors, success = tasks.init_db_with_trips(uploaded_file.id, s.id)
        async_result = tasks.init_db_with_trips.apply_async((uploaded_file.id, s.id))
        context = {"progress_id": async_result.task_id, "task_id": task_id}

        response = render(request, "progress_poll.html", context)
        response["HX-Trigger"] = "running"
        return response
    except AssertionError as e:
        html = f"<html>{str(e)}</html>"
        return HttpResponse(html)


def assign_vehicle_types(request: HttpRequest, task_id: str):
    if request.method == "POST":
        vehicle_type_pairs = request.POST.getlist("vehicle_type_dropdown")
        tasks.update_vehicle_types_from_dropdown(vehicle_type_pairs, task_id)

    return redirect(reverse("simba:stations", args=[str(task_id)]))


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
    print(f"Running TOOLCHAIN {datetime.now()}")
    if form is None:
        new_form = UploadFileForm()
        # If this function is called without a request and a form,  use the initial values as
        # cleaned data
        cleaned_data = {field: new_form[field].initial for field in new_form.fields}
    else:
        cleaned_data = form.cleaned_data

    print(f"Writing to db {datetime.now()}")
    django_scenario, simba_schedule, args = tasks.input_files_to_database(cleaned_data, request)
    if request.user.is_authenticated:
        django_scenario.manager = request.user
    # start computation
    task_id = get_unique_task_id()
    print(f"{task_id=}")
    django_scenario.task_id = task_id
    django_scenario.save()
    tasks.run_ebus_toolchain(simba_schedule, args, task_id)
    print(f"Simulation Finished {datetime.now()}")
    return django_scenario


def run_simulation(request: HttpRequest, task_id: str):
    if request.method == "POST":
        print(f"Running TOOLCHAIN {datetime.now()}")
        scenario = Scenario.objects.get(task_id=task_id)
        simba_schedule, args = tasks.get_schedule_from_db(scenario)
        tasks.run_ebus_toolchain(simba_schedule, args, task_id)
        print(f"Simulation Finished {datetime.now()}")
        # as a result render simulation result plots in a not yet existing div/tab
        if "ebus_map" in settings.INSTALLED_APPS:
            create_stations_for_map(scenario)

        response = redirect("simba:result")
        response["Location"] += "?task_id=" + scenario.task_id
        return response


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


@login_required(login_url="/login/")
def scenarios(request):
    # show all scenarios of a user. Also endpoint for update and delete (POST)
    if request.method == "POST":
        if "update" in request.POST:
            # manager can update scenario user groups
            scenario = Scenario.objects.get(id=request.POST["update"], manager=request.user)
            usergroups = map(int, request.POST["values"].split(","))
            for ug in request.user.usergroup_set.all():
                if ug.id in usergroups:
                    ug.scenarios.add(scenario)
                elif scenario in ug.scenarios.all():
                    ug.scenarios.remove(scenario)
            return HttpResponse(status=201)  # created
        if "delete" in request.POST:
            Scenario.objects.filter(id=request.POST["delete"], manager=request.user).delete()
    scenarios = Scenario.objects.filter(manager=request.user)
    usergroups = request.user.usergroup_set.all()
    for ug in usergroups:
        scenarios = scenarios.union(ug.scenarios.all())
    scenarios = scenarios.order_by("id")
    return render(request, "scenarios.html", {"scenarios": scenarios})


@login_required(login_url="/login/")
def usergroups(request):
    # manage usergroups of a user. Also endpoint for add and leave (POST)
    if request.method == "POST":
        if "add" in request.POST:
            # TODO: should be a form
            ug = UserGroup.objects.create(
                name=request.POST["name"],
            )
            ug.users.add(request.user)
        elif "invite" in request.POST:
            if settings.EMAIL_BACKEND:
                email = request.POST["email"].lower()
                if User.objects.filter(username=email).exists():
                    return HttpResponse("User already exists", status=409)
                url = f"{request.scheme}://{request.get_host()}{reverse('core:signup')}"
                # generate and append token (embed email, sign with server key)
                url += f"?token={signing.dumps(email)}"
                body = f"Klicken Sie auf folgenden Link, um sich zu registrieren: {url}"
                mail.send_mail(
                    subject="Willkommen zu eBus2030+",
                    message=body,
                    from_email=None,
                    recipient_list=[email],
                    fail_silently=False,
                )
            else:
                raise NotImplementedError("No email backend set")
        elif "leave" in request.POST:
            ug = request.user.usergroup_set.all().get(id=request.POST["leave"])
            ug.users.remove(request.user)
            if not ug.users:
                # delete user group after last one has left
                ug.delete()
    usergroups = request.user.usergroup_set.all()
    return render(request, "usergroups.html", {"usergroups": usergroups})
