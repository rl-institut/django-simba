import logging
import traceback
from datetime import timedelta, datetime, timezone as tz

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing, mail
from django.core.exceptions import ObjectDoesNotExist
from django.http import FileResponse, HttpResponse, JsonResponse, HttpRequest, Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils import timezone
from django.views.generic import TemplateView
from django.views.decorators.http import require_GET, require_POST
from eflips.depot.api import simulate_scenario  # noqa

from core.models import Progress

from celery.result import AsyncResult

# Unused import of dash_app needed to register app
from dash_app import dash_app, ids  # noqa: F401
from django_mapengine.views import MapEngineMixin
from . import tasks, schedule_readers
from .forms import ElectrificationOptionsForm, SimulationParameters, VehicleTypeForm
from .tasks import create_db_url, get_args  # noqa
from .util import get_unique_task_id

import ebustoolbox
from ebustoolbox.models import (
    Scenario,
    UserGroup,
    UploadedFile,
    VehicleType,
    DefaultScenario,
    Station,
    EnumChargeType,
    Rotation,
    Trip,
    SimulationRange,
    DepotSelection,
    ElectrificationOptions,
    VehicleTypeSelection,
    VehicleTypeMutation,
)

logger = logging.getLogger("custom")


def show_uploads_view(request: HttpRequest, filename):
    file = open("uploads/" + filename, "rb")
    response = FileResponse(file)
    return response


def result_view(request: HttpRequest, task_id):
    # View controlling if the wait or success view should be shown
    try:
        if Scenario.objects.get(task_id=task_id).finished:
            request.task_id = str(task_id)
            return SuccessView.as_view()(request, task_id=task_id, finished=True)
        else:
            return wait_view(request, task_id)
    except Scenario.DoesNotExist:
        html = "<html><body>task_id is not valid</body></html>"
        return HttpResponse(html)


def wait_view(request, task_id):
    # View while waiting for results.
    # Will trigger success view as soon as long-running task
    # returns pending
    logger.info("SimBA is calculating. Showing wait view")
    return render(request, "wait.html", {"task_id": task_id})


class SuccessView(TemplateView, MapEngineMixin):
    # View which generates the page containing simulation results

    template_name = "result.html"

    def get_context_data(self, **kwargs):
        context = super(SuccessView, self).get_context_data(**kwargs)
        task_id = kwargs.get("task_id")
        if task_id is None:
            raise Http404
        task_id = str(task_id)
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
    # Returns a Json with a success field.
    # The field is True if the task has finished and False if it is still pending
    task_id = request.GET.get("task_id")
    task_result = AsyncResult(task_id)
    if (
        task_result.ready()
        or Scenario.objects.filter(task_id=task_id, finished__isnull=False).exists()
    ):
        logger.info("Task is finished")
        return JsonResponse({"success": True})
    logger.info("Task is pending")
    return JsonResponse({"success": False})


def schedule(request: HttpRequest, task_id, finished):
    # Generate the home view of the tool chain with input forms
    # Schedule uploading triggers a progress which will send finished="true" if the upload finished
    if finished == "true":
        scenario = Scenario.objects.get(task_id=task_id)
        task_id = scenario.task_id
        scenario.task_id = ebustoolbox.util.get_unique_task_id()
        scenario.save()
        _ = tasks.create_empty_child_scenario(scenario, task_id=task_id)
        scenario.name = "Parent of " + scenario.name
        scenario.save()
        return redirect(reverse("simba:simulation_parameters", args=[str(task_id)]))
    if task_id is None:
        task_id = get_unique_task_id()
    context = {
        "task_id": task_id,
    }
    return render(request, "schedule.html", context)


def get_simulation_parameters(request: HttpRequest, task_id):
    # Generate the home view of the tool chain with input forms
    try:
        scenario = Scenario.objects.get(task_id=task_id)
        parent = scenario.parent

    except Scenario.DoesNotExist:
        raise Http404
    # if the scenario has a manager, only this User can run the simulation
    if scenario.manager and scenario.manager != request.user:
        raise Http404
    context = {}
    simulation_parameters_form = SimulationParameters()
    if request.method == "POST":
        simulation_parameters_form = SimulationParameters(request.POST)
        if simulation_parameters_form.is_valid():
            date_range = simulation_parameters_form.cleaned_data["date_range"]
            from_date, to_date = date_range  # Unpack the tuple
            time_delta = timedelta(days=(to_date - from_date).days + 1)
            from_datetime = datetime.combine(from_date, datetime.min.time())
            from_datetime_aware = timezone.make_aware(from_datetime, timezone=tz.utc)

            if tasks.get_rotations_by_timespan(parent, time_delta, from_datetime_aware).count() > 0:
                sim_range = SimulationRange.objects.filter(scenario=scenario).first()
                if sim_range is None:
                    sim_range = SimulationRange(scenario=scenario)
                sim_range.start = from_datetime_aware
                sim_range.end = from_datetime_aware + time_delta
                sim_range.save()
                return redirect(reverse("simba:vehicle_types", args=[str(task_id)]))
            error = "Zeitspanne enthält keine Umläufe."
            context["error"] = error
        else:
            logging.warning("Simulation parameters are invalid")

    trips = Trip.objects.filter(scenario=parent).order_by("departure_time")
    start = trips.first().departure_time.date().isoformat()
    end = trips.last().arrival_time.date().isoformat()
    sim_range = SimulationRange.objects.filter(scenario=scenario).first()
    if sim_range:
        initial_start = sim_range.start.date().isoformat()
        initial_end = (sim_range.end - timedelta(days=1)).isoformat()
    else:
        initial_start = start
        initial_end = end
    context |= {"min_date": start, "max_date": end}
    context |= {"start_date": initial_start, "end_date": initial_end}
    context |= {"task_id": task_id, "form": simulation_parameters_form}
    return render(request, "simulation_parameters.html", context)


def get_options(request: HttpRequest, task_id, reader_num: int):
    context = {
        "reader_num": reader_num,
        "task_id": task_id,
        "max_file_size_b": settings.MAX_FILE_SIZE_B,
    }
    response = HttpResponse(context)
    try:
        form = schedule_readers.get_options_form(reader_num)()
        context |= {"form": form}
        response = render(request, "schedule_reader_options.html", context)
    except:  # noqa
        logger.error(traceback.format_exc())
        # 204 - No Content https://htmx.org/docs/#requests
        response.status_code = 204
    return response


def get_vehicle_types(request: HttpRequest, task_id):
    context = {"task_id": task_id}

    try:
        scenario = Scenario.objects.get(task_id=task_id)
        parent = scenario.parent
    except Scenario.DoesNotExist:
        raise Http404
    # if the scenario has a manager, only this User can run the simulation
    if scenario.manager and scenario.manager != request.user:
        raise Http404

    default_scenario = DefaultScenario.objects.first().scenario
    vehicle_types = VehicleType.objects.filter(scenario=parent)
    # Get all default vehicle types. Only Opportunity charging capable for now
    default_vehicle_types = VehicleType.objects.filter(
        scenario=default_scenario, opportunity_charging_capable=True
    )

    # if the child / mutation scenario has no vehicle types create them
    child_vehicle_types = VehicleType.objects.filter(scenario=scenario)
    if child_vehicle_types.count() == 0:
        for vt in vehicle_types:
            org_vt_id = vt.id
            vt.id = None
            vt.scenario = scenario
            vt.save()
            org_vt = VehicleType.objects.get(id=org_vt_id)
            VehicleTypeMutation.objects.create(
                original_vehicle_type=org_vt, mutated_vehicle_type=vt
            )

    context["vehicle_types"] = vehicle_types
    # Create form for every selection of a default vehicle type
    from django.forms import modelformset_factory

    VehicleTypeSelectionFormSet = modelformset_factory(
        VehicleTypeSelection, fields=["default_vehicle_type"], extra=0
    )
    for vt in child_vehicle_types:
        vt_select, _ = VehicleTypeSelection.objects.get_or_create(vehicle_type=vt)

    formset_vt = VehicleTypeSelectionFormSet(
        queryset=VehicleTypeSelection.objects.filter(vehicle_type__in=child_vehicle_types),
        prefix="dvt_selection",
    )

    # Make the choice of the default vehicle type visible
    for form in formset_vt:
        form.fields["default_vehicle_type"].queryset = default_vehicle_types
        form.fields["default_vehicle_type"].widget.attrs["type"] = "visible"
    context["formset_vt"] = formset_vt

    # Form for every default vehicle type to allow mutation of default vehicle values
    VehicleTypeFormSet = modelformset_factory(VehicleType, form=VehicleTypeForm, extra=0)
    formset_dvt = VehicleTypeFormSet(
        queryset=VehicleType.objects.filter(id__in=default_vehicle_types), prefix="dvt_mutation"
    )
    context["formset_dvt"] = formset_dvt
    context["default_vehicle_types"] = default_vehicle_types

    # check if can be skipped by seeing if vehicle types have relevant data
    skippable = reverse("simba:depots", args=[str(task_id)])
    for vt in vehicle_types:
        if vt.consumption is None:
            skippable = False
            break
    context["skippable"] = skippable

    if request.method == "POST":
        vt_type_mutations = VehicleTypeFormSet(request.POST, prefix="dvt_mutation")
        vt_selection = VehicleTypeSelectionFormSet(request.POST, prefix="dvt_selection")
        if not vt_type_mutations.is_valid() or not vt_selection.is_valid():
            return render(request, "vehicle_types.html", context)

        vt_type_mutations = {form.instance.id: form.instance for form in vt_type_mutations}
        vt_selection.save()

        # Change every vehicle to the selected default vehicle type properties
        for form in vt_selection:
            vt, dvt = form.instance.vehicle_type, form.instance.default_vehicle_type
            changed_dvt = vt_type_mutations[dvt.id]
            # overwrite mutation vt with the new values of the mutated default vehicle type
            changed_dvt.id = vt.id
            # Restore some values from original vt
            changed_dvt.name = vt.name
            changed_dvt.scenario = vt.scenario
            changed_dvt.name_short = vt.name_short
            changed_dvt.save()
        return redirect(reverse("simba:depots", args=[str(task_id)]))

    return render(request, "vehicle_types.html", context)


def get_depots(request: HttpRequest, task_id):
    # View for the depot input tab.
    # Either continues to next wizard step or renders depot page.
    context = {"task_id": task_id}
    try:
        scenario = Scenario.objects.get(task_id=task_id)
        parent = scenario.parent
    except Scenario.DoesNotExist:
        raise Http404
    # if the scenario has a manager, only this User can run the simulation
    if scenario.manager and scenario.manager != request.user:
        raise Http404

    # Get filtered depots by simrange
    sim_range = SimulationRange.objects.filter(scenario=scenario).first()
    if sim_range:
        # If a simulation range is given, only allow filtering of depots which service rotations
        assert SimulationRange.objects.filter(scenario=scenario).count() == 1
        td = sim_range.end - sim_range.start
        rots = tasks.get_rotations_by_timespan(parent, td, sim_range.start)
        station_ids = (
            Trip.objects.filter(rotation__in=rots)
            .values_list("route__departure_station", "route__arrival_station")
            .distinct()
        )
        station_ids = set(x for pair in station_ids for x in pair)
        depots_query = Station.objects.filter(id__in=station_ids)
    else:
        depots_query = Station.objects.filter(scenario=parent)

    depots_query = depots_query.filter(charge_type=EnumChargeType.DEPOT).order_by("id")
    if request.method == "POST":
        depot_ids = [dep.id for dep in depots_query]
        dep_sel, _ = DepotSelection.objects.get_or_create(scenario=scenario)
        if len(depot_ids) == 1:
            dep_sel.depots.add(*depot_ids)
            return redirect(reverse("simba:electrification", args=[str(task_id)]))
        if len(depot_ids) > 1:
            selected_depot_ids = []
            for dep in depots_query:
                if request.POST.get(f"sim_depot_{dep.id}") == "on":
                    selected_depot_ids.append(dep.id)
            if len(selected_depot_ids) >= 1:
                dep_sel.depots.add(*depot_ids)
                # tasks.trim_depots(scenario, depots_to_remove)
                return redirect(reverse("simba:electrification", args=[str(task_id)]))
        context["error"] = "Wähle mindestens ein Depot aus."
        context["depots"] = depots_query
        return render(request, "depots.html", context)
    else:
        depots = (
            depots_query.filter(scenario=parent)
            .filter(charge_type=EnumChargeType.DEPOT)
            .order_by("id")
        )
        context["depots"] = depots
        return render(request, "depots.html", context)


def get_electrification(request: HttpRequest, task_id):
    try:
        scenario = Scenario.objects.get(task_id=task_id)
        parent = scenario.parent
    except Scenario.DoesNotExist:
        raise Http404
        # if the scenario has a manager, only this User can run the simulation
    if scenario.manager and scenario.manager != request.user:
        raise Http404
    electrification_option = ElectrificationOptions.objects.filter(scenario=scenario).first()
    if electrification_option is None:
        electrification_option = ElectrificationOptions.objects.create(
            scenario=scenario, station_optimization=False
        )
        electrification_option.electrified_stations.add(
            *list(
                Station.objects.filter(
                    scenario=parent, is_electrified=True, charge_type=EnumChargeType.OPPORTUNITY
                )
            )
        )
        electrification_option.save()
    form = ElectrificationOptionsForm(instance=electrification_option)
    context = {"task_id": task_id, "form": form}
    stations = (
        Station.objects.filter(scenario=parent)
        .exclude(charge_type=EnumChargeType.DEPOT)
        .order_by("id")
    )
    opp_count = Rotation.objects.filter(scenario=parent).filter(allow_opportunity_charging=True)
    is_depot_scenario = True if len(opp_count) == 0 else False
    context["stations"] = stations
    context["electrified_stations"] = electrification_option.electrified_stations.all()
    context["is_depot_scenario"] = is_depot_scenario

    if request.method == "GET":
        return render(request, "input_electrification.html", context)
    elif request.method == "POST":
        if is_depot_scenario:
            response = redirect(reverse("simba:scenario_overview", args=[str(task_id)]))
            return response

        form = ElectrificationOptionsForm(request.POST, instance=electrification_option)
        context["form"] = form
        if not form.is_valid():
            return render(request, "input_electrification.html", context)

        station_id_list = request.POST.getlist(key="station_id")
        electrified_stations = Station.objects.filter(scenario=parent, id__in=station_id_list)
        ElectrificationOptions.objects.filter(scenario=scenario).delete()
        ele_option = form.save()
        ele_option.electrified_stations.add(*electrified_stations)
        ele_option.save()

        # redirect to "simulation overview" page which can start a simulation
        response = redirect(reverse("simba:scenario_overview", args=[str(task_id)]))
        return response
    else:
        return HttpResponse("Method is not allowed", status=405)


def scenario_overview_view(request: HttpRequest, task_id, finished=None):
    # View controlling if the wait or success view should be shown
    try:
        scenario = Scenario.objects.get(task_id=task_id)
    except Scenario.DoesNotExist:
        raise Http404
        # if the scenario has a manager, only this User can run the simulation
    if scenario.manager and scenario.manager != request.user:
        raise Http404

    if finished == "true":
        return redirect(reverse("simba:result", args=[task_id]))

    try:
        if not scenario.finished:
            request.task_id = str(task_id)
            session = request.session
            from dash_app.dash_app import create_app

            # By creating a specific app for this task ID, the app "knows" which data to load
            # ToDO make sure only authorized users can view this
            create_app(task_id=str(task_id))
            # the dictionary in "django_plotly_dash" appears in the session_state of the app, which
            # is an optional kwarg in app.callbacks
            session["django_plotly_dash"] = {"task_id": str(task_id)}

            response = ScenarioOverview.as_view()(request, task_id=task_id, finished=False)

            # Setting Cache-Control header
            patch_cache_control(response, no_cache=True, no_store=True, must_revalidate=True)
            return response
        else:
            url = reverse("simba:result", args=[task_id])
            duration = 2
            content = "This scenario has already been simulated."
            return render(
                request,
                "redirect_timer.html",
                {"content": content, "duration": duration, "redirect_url": url},
            )

    except Scenario.DoesNotExist:
        html = "<html><body>task_id is not valid</body></html>"
        return HttpResponse(html)


class ScenarioOverview(TemplateView, MapEngineMixin):
    template_name = "scenario_overview.html"

    def get_context_data(self, **kwargs):
        context = super(ScenarioOverview, self).get_context_data(**kwargs)
        task_id = kwargs.get("task_id")
        if task_id is None:
            raise Http404
        task_id = str(task_id)
        context["task_id"] = task_id

        return context


def progress(request: HttpRequest, progress_id, progress_type: str):
    context = {"progress_id": progress_id, "status": "", "current_progress": 0}
    context |= {"finished": False}
    try:
        progress = Progress.objects.get(task_id=progress_id)
    except ObjectDoesNotExist:
        response = render(request, "progress.html", context)
        return response

    context["current_progress"] = max(progress.get_progress(), 1)
    context["status"] = progress.status
    status_code = 200
    hx_trigger = "running"
    if progress.success or not progress.running or len(progress.errors) != 0:
        context["errors"] = progress.errors
        # End polling
        status_code = 286
        context["finished"] = True
        hx_trigger = "notRunning"
    response = render(request, "progress.html", context)
    if context["finished"] and len(context["errors"]) == 0:
        task_id = progress.scenario.task_id
        response["HX-Redirect"] = reverse(progress_type, args=[task_id, "true"])
    response["HX-Trigger"] = hx_trigger
    response.status_code = status_code
    return response


@require_POST
def upload_trips(request: HttpRequest, task_id: str, reader_num: int):
    context = {"task_id": task_id, "progress_type": "simba:schedule"}
    try:
        form = schedule_readers.get_options_form(reader_num)(request.POST, request.FILES)
        # set in TimezoneMiddleware in core.middleware
        now = timezone.localtime()
        now_str = now.strftime(format="%Y-%m-%d %H:%M")
        scenario_name = request.POST.get("scenario_name")
        if scenario_name == "":
            scenario_name = f"Mein Szenario vom {now_str}"
        if not form.is_valid():
            context = {
                "form": form,
                "task_id": task_id,
                "reader_num": reader_num,
                "max_file_size_b": settings.MAX_FILE_SIZE_B,
            }
            response = render(request, "schedule_reader_options.html", context)
            response["HX-Retarget"] = "#options_form"
            return response
        s, _ = Scenario.objects.get_or_create(task_id=task_id)
        s.name = scenario_name
        if request.user.is_authenticated:
            s.manager = request.user

        s.save()

        cleaned_data = form.cleaned_data

        # check sum of file sizes
        if sum([f.size for f in request.FILES.values()]) > settings.MAX_FILE_SIZE_B:
            raise Exception("Upload zu groß")

        files = dict()
        for name, file in request.FILES.items():
            # check file size
            if file.size > settings.MAX_FILE_SIZE_B:
                # file too large
                raise Exception("Datei zu groß")
            uploaded_file = UploadedFile.objects.create(scenario=s, file=file)
            files[name] = uploaded_file.file.path, uploaded_file.id
            del cleaned_data[name]
        # what kind of file is uploaded
        async_result = tasks.init_db_with_trips.apply_async((s.id, reader_num, files, cleaned_data))
        context["progress_id"] = async_result.task_id

        response = render(request, "progress_poll.html", context)
        response["HX-Trigger"] = "running"
        return response
    except AssertionError as e:
        html = f"<html>{str(e)}</html>"
        response = HttpResponse(html)
        return response
    except Exception as e:
        html = f"<html>{str(e)}</html>"
        response = HttpResponse(html)
        response["HX-Trigger"] = "notRunning"
        return response


@require_POST
def cancel_upload(request: HttpRequest, task_id: str):
    # cause a SoftTimeLimitExceeded in task and redirect to schedule upload
    AsyncResult(task_id).revoke(terminate=True, signal="SIGUSR1")
    return redirect(reverse("simba:schedule"))


def landing_page(request: HttpRequest):
    return render(request, "landing_page.html")


def copy_scenario(request: HttpRequest, task_id: str):
    try:
        scenario = Scenario.objects.get(task_id=task_id)
    except Scenario.DoesNotExist:
        raise Http404
    # if the scenario has a manager, only this User can run the simulation
    if scenario.manager and scenario.manager != request.user:
        raise Http404
    try:
        copied_scenario = tasks.create_scenario_copy_for_user(scenario)
    except AssertionError:
        raise Http404
    print(copied_scenario.task_id)
    response = redirect(reverse("simba:scenario_overview", args=[str(copied_scenario.task_id)]))
    return response


def run_simulation(request: HttpRequest, task_id: str):
    context = {"task_id": task_id, "progress_type": "simba:scenario_overview"}
    logger.debug(context)
    response = HttpResponse(context)

    try:
        if request.method == "GET":
            try:
                scenario = Scenario.objects.get(task_id=task_id)
                parent = scenario.parent
            except Scenario.DoesNotExist:
                raise Http404
            # if the scenario has a manager, only this User can run the simulation
            if scenario.manager and scenario.manager != request.user:
                raise Http404
            # This triggers progress polling. If the toolchain is finished,
            # the progress view will be triggered with the task_id and progress type
            logger.info("Running Toolchain.")

            sim_task_id = get_unique_task_id()
            # create scenario from mutation and parent and simulate it
            async_result = tasks.run_and_merge_scenarios.apply_async(
                (parent.id, scenario.id, sim_task_id), task_id=str(sim_task_id)
            )
            context["task_id"] = sim_task_id
            context["progress_id"] = async_result.task_id
            response = render(request, "progress_poll.html", context)
            response["HX-Trigger"] = "running"
    except Exception:
        logger.error(traceback.format_exc())
        response["HX-Trigger"] = "notRunning"
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

import random
import time
from django.shortcuts import render
import plotly.graph_objs as go
import plotly.offline as pyo
import plotly.graph_objects as go
import numpy as np
from plotly_resampler import FigureResampler, FigureWidgetResampler

def generate_random_data(points):
    """Generate random data for scatter plots."""
    return [{'x': random.random(), 'y': random.random()} for _ in range(points)]

def render_test(request, option, points):
    # Start server-side timer
    server_start_time = time.time()

    # Generate random data
    data = generate_random_data(points)

    if option in ['plotly', 'plotlygl']:
        # Extract x and y values from the data
        x = [point['x'] for point in data]
        y = [point['y'] for point in data]

        # Create a Plotly trace
        if option == 'plotlygl':
            trace = go.Scattergl(  # Use Scattergl for WebGL rendering
                x=x,
                y=y,
                mode='markers'
            )
        else:  # option == 'plotly'
            trace = go.Scatter(  # Use Scatter for SVG rendering
                x=x,
                y=y,
                mode='markers'
            )

        # Create layout
        layout = go.Layout(
            title=f'{option.upper()} Scatter Plot ({points} Points)',
            width=800,
            height=400
        )

        # Generate Plotly HTML
        fig = go.Figure(data=[trace], layout=layout)
        plot_div = pyo.plot(fig, output_type='div', include_plotlyjs=False)

        # Pass the Plotly HTML to the template
        server_end_time = time.time()
        server_time = (server_end_time - server_start_time) * 1000  # Convert to ms
        return render(request, 'ebustoolbox/render_test.html', {
            'option': option,
            'points': points,
            'plot_div': plot_div,
            'server_time': server_time
        })

    elif option == 'chartjs':
        # Pass data to the template for Chart.js
        server_end_time = time.time()
        server_time = (server_end_time - server_start_time) * 1000  # Convert to ms
        return render(request, 'ebustoolbox/render_test.html', {
            'option': option,
            'points': points,
            'data': data,
            'server_time': server_time
        })

    elif option == 'echarts':
        # Pass data to the template for ECharts
        server_end_time = time.time()
        server_time = (server_end_time - server_start_time) * 1000  # Convert to ms
        return render(request, 'ebustoolbox/render_test.html', {
            'option': option,
            'points': points,
            'data': data,  # Pass the raw data to the template
            'server_time': server_time
        })

    elif option == "plotlyglr":

        # Extract x and y values from the data
        x = [point['x'] for point in data]
        y = [point['y'] for point in data]

        fig = FigureResampler(go.Figure())
        fig.add_trace(go.Scattergl(name='noisy sine', showlegend=True), hf_x=x, hf_y=y)

        # Create layout
        layout = go.Layout(
            title=f'{option.upper()} Scatter Plot ({points} Points)',
            width=800,
            height=400
        )

        fig.show_dash(mode='inline')
        # Generate Plotly HTML
        plot_div = pyo.plot(fig, output_type='div', include_plotlyjs=False)

        # Pass the Plotly HTML to the template
        server_end_time = time.time()
        server_time = (server_end_time - server_start_time) * 1000  # Convert to ms
        return render(request, 'ebustoolbox/render_test.html', {
            'option': option,
            'points': points,
            'plot_div': plot_div,
            'server_time': server_time
        })



    else:
        # Handle invalid options
        return render(request, 'ebustoolbox/render_test.html', {
            'error': f"Invalid option: {option}"
        })
