import logging
import traceback
import dateutil.parser as parser
import datetime

from django.conf import settings
from django.core.exceptions import PermissionDenied
import pytz
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing, mail, serializers
from django.db.models import F, QuerySet, Sum, Value, FloatField, Q, Avg
from django.db.models.functions import Cast, Coalesce
from django.db.transaction import atomic
from django.utils.translation import gettext as _
from django.http import (
    HttpResponse,
    HttpRequest,
    Http404,
    HttpResponseForbidden,
    JsonResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, FormView, ListView
from rest_framework.parsers import JSONParser  # noqa

from simba.ids import INCLINE, LEVEL_OF_LOADING, SPEED, T_AMB  # noqa

from core.models import Progress, EnumProgress
import core.deepcopy
from celery.result import AsyncResult

from django_mapengine.views import MapEngineMixin
from temperatures.models import WeatherStation  # noqa
from . import tasks, forms
import temperatures.tasks
from .forms import (
    ChargingPowerForm,
    AreaInformationForm,
    DepotConfigurationWishForm,
    VehicleTypeForm,
    VehicleTypeSelectionForm,
    FileUploadForm,
    ScenarioSelection,
    ManualTcoForm,
)
from .tasks import merge_scenario
from .import_export import ScenarioJSONImporterExporter, visit_all_scenario_queries

from .util import get_unique_task_id

from ebus_map.managers import X, Y

from . import data
import ebustoolbox
import ebustoolbox.tasks
from ebustoolbox.models import (
    AreaInformation,
    AreaType,
    DepotConfigurationWish,
    EnumSimulationType,
    Notification,
    Rotation,
    Scenario,
    Temperatures,
    UserGroup,
    UploadedFile,
    VehicleClass,
    VehicleType,
    DefaultScenario,
    Station,
    EnumChargeType,
    Event,
    EventType,
    Consumption,
    Trip,
    SimulationRange,
    VehicleTypeSelection,
    VehicleTypeMutation,
    StationMutation,
    EnumScenarioType,
    EnumNotificationType,
    annotate_distance,
)


# import redis
# r = redis.Redis.from_url(settings.REDIS_URL)
# r.rpush('someKey', json.dumps({'i': (cache.get('key')), 'time': 0}))

logger = logging.getLogger("custom")


def progress_scenario(request: HttpRequest, progress_id, template_name):
    context = {"progress_id": progress_id, "status": "", "current_progress": 0}
    context |= {"finished": False}
    progress: Progress = Progress.objects.get(task_id=progress_id)
    context["progress"] = progress

    context["current_progress"] = max(progress.get_progress(), 1)
    context["status"] = progress.status

    status_code = 200
    hx_trigger = "running"
    result = AsyncResult(str(progress_id).encode())
    if result.state in ["PENDING", "REVOKED", "FAILURE"]:
        # the celery task is not running. The progress will not be updated. This has to be fixed.
        progress.refresh_from_db()
        if progress.running:
            progress.running = False
            progress.status = "Abgebrochen"
            progress.errors.append(f"Task is {result.state}")
            progress.save()

    if progress.success or not progress.running or len(progress.errors) != 0:
        context["errors"] = progress.errors
        # End polling
        status_code = 286
        context["finished"] = True
        hx_trigger = "notRunning"
    if progress.success:
        hx_trigger = "success"
        if progress.progress_type == EnumProgress.RUNNING_SIMULATION:
            mutation_scenario = progress.scenario
            children = Scenario.objects.filter(parent=mutation_scenario)
            assert (
                children.count() == 2
            ), "There should only be two children. A sizing and a default scenario"
            sizing_scenario = children.get(simulationtype__sim_type=EnumSimulationType.SIZING)
            default_scenario = children.get(simulationtype__sim_type=EnumSimulationType.DEFAULT)
            context |= {"sizing_scenario": sizing_scenario, "default_scenario": default_scenario}

    response = render(request, f"core/{template_name}", context)
    response["HX-Trigger"] = hx_trigger
    response.status_code = status_code
    return response


def get_unique_progress_or_none(scenario_task_id):
    progress_db = Progress.objects.filter(
        scenario__task_id=scenario_task_id,
        progress_type=EnumProgress.INIT_SCHEDULE,
    )
    assert len(progress_db) <= 1, "Only single progress of scenario upload progress should exist"
    return progress_db.first()


def get_or_create_child_vehicle_types(
    scenario: Scenario,
) -> QuerySet[VehicleType]:
    """Create vehicle types and mutations according to the scenarios parent."""
    parent_vehicle_types = VehicleType.objects.filter(scenario=scenario.parent)
    for parent_vt in parent_vehicle_types:
        if not VehicleTypeMutation.objects.filter(
            scenario=scenario,
            original_vehicle_type=parent_vt,
        ).exists():
            logger.info(f"{parent_vt} has no linked vehicle type. Creating a linked vehicle type")
            org_vt_id = parent_vt.id
            parent_vt.id = None
            parent_vt.scenario = scenario
            parent_vt.save()
            org_vt = VehicleType.objects.get(id=org_vt_id)
            VehicleTypeMutation.objects.create(
                scenario=scenario, original_vehicle_type=org_vt, mutated_vehicle_type=parent_vt
            )
    child_vehicle_types = VehicleType.objects.filter(scenario=scenario)
    assert (
        VehicleTypeMutation.objects.filter(scenario=scenario).count()
        == parent_vehicle_types.count()
        == child_vehicle_types.count()
    ), "The number of instances should be equal for VehicleTypeMutations and VehicleTypes (parent and child)"
    return child_vehicle_types


def get_user_vehicle_types(user) -> QuerySet[VehicleType]:
    """Get a Queryset of vehicle types which can are accessible by the user"""
    default_scenario = DefaultScenario.objects.first().scenario
    return VehicleType.objects.filter(scenario=default_scenario, opportunity_charging_capable=True)


def get_sorted_mutation_scenarios(user) -> QuerySet[Scenario]:
    """Get a QuerySet of scenarios which are accessible by the user and the default scenario

    The QuerySet is ordered by User Scenarios, UserGroup Scenarios and lastly the default Scenario
    """
    if not user.is_authenticated:
        # Query for default scenario
        data_scenarios = Scenario.objects.filter(
            scenario_type=EnumScenarioType.PUBLIC_DATA,
            manager__is_superuser=True,
        )
        return data_scenarios
    scenario_qs = Scenario.objects.filter(scenario_type=EnumScenarioType.MUTATION).annotate(
        order_id=Cast(F("manager_id"), FloatField()) - user.id,
    )

    user_scenarios = get_user_scenario_qs(user, scenario_qs=scenario_qs)
    # Get the Scenario related to the scenarios which contain default data
    data_scenarios = Scenario.objects.filter(
        manager__is_superuser=True,
        scenario_type=EnumScenarioType.PUBLIC_DATA,
    ).annotate(order_id=Value(float("inf"), output_field=FloatField()))
    all_scenarios = user_scenarios.union(data_scenarios)

    # Annotation is not possible after using union
    # Order output. User Scenarios first
    all_scenarios_sorted = all_scenarios.order_by("order_id", "-id")
    return all_scenarios_sorted


def get_user_scenario_qs(user: User, scenario_qs: QuerySet[Scenario]) -> QuerySet[Scenario]:
    if not user.is_authenticated:
        return Scenario.objects.none()
    return scenario_qs.filter(Q(manager=user) | Q(usergroup__users=user)).order_by("id")


class AuthorizedMixIn:
    """Implements dispatch to check authorization"""

    has_permisson = None

    @staticmethod
    def get_permission(user, task_id) -> bool:
        """Make sure User is authorized and add scenario to class"""
        scenario = get_object_or_404(Scenario, task_id=task_id)

        if user.is_superuser:
            return True

        if scenario.manager is None:
            # Scenario is not managed and not protected by authentification
            return True

        if user.is_anonymous:
            # Scenario is managed. anonymous user does not have permission
            return False

        if scenario.manager == user:
            return True
        usergroup_scenarios = Scenario.objects.filter(usergroup__users=user)
        if scenario in usergroup_scenarios:
            return True
        return False

    def dispatch(self, request, *args, **kwargs):
        self.has_permisson = self.get_permission(request.user, kwargs.get("task_id"))
        if not self.has_permisson:
            return HttpResponseForbidden(_("Access Denied"))  # Reject the request

        return super().dispatch(request, *args, **kwargs)


class ScenarioMixIn(AuthorizedMixIn):
    """Implements redirect at dispatch if scenario was simulated already and sets default context.

    Requires AuthorizedMixin
    """

    scenario: Scenario

    def dispatch(self, request, *args, **kwargs):
        """Make sure User is authorized and add scenario to class"""
        if not self.get_permission(request.user, kwargs.get("task_id")):
            return HttpResponseForbidden("Access Denied")  # Reject the request
        scenario = get_object_or_404(Scenario, task_id=kwargs.get("task_id"))
        self.scenario = scenario
        progress = Progress.objects.filter(
            scenario=scenario, progress_type=EnumProgress.RUNNING_SIMULATION
        ).first()
        if progress is not None:
            context = {"wiz_idx": 3}
            if progress.running and not progress.success:
                # Simulation is running, redirect to
                context |= {
                    "duration": 4,
                    "redirect_url": reverse("simba:summary", args=[scenario.task_id]),
                    "content": _(
                        "Ihre Simulation wird ausgeführt, daher werden sie zur Zusammenfassung zurückgeleitet."
                    ),
                }
                return render(request, "core/redirect_with_timer.html", context)
            if progress.success:
                # Simulation finished sucessfully
                # Simulation is running, redirect to
                context |= {
                    "duration": 4,
                    "redirect_url": reverse("simba:result", args=[scenario.task_id]),
                    "content": _(
                        "Ihre Simulation ist beendet, daher werden sie zu den Ergebnissen weitergeleitet.."
                    ),
                }
                return render(request, "core/redirect_with_timer.html", context)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """Add scenario to the context"""
        context = super().get_context_data(**kwargs)
        context["scenario"] = self.scenario
        context["task_id"] = self.scenario.task_id
        return context


def get_notifications(request, task_id: str, view: str):
    _ = get_scenario_and_assert_authorization(request, task_id)
    view_class = globals().get(view)
    if view_class is None or view_class.__dict__.get("get_notifications") is None:
        raise Http404("Benachrichtigungen für diese Seite gibt es nicht")
    notifications = view_class.get_notifications(task_id)
    print(notifications.count())
    # Make a dictionary out of the different classes for easier template acccess
    notifications_dict = tasks.get_notfications_dict(notifications)
    context = dict()
    context = {"notifications": notifications_dict}
    context["any_notification"] = notifications.exists()
    context["task_id"] = task_id
    context["hx_trigger"] = "htmx:afterSettle from:body throttle:1s"

    return render(request, "ebustoolbox/partials/notifications_multi.html", context)


class TripsView(FormView):
    template_name = "ebustoolbox/trips.html"
    form_class = forms.TripsForm
    success_name = "simba:vehicles"

    def get_context_data(self, request, **kwargs):
        context = super(TripsView, self).get_context_data(**kwargs)
        assert context["form"], "Form view should return context with applied form"
        task_id = kwargs.get("task_id")
        if task_id:
            _ = get_scenario_and_assert_authorization(self.request, task_id)
            # scenario is created so we pass the progress id so a progress bar can be shown
            progress_db = get_unique_progress_or_none(kwargs.get("task_id"))
            context["progress_id"] = progress_db.task_id if progress_db else None

        scenarios = get_sorted_mutation_scenarios(self.request.user)
        context["scenarios"] = scenarios
        context["requested"] = request.GET.get("s")
        return context

    def get(self, request, *args, **kwargs):
        task_id = kwargs.get("task_id")
        first = kwargs.get("first", 0)
        if task_id and first != 1:
            progress_db = get_unique_progress_or_none(task_id)
            if progress_db and progress_db.success:
                response = redirect(reverse("simba:vehicles", args=[str(task_id)]))
                response["HX-Location"] = reverse("simba:vehicles", args=[str(task_id)])
                return response
        return self.render_to_response(self.get_context_data(request, **kwargs))

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            logger.debug("Invalid trips form provided")
            context = self.get_context_data(**kwargs)
            context["form"] = self.get_form_class()(self.request.POST, self.request.FILES)
            return render(request, "ebustoolbox/partials/trips_form.html", context)
            # return render(request, "ebustoolbox/partials/trips_form.html", context)

    def form_valid(self, form):
        """Handles successful form submission."""
        cleaned_data = form.cleaned_data
        task_id = self.kwargs.get("task_id", get_unique_task_id())
        task_id = get_unique_task_id()

        # Get a User as manager or none
        manager = None
        if self.request.user.is_authenticated:
            manager = self.request.user
        # If schedule reading failed before a scenario already exists
        print((task_id, manager))
        scenario = Scenario.objects.create(task_id=task_id, manager=manager)
        scenario.name = cleaned_data["scenario_name"]
        scenario.description = cleaned_data["description"]
        scenario.simba_options["find_stations"] = bool(cleaned_data.get("find_stations"))
        # If schedule reading failed before there is a parent already. Delete it if
        # its only child is the current scenario
        if scenario.parent:
            if scenario.parent.scenario_set.count() == 1:
                scenario.parent.delete()
        scenario.parent = None
        scenario.save()

        data_file = form.files.get("data_file")
        scenario_uuid = form.cleaned_data["existing_scenario"]
        if data_file:
            assert len(form.files) == 1, "Currently only single file uploads are allowed"

            # Loop for possible later multifile support
            files = dict()
            for name, file in form.files.items():
                # check file size
                if file.size > settings.MAX_FILE_SIZE_B:
                    # file too large
                    raise Exception("Datei zu groß")
                uploaded_file = UploadedFile.objects.create(scenario=scenario, file=file)
                files[name] = uploaded_file.file.path, uploaded_file.id
                # Delete the files from cleaned_data, since this is passed to
                # celery and needs to be json serializable
                del cleaned_data[name]
            file_suffix = data_file.name[-3:]
            parent, scenario = tasks.get_parent(scenario)
            progress_id = tasks.get_uuid()
            progress = Progress.objects.create(
                scenario=scenario,
                task_id=progress_id,
                progress_type=EnumProgress.INIT_SCHEDULE,
            )
            async_result = None
            if file_suffix == "csv":
                # change the file naming according to SimbaScheduleReader
                async_result = tasks.init_db_with_trips.apply_async(
                    (
                        scenario.id,
                        1,
                        {"file_path": files["data_file"]},
                        {},
                        progress.id,
                    ),
                    task_id=progress_id,
                )
            elif file_suffix == "zip":
                async_result = tasks.init_db_with_trips.apply_async(
                    (scenario.id, 3, {"x10_zip_file": files["data_file"]}, {}, progress.id),
                    task_id=progress_id,
                )
            else:
                progress.success = False
                progress.running = False
                progress.errors.append(
                    (
                        _(
                            "Dieser Dateityp wird nicht unterstüzt. Bitte laden sie eine .csv"
                            "im SimBA-Format oder eine .zip datei im x10 Format hoch."
                        )
                    )
                )
                progress.save()

            if async_result is not None:
                assert (
                    progress_id == async_result.task_id
                ), "Asynch result and Progress need to be equal for proper fetching of progress"
        elif scenario_uuid:
            if Scenario.objects.get(task_id=scenario_uuid) not in get_sorted_mutation_scenarios(
                self.request.user
            ):
                raise Http404
            mutation_scenario = Scenario.objects.get(task_id=scenario_uuid)
            assert mutation_scenario.scenario_type in [
                EnumScenarioType.MUTATION,
                EnumScenarioType.PUBLIC_DATA,
            ]
            if self.request.user.is_authenticated:
                mutation_scenario.manager = self.request.user
            else:
                mutation_scenario.manager = None
            copied_mutation = tasks.create_scenario_copy_for_user(mutation_scenario)
            copied_mutation.name = scenario.name
            copied_mutation.name_short = scenario.name_short
            copied_mutation.scenario_type = EnumScenarioType.MUTATION
            copied_mutation.description = scenario.description
            copied_mutation.save()
            scenario.delete()
            scenario = copied_mutation
            response = HttpResponse()
            response["HX-Redirect"] = reverse("simba:vehicles", args=[str(scenario.task_id)])
            return response

        else:
            raise NotImplementedError
        progress_db = Progress.objects.filter(task_id=progress_id).first()
        if progress_db and progress_db.success:
            # Processing the scenario finished
            response = HttpResponse()
            response["HX-Location"] = reverse("simba:vehicles", args=[str(task_id)])
            return response
        response = HttpResponse()
        # Redirect to the same url with task_id added. this allows insertion of the
        # backend progress bar
        response["HX-Redirect"] = reverse("simba:trips", args=[str(task_id), 1])
        return response


def get_scenario_and_assert_authorization(request, task_id) -> Scenario:
    scenario = get_object_or_404(Scenario, task_id=task_id)
    if request.user.is_superuser:
        return scenario
    if scenario.manager and scenario.manager != request.user:
        raise PermissionDenied(_("Sie haben keinen Zugriff auf diese Seite"))
    return scenario


class VehiclesView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/vehicles.html"
    success_name = "simba:stations"

    @staticmethod
    def get_notifications(task_id):
        scenario = get_object_or_404(Scenario, task_id=task_id)
        # TODO: show only a subset of notifications or all notifications?
        notifications = Notification.objects.filter(scenario=scenario)
        return notifications

    def get_context_data(self, **kwargs):
        scenario = self.scenario
        context = super().get_context_data(**kwargs)
        data = None
        if self.request.method == "POST":
            data = self.request.POST
        # NOTE: stations are linked with the parent/source scenario
        middlepoint = tasks.get_middlepoint(scenario.parent)
        lon, lat = None, None
        startdate = datetime.datetime(year=2015, month=1, day=1)
        # Historical dwd data goes mostly till end of 2024 and does not include the current year
        enddate = datetime.datetime(year=2024, month=12, day=31)
        # TODO: define default weatherstation in central germany
        weatherstation = WeatherStation.objects.first()
        minimal_data_ratio = 0.8
        min_data_points = (enddate - startdate).total_seconds() / 3600 * minimal_data_ratio
        if middlepoint:
            lon, lat = middlepoint
            # Annotate the weatherstations with distance attribute and sort by distance
            weatherstations = list(temperatures.tasks.get_closest_station(lon, lat))
            for ws in weatherstations:
                if (
                    temperatures.tasks.get_weatherdata(ws.dwd_id, startdate, enddate)
                ).count() > min_data_points:
                    weatherstation = ws
                    break
        context |= self.get_simulation_parameters_context(data, scenario)
        context |= self.get_vehicles_context(data, scenario)
        context |= dict(
            weatherstation=weatherstation,
            distance=getattr(weatherstation, "distance", None),
            startYear=startdate.year,
            endYear=enddate.year,
            startDate=startdate.isoformat(),
            endDate=enddate.isoformat(),
        )

        return context

    @staticmethod
    def get_simulation_parameters_context(data, scenario: Scenario) -> dict:
        """Get context for Simulation Range"""
        context = {}
        trips = Trip.objects.filter(scenario=scenario.parent).order_by("departure_time")
        start = trips.first().departure_time
        end = trips.last().arrival_time
        start_date = start.date().isoformat()
        start_time = start.time().isoformat()
        end_date = end.date().isoformat()
        end_time = end.time().isoformat()
        sim_range, unused_variable = SimulationRange.objects.get_or_create(scenario=scenario)
        temperature_average = None
        temperature_extreme = None
        if data:
            start, end = VehiclesView.parse_start_end_utc_from_POST(data)
            initial_start_date = start.date().isoformat()
            initial_start_time = start.time().isoformat()
            initial_end_date = end.date().isoformat()
            initial_end_time = end.time().isoformat()
            temperature_average = data["temperature_average"]
            temperature_extreme = data["temperature_extreme"]
            simulation_parameters_form = forms.SimulationParameters(
                data={
                    "temperature_average": temperature_average,
                    "start": start,
                    "end": end,
                    "temperature_extreme": temperature_extreme,
                },
                instance=SimulationRange.objects.get(scenario=scenario),
            )
        else:
            if sim_range.start and sim_range.end:
                assert SimulationRange.objects.filter(scenario=scenario).count() == 1
                # Times are provided to the context not via form, since different widgets
                # are used.
                start, end = sim_range.start, sim_range.end
                initial_start_date = sim_range.start.date().isoformat()
                initial_start_time = sim_range.start.time().isoformat()
                initial_end_date = sim_range.end.date().isoformat()
                initial_end_time = sim_range.end.time().isoformat()
                temperature_average = sim_range.temperature_average
                temperature_extreme = sim_range.temperature_extreme
            else:
                initial_start_date = start_date
                initial_start_time = start_time
                initial_end_date = end_date
                initial_end_time = end_time

            simulation_parameters_form = forms.SimulationParameters(
                initial={
                    "start": start,
                    "end": end,
                },
                instance=SimulationRange.objects.get(scenario=scenario),
            )
        context |= {"min_date": start_date, "max_date": end_date}
        context |= {
            "start_date": initial_start_date,
            "end_date": initial_end_date,
        }
        context |= {
            "initial_start_time": initial_start_time,
            "initial_end_time": initial_end_time,
        }
        context |= {
            "task_id": scenario.task_id,
            "simulation_parameters_form": simulation_parameters_form,
        }
        return context

    def get_vehicles_context(self, data, scenario: Scenario) -> dict:
        """Get context for vehicle types"""
        context = {}

        # Get all default vehicle types. Only Opportunity charging capable for now
        # Expand the query for desired vehicle types which can be selected
        rots = annotate_distance(Rotation.objects.filter(scenario=scenario.parent))
        result = rots.aggregate(
            distance=Sum("distance"),
            duration=Sum(F("trip__arrival_time") - F("trip__departure_time")),
        )
        average_speed_kmh = (result["distance"] / 1000) / (
            result["duration"].total_seconds() / 3600
        )

        all_default_vehicle_types = get_user_vehicle_types(self.request.user)

        for vt in all_default_vehicle_types:
            vt.has_diesel_heating = "zusatzheizung" in vt.name.lower()
        context["all_default_vehicle_types"] = all_default_vehicle_types

        # Filter out vehicle types with zusatzheizung
        default_vehicle_types = all_default_vehicle_types.exclude(name__icontains="Zusatzheizung")

        # annotate vehicle types with consumption at average speed km/h, 0 incline and 50% lol
        for vt in all_default_vehicle_types:
            # TODO: use average speed of vehicle type
            consumption: Consumption = Consumption.objects.get(vehicle_class__vehicle_types=vt)

            def get_cons(temperature):
                return consumption.get_consumption(
                    {
                        SPEED: average_speed_kmh,
                        T_AMB: temperature,
                        INCLINE: 0,
                        LEVEL_OF_LOADING: 0.5,
                    }
                )

            consumption_over_temp = [[temp, get_cons(temp)[0]] for temp in range(-10, 40, 5)]
            vt.consumption_over_temp = consumption_over_temp

        # if the child / mutation scenario has no vehicle types create them
        # for each parent vehicle type, with a mutated vehicle type of this scenario. PseudoCode:
        child_vehicle_types = get_or_create_child_vehicle_types(scenario)
        vehicle_modification = {}
        for vt in child_vehicle_types:
            vt_select, unused_variable = VehicleTypeSelection.objects.get_or_create(vehicle_type=vt)
            dvt = vt_select.default_vehicle_type
            modification = VehicleTypeForm(data, instance=vt, prefix=f"mutation_{vt.id}")

            if dvt and "zusatzheizung" in dvt.name.lower():
                # User chose a vt with diesel heating. select version without diesel instead
                all_dvts = get_user_vehicle_types(self.request.user)
                search_name = dvt.name[0 : dvt.name.lower().find("_zusatzheizung")]
                dvt = all_dvts.exclude(name__icontains="zusatzheizung").get(
                    name__icontains=search_name
                )
                vt_select.default_vehicle_type = dvt
                modification.fields["has_diesel_heating"].initial = True
            selection = VehicleTypeSelectionForm(
                data=data,
                prefix=f"selection_{vt.id}",
                vehicle_type=vt,
                choices_queryset=default_vehicle_types,
                instance=vt_select,
            )
            vehicle_modification[vt.id] = {
                "vehicle_type": vt,
                "vehicle_choices": default_vehicle_types,
                "selection": selection,
                "vehicle_modification": modification,
            }

        context["vehicle_modification"] = vehicle_modification
        context["choice_vts"] = default_vehicle_types

        return context

    @staticmethod
    def parse_start_end_utc_from_POST(data):
        start_dt = parser.parse(f"{data['start-date']} {data['start-time']}")
        start_dt_utc = start_dt.replace(tzinfo=pytz.UTC)
        end_dt = parser.parse(f"{data['end-date']} {data['end-time']}")
        end_dt_utc = end_dt.replace(tzinfo=pytz.UTC)
        return start_dt_utc, end_dt_utc

    def post(self, request, *args, **kwargs):
        forms = []
        scenario = self.scenario
        context = self.get_context_data(scenario=scenario, **kwargs)
        simulation_parameters_form = context["simulation_parameters_form"]
        if simulation_parameters_form.is_valid():
            sim_range: SimulationRange = simulation_parameters_form.save()
            Temperatures.objects.filter(scenario=scenario).delete()
            # Create temperature instance
            Temperatures.create_constant_temperatures(scenario, sim_range.temperature_average)
        forms.append(simulation_parameters_form)
        vehicle_modification = context["vehicle_modification"]

        # Gather all vehicle selection and modification forms
        for d_values in vehicle_modification.values():
            form = d_values["selection"]
            forms.append(form)
            form = d_values["vehicle_modification"]
            forms.append(form)

        # Validate vehicle forms
        if all(f.is_valid() for f in forms):
            return self.forms_valid(forms, scenario)

        else:
            for f in forms:
                if not f.is_valid():
                    logger.info(f"{f.errors}")
            return self.render_to_response(context)

    @atomic()
    def forms_valid(self, forms, scenario):
        """Handles successful form submission."""
        # Delete previous selections
        VehicleTypeSelection.objects.filter(vehicle_type__scenario=scenario).delete()
        VehicleTypeSelectionForms = list(
            filter(lambda x: x._meta.model == VehicleTypeSelection, forms)
        )
        for form in VehicleTypeSelectionForms:
            form.save()

        # Make Sure there are no conflicting VehicleClasses.
        # This might be the case if the scenario was deepcopied
        VehicleClass.objects.filter(scenario=scenario).delete()

        vehicle_type_forms = list(filter(lambda x: x._meta.model == VehicleType, forms))
        for form in vehicle_type_forms:
            # Mutate the vehicle according to the selected default vehicle
            instance = form.instance
            # Since we dont show vehicle_types with dieselengine, but instead give a checkbox
            # the default_vehicle_type used as the source of propoerties is swapped depending
            # on the state of the checkbox
            vt_selection = VehicleTypeSelection.objects.get(vehicle_type=instance)
            d_vt = vt_selection.default_vehicle_type
            if form.cleaned_data["has_diesel_heating"]:
                all_dvts = get_user_vehicle_types(self.request.user)
                d_vt = all_dvts.filter(name__contains=d_vt.name).get(
                    name__icontains="zusatzheizung"
                )
                vt_selection.default_vehicle_type = d_vt
                vt_selection.save()
                logger.info(f"Used {d_vt.name} since user chose diesel heating")
            instance = tasks.apply_vehicle_type(
                target_vehicle_type=instance, source_vehicle_type=d_vt
            )

            # Overwrite the instance with the data from the form
            vehicle_type_form = VehicleTypeForm(
                self.request.POST, instance=instance, prefix=form.prefix
            )
            mutated_vt = vehicle_type_form.save()
            # ConsumptionCalc. prioritizes ConsumptionTables linked to the respective VehicleClass.
            # If the user passed a constant consumption,
            # the vehicle type is delinked from the VehicleClass which has a consumption table.
            if vehicle_type_form.cleaned_data["consumption"] is not None:
                logger.info(f"{mutated_vt=} will not use a consumption table")
                mutated_vt.save()
                vc = VehicleClass.objects.filter(
                    scenario=mutated_vt.scenario,
                    consumption__isnull=False,
                    vehicle_types=mutated_vt,
                )
                assert vc.count() == 1
                vc = vc.first()
                vc.vehicle_types.remove(mutated_vt)
                logger.info(f"{vc=}, {vc.vehicle_types.all()=}")
            else:
                logger.info(
                    f"{mutated_vt=} will use a consumption table. Constant consumption is deleted"
                )
                mutated_vt.consumption = None
                mutated_vt.save()

        response = redirect(reverse(self.success_name, args=[scenario.task_id]))
        return response

    def form_invalid(self, form, **kwargs):
        logger.debug("Invalid Vehicles Form provided")
        return self.render_to_response(self.get_context_data(**kwargs, form=form))


class StationsView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/stations.html"
    success_name = "simba:costs"
    default_min_standing_time = 2

    @staticmethod
    def get_station_prefix(station):
        return f"station_{station.id}"

    def get_context_data(self, **kwargs):
        scenario = self.scenario
        context = super().get_context_data(**kwargs)
        parent_station_query = Station.objects.filter(scenario=scenario.parent).exclude(
            charge_type=EnumChargeType.DEPOT
        )
        try:
            min_standing_time = int(self.request.GET.get("min_standing_time", "0"))
        except ValueError:
            min_standing_time = 0

        if min_standing_time > 0:
            parent_trips = Trip.objects.filter(scenario=scenario.parent)
            parent_trips_annotated = tasks.annotate_trips_with_standing_time(parent_trips)
            td_min_standing_time = datetime.timedelta(minutes=min_standing_time)
            # TODO: check if this is slow for bigger scenarios
            parent_trips_filtered = parent_trips_annotated.filter(
                standing_time__gte=td_min_standing_time
            )
            parent_station_query = tasks.get_distinct_arrival_station(parent_trips_filtered)

        annotated_query = tasks.annotate_stations_with_lines(parent_station_query)

        # Station Mutations where created in get()
        station_mutations = {
            s.original_station.id: s.mutated_original_station
            for s in StationMutation.objects.filter(scenario=scenario)
        }
        scenario_stations = {}
        # Mutate stations with lines of their annotated parents
        for station in annotated_query:
            station_lines = {*station.lines_departure, *station.lines_arrival}
            mutated_station = station_mutations[station.id]
            mutated_station.lines = station_lines
            scenario_stations[mutated_station.id] = mutated_station

        context["ordered_stations"] = (
            Station.objects.filter(id__in=scenario_stations.keys())
            .order_by("name")
            .values_list("id", flat=True)
        )
        context["stations"] = scenario_stations

        # Might be more elegant to to that in a db query
        # Merge the lines for each stations so they can be filtered by
        context["all_lines"] = set()
        for stat in context["stations"].values():
            context["all_lines"] = context["all_lines"].union(stat.lines)

        data = self.request.POST
        if self.request.method != "POST":
            default_charge_power = scenario.simba_options["cs_power_opps"]
            data = {"default_charge_power": default_charge_power}

        # General Charging power defined on top level
        context["charging_power_form"] = forms.ChargingPowerForm(data)

        # Settings specific to each station
        context["stations_forms"] = dict()
        for station in context["stations"].values():
            context["stations_forms"][station.id] = forms.StationForm(
                data, instance=station, prefix=self.get_station_prefix(station)
            )
        context["detailedOptionNames"] = list(forms.StationForm().fields.keys())

        return context

    def get(self, request, *args, **kwargs):
        scenario = self.scenario
        # Enforce a queryparam of min_standing time
        if request.GET.get("min_standing_time") is None:
            return redirect(
                reverse("simba:stations", args=[scenario.task_id])
                + "?min_standing_time="
                + str(self.default_min_standing_time)
            )
        station_mutations = StationMutation.objects.filter(
            scenario=scenario,
        )
        if station_mutations.count() != Station.objects.filter(scenario=scenario.parent).count():
            # Not every station from the parent scenario is linked to a new station
            # Delete the current stations, create new ones and link them
            ebustoolbox.tasks.create_station_mutations(scenario)
        return self.render_to_response(
            self.get_context_data(**kwargs),
        )

    def post(self, request, *args, **kwargs):
        scenario = self.scenario
        context = self.get_context_data(**kwargs)
        all_valid = all(form.is_valid() for form in context["stations_forms"].values())
        if not all_valid:
            logger.debug("Invalid StationsForm provided")
            return self.render_to_response(context)
        charge_form = ChargingPowerForm(request.POST)
        if not charge_form.is_valid():
            logger.debug("Invalid ChargingPowerForm provided")
            return self.render_to_response(context)
        # The forms are valid. Update the stations and exclude stations
        # from electrification
        default_charge_power = charge_form.cleaned_data["default_charge_power"]
        ebustoolbox.tasks.update_stations_and_exclusion(
            context["stations_forms"].values(), default_charge_power
        )
        # update simba options
        scenario.simba_options["cs_power_opps"] = default_charge_power
        response = redirect(reverse(self.success_name, args=[scenario.task_id]))
        return response


class CostsView(ScenarioMixIn, TemplateView):
    success_name = "simba:depots"
    template_name = "ebustoolbox/costs.html"

    def get_context_data(self, **kwargs):
        data = {}
        if self.request.method == "POST":
            data = self.request.POST

        context = super().get_context_data(**kwargs)
        # Todo define which scenarios can be picked
        selectable_scenarios = Scenario.objects.filter(id__gte=590)
        costs_form = forms.CostInputModeForm(data=data, prefix="costsRadio")
        context["cost_mode_form"] = costs_form
        # Radio Button Values
        context["radio_values"] = dict()
        for choice in forms.CostInputModeForm.CHOICES:
            val = choice[0]
            context["radio_values"][val] = choice[0]
        context["costs_fileUpload"] = FileUploadForm(data=data)
        form = ScenarioSelection(data=data, queryset=selectable_scenarios)
        context["costs_scenario_selection"] = form
        context["costs_manual"] = ManualTcoForm(data=data, prefix="costs")
        # get all vehicle types in scenario to show cost params for each type
        scenario = Scenario.objects.get(task_id=kwargs["task_id"])
        context["vehicle_types"] = scenario.vehicletype_set.all()

        return context

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        form = context["cost_mode_form"]
        if not form.is_valid():
            logger.debug("Invalid Costs Form provided")
            return self.render_to_response(context)
        match form.cleaned_data["input_mode"]:
            case "no_input":
                pass
            case "file_upload":
                file_form = context["costs_fileUpload"]
                if not file_form.is_valid():
                    logger.debug("Invalid Costs File Form provided")
                    return self.render_to_response(context)
                raise NotImplementedError("file upload is not yet implemented")
            case "reference_scenario":
                scenario_selection = context["costs_scenario_selection"]
                if not scenario_selection.is_valid():
                    logger.debug("Invalid Costs Scenario Selection provided")
                    return self.render_to_response(context)
                raise NotImplementedError("scenario_selection is not yet implemented")
            case "manual":
                manual_form = context["costs_manual"]
                if not manual_form.is_valid():
                    logger.debug("Invalid Cost Manual Form provided")
                    return self.render_to_response(context)
                # TODO: actually do something with manual inputs, like saving them in DB
                pass
            case _:
                raise NotImplementedError(f"Mode {form.cleaned_data['input_mode']}")
        return redirect(reverse(self.success_name, args=[kwargs["task_id"]]))


class DepotsView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/depots.html"
    success_name = "simba:summary"

    def get_context_data(self, **kwargs):
        scenario = self.scenario
        context = super().get_context_data(**kwargs)
        data = None
        if self.request.method == "POST":
            data = self.request.POST
        # TODO: Depots could get queried for the sim_range. below method returns the stations
        # from the parent, this needs fixing if this functionality is desired.
        # Instead the stations from the scenario should be returned. this can be done
        # through the StationMutation
        # depots_query = get_depots(scenario)
        depots_query = Station.objects.filter(scenario=scenario, charge_type=EnumChargeType.DEPOT)
        context["depots"] = {station.id: station for station in depots_query}

        if DepotConfigurationWish.objects.filter(scenario=scenario).count() != depots_query.count():
            # each depot needs one configuration. if this is not the case recreate them
            DepotConfigurationWish.objects.filter(scenario=scenario).delete()
            depot_configs = []
            for station in depots_query:
                # Create the depot configs with default values.
                # This also instantiates the form with these values
                depot_configs.append(
                    DepotConfigurationWish(
                        scenario=scenario,
                        station=station,
                        default_power=150,
                        standard_block_length=6,
                    )
                )
            DepotConfigurationWish.objects.bulk_create(depot_configs)

        if AreaInformation.objects.filter(scenario=scenario).count() < depots_query.count():
            AreaInformation.objects.filter(scenario=scenario).delete()
            depot_vehicle_type = {depot: set() for depot in depots_query}
            for rotation in Rotation.objects.filter(scenario=scenario.parent).prefetch_related(
                "trip_set"
            ):
                trips = rotation.trip_set.order_by("departure_time")
                depot = trips.first().route.departure_station
                assert depot == trips.last().route.arrival_station
                org_vehicle_type = rotation.vehicle_type
                vt_mut = VehicleTypeMutation.objects.get(
                    scenario=scenario, original_vehicle_type=org_vehicle_type
                )
                stat_mut = StationMutation.objects.get(scenario=scenario, original_station=depot)
                depot_vehicle_type[stat_mut.mutated_original_station].add(
                    vt_mut.mutated_vehicle_type
                )

            area_informations = []
            for station, vehicle_types in depot_vehicle_type.items():
                wish = DepotConfigurationWish.objects.get(station=station)
                for vt in vehicle_types:
                    area_informations.append(
                        AreaInformation(
                            scenario=scenario,
                            depot_configuration_wish=wish,
                            vehicle_type=vt,
                            area_type=AreaType.DIRECT_ONESIDE,
                        )
                    )
            AreaInformation.objects.bulk_create(area_informations)

        depot_configs = DepotConfigurationWish.objects.filter(scenario=scenario)
        context["forms"] = dict()
        for depot_config in depot_configs:
            depot_forms = dict()

            depot_forms["depot_config"] = DepotConfigurationWishForm(
                data=data,
                instance=depot_config,
                prefix=f"depot_configuration_wish_{depot_config.station.id}",
            )
            areas = [
                AreaInformationForm(data=data, instance=x, prefix=f"area_info_{x.id}")
                for x in AreaInformation.objects.filter(
                    depot_configuration_wish=depot_config
                ).select_related("vehicle_type")
            ]
            depot_forms["area_information"] = sorted(
                areas, key=lambda x: x.instance.vehicle_type.name
            )
            context["forms"][depot_config.station] = depot_forms
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        if "hx-request" in request.headers:
            for depot_id, form_dict in context["forms"].items():
                form_dict["depot_config"].is_valid()
                instance = form_dict["depot_config"].instance
                # Set default for the depot config when auto generate was set to false
                if not form_dict["depot_config"].cleaned_data["auto_generate"]:
                    instance.cleaning_duration = 30
                    instance.shunting_duration = 5
                instance.save(
                    update_fields=["auto_generate", "cleaning_duration", "shunting_duration"]
                )
            self.request.method = "get"
            return self.get(request, *args, **kwargs)

        all_valid = True
        all_forms = dict()
        for depot_id, form_dict in context["forms"].items():
            form = form_dict["depot_config"]
            all_forms[depot_id] = list()
            all_forms[depot_id].append(form)
            if not form.is_valid():
                logger.info("Invalid Depot Form")
                all_valid = False

            # NOTE:The depot is generated automatically.
            # In this case no AreaInformation is needed.
            # Validation is skipped:
            if form.instance.auto_generate:
                continue

            forms = form_dict["area_information"]
            for form in forms:
                all_forms[depot_id].append(form)
                if not form.is_valid():
                    logger.info("Invalid Area Form")
                    all_valid = False

        if all_valid:
            for depot_id, d_forms in all_forms.items():
                forms_ = list(filter(lambda x: isinstance(x, DepotConfigurationWishForm), d_forms))
                if len(forms_) == 1:
                    instance = forms_[0].save()
                elif len(forms_) > 1:
                    raise Exception("There should only be a single DepotInfoForm per depot")

                forms_ = list(filter(lambda x: isinstance(x, AreaInformationForm), d_forms))
                for form in forms_:
                    form.save()

            response = redirect(reverse(self.success_name, args=[self.scenario.task_id]))
            return response

        return self.render_to_response(context)


class SummaryView(AuthorizedMixIn, TemplateView):
    template_name = "ebustoolbox/summary.html"

    @staticmethod
    def get_notifications(task_id):
        scenario = get_object_or_404(Scenario, task_id=task_id)
        children = list(Scenario.objects.filter(parent=scenario).values_list("id", flat=True))
        notifications = Notification.objects.filter(scenario__in=[scenario.id] + children).exclude(
            notification_type__in=[
                EnumNotificationType.MULTIPLE_DEPOT_TRIPS_IN_BLOCK_WARNING,
                EnumNotificationType.INTERMEDIATE_DEPOT_STOPS_TRANSFORMED,
                EnumNotificationType.MERGED_STATIONS_FOR_INCONSISTENT_TRIPS,
            ]
        )
        return notifications

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task_id = kwargs.get("task_id")
        scenario = get_object_or_404(Scenario, task_id=task_id)
        context["scenario"] = scenario
        context["task_id"] = task_id
        progress = Progress.objects.filter(
            scenario=scenario, progress_type=EnumProgress.RUNNING_SIMULATION
        ).last()
        if progress:
            context["progress"] = progress
            logger.info(f"Returning {progress=} in context")

        sim_range = SimulationRange.objects.get(scenario=scenario)
        german_weekdays = {
            0: _("Mo"),
            1: _("Di"),
            2: _("Mi"),
            3: _("Do"),
            4: _("Fr"),
            5: _("Sa"),
            6: _("So"),
        }
        _format = "%d:%m:%Y, %H:%M"
        start = sim_range.start
        end = sim_range.end
        context["sim_duration"] = (
            f"{german_weekdays[start.weekday()]} {start.strftime(_format)} - "
            f"{german_weekdays[end.weekday()]} {end.strftime(_format)}"
        )
        context["temperature_average"] = sim_range.temperature_average
        context["temperature_extreme"] = sim_range.temperature_extreme
        parent_vehicle_types = VehicleType.objects.filter(scenario=scenario.parent)

        scenario_stations = Station.objects.filter(scenario=scenario).exclude(
            charge_type=EnumChargeType.DEPOT
        )

        annotated_parent_vehicle_types = tasks.annotate_vehicletypes_with_lines(
            parent_vehicle_types
        )
        vt_mutations = {
            x.original_vehicle_type.id: x.mutated_vehicle_type
            for x in VehicleTypeMutation.objects.filter(scenario=scenario)
        }
        annotated_vehicle_types = []
        for vt in annotated_parent_vehicle_types:
            mutated_vt = vt_mutations[vt.id]
            mutated_vt.lines = vt.lines
            annotated_vehicle_types.append(mutated_vt)
        context["vehicle_types"] = annotated_vehicle_types

        context["electrified_stations"] = scenario_stations.filter(is_electrified=True)
        excluded = Station.objects.filter(scenario=scenario, is_electrifiable=False)
        excluded_ids = excluded.values_list("id", flat=True)
        context["automatic_stations"] = scenario_stations.filter(
            is_electrified=False, is_electrifiable=True
        )
        context["excluded_stations"] = scenario_stations.filter(id__in=excluded_ids)
        context["depots"] = Station.objects.filter(
            scenario=scenario, charge_type=EnumChargeType.DEPOT
        )
        return context

    def post(self, request, *args, **kwargs):
        raise NotImplementedError()
        return self.render_to_response(self.get_context_data())


def result_view(request: HttpRequest, task_id):
    """View controlling if the wait or success view should be shown"""
    try:
        scenario = Scenario.objects.get(task_id=task_id)
        if scenario.finished:
            request.task_id = str(task_id)
            return ResultView.as_view()(request, task_id=task_id, finished=True)
        # scenario exists, but is not finished: redirect to dashboard
        # TODO: redirect to progress page once that exists
        return redirect(reverse("simba:dashboard"))
    except Scenario.DoesNotExist:
        raise Http404


class ResultView(AuthorizedMixIn, TemplateView, MapEngineMixin):
    template_name = "ebustoolbox/result.html"

    def get_scenario_center(self, scenario):
        """
        Compute the mean center [longitude, latitude] of all stations
        belonging to the given scenario.
        """
        all_stations = Station.objects.filter(scenario=scenario).exclude(geom__isnull=True)

        agg = all_stations.aggregate(
            mean_lon=Avg(Cast(X("geom"), FloatField())),
            mean_lat=Avg(Cast(Y("geom"), FloatField())),
        )

        # Handle case with no stations
        if agg["mean_lon"] is None or agg["mean_lat"] is None:
            return [52.31, 13.24]  # fallback, Berlin

        return [agg["mean_lon"], agg["mean_lat"]]

    def get_context_data(self, **kwargs):
        context = super(ResultView, self).get_context_data(**kwargs)
        task_id = kwargs.get("task_id")
        if task_id is None:
            raise Http404
        task_id = str(task_id)
        context["task_id"] = task_id
        scenario = get_object_or_404(Scenario, task_id=task_id)
        context["scenario"] = scenario
        notifications = Notification.objects.filter(scenario=scenario)
        context["notifications"] = tasks.get_notfications_dict(notifications)
        center = self.get_scenario_center(scenario)
        # Update mapengine_setup JS sees the center
        context["mapengine_setup"] = {**context["mapengine_setup"], "center": center}
        return context


class DashboardView(TemplateView):
    empty_template_name = "ebustoolbox/dashboard-empty-state.html"
    template_name = "ebustoolbox/dashboard.html"

    def get_context_data(self, **kwargs):
        context = {}
        if not self.request.user.is_authenticated:
            raise Http404()
        scenarios = Scenario.objects.filter(manager=self.request.user)
        context["scenarios"] = scenarios
        if len(scenarios) == 0:
            self.render_to_response(context)
        return context

    @login_required(login_url="/login/")
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        if len(context["scenarios"]) == 0:
            return render(request, template_name=self.empty_template_name, context=context)

        return render(request, template_name=self.template_name, context=context)

    def post(self, request, *args, **kwargs):
        raise Http404()


def get_depots(scenario):
    # Get filtered depots by simrange
    parent = scenario.parent
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
    return depots_query


@require_POST
def cancel_upload(request: HttpRequest, task_id: str):
    # cause a SoftTimeLimitExceeded in task and redirect to schedule upload
    AsyncResult(task_id).revoke(terminate=True, signal="SIGUSR1")
    return redirect(reverse("simba:schedule"))


def merge_and_run(request: HttpRequest, task_id: str):
    scenario = get_scenario_and_assert_authorization(request, task_id)

    simulation_progess = Progress.objects.filter(
        scenario=scenario,
        progress_type=EnumProgress.RUNNING_SIMULATION,
    )

    # Users should not keep failed scenarios
    # This way the children of a scenario can be uniquely linked to their parent
    # (TODO: Discuss)
    logger.info("Deleting failed previous child-scenarios")
    logger.info(str(Scenario.objects.filter(parent=scenario).delete()))

    if not request.user.is_superuser:
        # Delete failed scenarios
        if simulation_progess.filter(running=True).exists():
            error_text = _("Starting multiple Simulations from the same source is not allowed")
            logger.info(error_text)
            return HttpResponseForbidden(error_text)

        if simulation_progess.filter(success=True).exists():
            error_text = _("Starting a Simulation which was sucessfully simulated is not allowed")
            logger.info(error_text)
            return HttpResponseForbidden(error_text)

    sim_task_id = get_unique_task_id()
    prev_progress = simulation_progess.first()
    if prev_progress:
        prev_progress.task_id = sim_task_id
        prev_progress.save()
        progress = prev_progress
    else:
        progress = Progress.objects.create(
            scenario=scenario,
            progress_type=EnumProgress.RUNNING_SIMULATION,
            task_id=sim_task_id,
        )
    logger.info("Running Toolchain.")
    sizing_task_id = get_unique_task_id()
    # create scenario from mutation and parent and simulate it
    try:
        async_result = tasks.run_and_merge_scenarios.apply_async(
            (scenario.id, sim_task_id, sizing_task_id),
            task_id=str(sim_task_id),
        )
        assert async_result.task_id == sim_task_id, "Task ids are expected to be equal"
    except Exception:
        progress.errors.append(
            _("Ein unerwarteter Fehler ist aufgetreten. Wenden Sie sich an ihren Administrator")
        )
        progress.set_failed()
        logger.error(traceback.format_exc())

    progress.refresh_from_db()
    progress.task_id = sim_task_id
    progress.save(update_fields=["task_id"])
    context = {}
    context["progress_id"] = sim_task_id
    context["scenario"] = scenario
    context["template_name"] = "progress_simulation.html"
    context["progress"] = progress
    response = render(request, "core/progress_poll.html", context)

    return response


class ModelListView(ListView):
    model = None

    def get_queryset(self, *args, **kwargs):
        import django.apps

        scenario = get_scenario_and_assert_authorization(self.request, self.kwargs["task_id"])
        model = django.apps.apps.app_configs["ebustoolbox"].models[self.kwargs["model"]]
        self.model = model
        qs = super(ModelListView, self).get_queryset(*args, **kwargs)
        qs = qs.filter(scenario=scenario)
        qs = qs.order_by("-id")
        return qs


def model_export_json(request: HttpRequest, model_str: str, task_id: str):
    import django.apps

    scenario = get_scenario_and_assert_authorization(request, task_id)
    model = django.apps.apps.app_configs["ebustoolbox"].models[model_str]
    if model_str.lower() != "scenario":
        objects = model.objects.filter(scenario=scenario)
    else:
        objects = model.objects.filter(task_id=task_id)

    jsondata = serializers.serialize("json", objects)

    return HttpResponse(jsondata, content_type="application/json")


def run_simulation(request: HttpRequest, task_id: str):
    try:
        try:
            scenario = Scenario.objects.get(task_id=task_id)
        except Scenario.DoesNotExist:
            raise Http404
        # if the scenario has a manager, only this User can run the simulation
        if scenario.manager and scenario.manager != request.user:
            raise Http404
        # This triggers progress polling. If the toolchain is finished,
        # the progress view will be triggered with the task_id and progress type
        logger.info("Running Toolchain.")
        if Rotation.objects.filter(scenario=scenario).count() == 1:
            return HttpResponse("The Scenario has no Rotations/blocks. Nothing to simulate")
        tasks.run_toolchain_from_scenario(scenario, assign_vehicles=True)
    except Exception:
        return HttpResponse("An error occured")

    redirection = f"<a href={reverse('simba:result', args=[task_id])}>{_('Zu den Ergebnissen')}</a>"
    return HttpResponse(_("Die Simulation war erfolgreich.") + redirection)


def download_scenario(request: HttpRequest, task_id: str):
    file_path = settings.MEDIA_ROOT / (str(task_id) + ".zip")
    if file_path.exists():
        with file_path.open("rb") as fh:
            response = HttpResponse(fh.read(), content_type="application/octet-stream")
            response["Content-Disposition"] = "attachment; filename=" + file_path.name
            return response
    return HttpResponse(_("Zip not ready yet"))


def generate_zip(request: HttpRequest, task_id: str):
    tasks.generate_zipped_scenario(task_id)
    return download_scenario(request, task_id)


@login_required(login_url="/login/")
def get_dashboard(request):
    # show all scenarios of a user
    # what about staff?
    base_qs = Scenario.objects.filter(
        scenario_type__in=[EnumScenarioType.SIMULATION, EnumScenarioType.MUTATION]
    )
    scenarios = get_user_scenario_qs(request.user, scenario_qs=base_qs).prefetch_related(
        "scenario_set"
    )
    # get task status from task_id for each scenario
    scenario_list = list()
    for scenario in scenarios:
        # Also show mutation scenarios, but only if they have not been simulated yet
        if scenario.scenario_type == EnumScenarioType.MUTATION:
            if scenario.scenario_set.count() == 0:
                scenario.state = "idle"
                scenario_list.append(scenario)
            continue
        # The progress is linked to the mutation sceanario.
        # The progress task_id is set to the resulting (simulation-) scenario task_id
        progress = Progress.objects.filter(task_id=scenario.task_id)
        # TODO: use scenario state enum or class constants
        if progress.filter(success=True).exists():
            scenario.state = "success"
        elif progress.filter(running=True).exists():
            scenario.state = "running"
        elif progress.exists():
            # not running, no success: fail
            scenario.state = "error"
        else:
            # no progress: still in setup
            scenario.state = "idle"
        scenario_list.append(scenario)

    if scenarios:
        return render(request, "ebustoolbox/dashboard.html", {"scenarios": scenario_list})
    else:
        return render(request, "ebustoolbox/dashboard-empty-state.html")


@login_required(login_url="/login/")
def compare(request):
    # show comparison page, get relevant data of user scenarios
    # allows for optional request parameter "s", which should be a scenario ID a user has access to
    # this scenario will then be shown in the first column of the comparison page
    base_qs = Scenario.objects.filter(
        scenario_type=EnumScenarioType.SIMULATION, finished__isnull=False
    )
    scenarios = get_user_scenario_qs(request.user, scenario_qs=base_qs)
    scenario_dict = dict()
    for scenario in scenarios:
        if not scenario.finished:
            # filter after union not supported
            continue
        stations = scenario.station_set.all()
        num_electrified_opps = stations.filter(charge_type=EnumChargeType.OPPORTUNITY).count()
        # sum up charging places at depots, defaults to 0 for null values
        num_cs_deps = stations.filter(charge_type=EnumChargeType.DEPOT).aggregate(
            cs=Coalesce(Sum("amount_charging_places"), 0)
        )["cs"]
        rotations = scenario.rotation_set.all()
        events = scenario.event_set.all()
        # calculate charged energy for all events
        events = events.annotate(
            charged=(F("soc_end") - F("soc_start")) * F("vehicle_type__battery_capacity")
        )
        # sum up charged energy by event type. Default value 0 in case of null values
        energy_opps = events.filter(event_type=EventType.CHARGING_OPPORTUNITY).aggregate(
            sum_charged=Coalesce(Sum("charged"), 0.0)
        )["sum_charged"]
        energy_deps = events.filter(event_type=EventType.CHARGING_DEPOT).aggregate(
            sum_charged=Coalesce(Sum("charged"), 0.0)
        )["sum_charged"]

        scenario_dict[scenario.id] = {
            _("Name"): scenario.name,
            _("Erstellt"): scenario.created.strftime("%d.%m.%Y"),
            _("Fahrzeuge"): scenario.vehicle_set.count(),
            _("Umläufe"): scenario.rotation_set.count(),
            _("Gesamtkilometer"): round(sum([r.get_distance() / 1000 for r in rotations])),
            _("Anzahl elektrifizierte Endhaltestellen"): num_electrified_opps,
            _("Geladene Energie an Endhaltestellen"): round(energy_opps),
            _("Anzahl Ladeplätze in allen Depots"): num_cs_deps,
            _("Geladene Energie an Depots [kWh]"): round(energy_deps),
        }

    return render(
        request,
        "ebustoolbox/compare.html",
        {
            "scenarios": scenario_dict,
            "requested": request.GET.get("s"),
        },
    )


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
                    return HttpResponse(_("User already exists"), status=409)
                url = f"{request.scheme}://{request.get_host()}{reverse('core:signup')}"
                # generate and append token (embed email, sign with server key)
                url += f"?token={signing.dumps(email)}"
                body = _(f"Klicken Sie auf folgenden Link, um sich zu registrieren: {url}")
                mail.send_mail(
                    subject=_("Willkommen zu eBus2030+"),
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


def render_critical_rotations(request, task_id: str):
    """Returns raw JSON data for critical rotations (critical vs. non-critical)"""
    vehicle_name_dict = data.get_all_buses_labeled(task_id)[0]
    buses = list(vehicle_name_dict.keys())

    s = Scenario.objects.get(task_id=task_id)

    df = data.get_critical_rotations_as_dataframe(s.id, buses)

    # Aggregate category counts
    category_counts = (
        df["SOC_category"].value_counts().reindex(["Nicht kritisch", "kritisch"], fill_value=0)
    )

    return JsonResponse(
        {
            "data": [
                {"value": count, "name": category} for category, count in category_counts.items()
            ]
        }
    )


def render_bustype(request, task_id: str):
    """Returns raw JSON data for vehicle type distribution"""
    vehicle_name_dict, unused_variable = data.get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    s = Scenario.objects.get(task_id=task_id)

    df = data.get_vehicle_types(s.id, buses)
    if len(df) == 0:
        return JsonResponse({"data": []})

    return JsonResponse(
        {
            "data": [
                {"value": row["count"], "name": row["name"]}
                for unused_variable, row in df.iterrows()
            ]
        }
    )


def get_soc_data(request, task_id: str):
    """
    Returns SOC (State of Charge) data over time for selected buses in JSON format.
    """
    response_data = data.get_soc_as_json(task_id)

    return JsonResponse(response_data)


def get_binned_soc_data(request, task_id: str):
    """
    Returns binned SOC histogram data over time, forward-filled to hourly resolution,
    ensuring one (the lowest) SOC entry per vehicle per hour.
    """

    response_data = data.get_binned_soc_as_json(task_id)

    return JsonResponse({"data": response_data})


def get_power_draw(request, task_id: str):
    """
    Returns power draw data over time by station ID for selected buses.
    """

    response_data = data.get_power_draw_as_json(request, task_id)

    return JsonResponse({"data": response_data})


def get_stats(request, task_id: str):
    response_data = data.get_stats_as_json(task_id)

    return JsonResponse(response_data)


def get_speed_hist(request, task_id: str):
    response_data = data.get_speed_hist_as_json(task_id)

    return JsonResponse(response_data)


def get_dist_hist(request, task_id: str):
    response_data = data.get_dist_hist_as_json(task_id)

    return JsonResponse(response_data)


def get_power_draw_and_occ(request, task_id: str):
    response_data = data.get_power_draw_and_occ_as_json(task_id)

    return JsonResponse({"data": response_data})


def get_gantt(request, task_id: str):
    scenario = Scenario.objects.get(task_id=task_id)

    records = data.recent_memoizer(data.get_gantt, scenario.id)(scenario.id).to_dict(
        orient="records"
    )

    return JsonResponse({"data": records})


def export_scenario_tree(request, task_id: str):
    """Allow admins and authorized users to download a json export of a scenario tree

    A scenario tree contains all child scenarios as well as parents, but NOT other children
    of parents.
    In case a Mutation Scenario is given, no merging of the source will take place.
    Exporting source scenarios directly is not allowed, since it can easily have to many children
    Exporting is limited to MAX_NR_SCENARIOS scenarios
    """
    # Raise an exception if user is not authorized for this task_id
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    scenario = Scenario.objects.get(task_id=task_id)
    default_scenario = DefaultScenario.objects.first()
    if default_scenario and scenario == default_scenario.scenario:
        return HttpResponseForbidden(_("Das Default Scenario darf nicht heruntergeladen werden."))
    if scenario.scenario_type == EnumScenarioType.SOURCE:
        return HttpResponseForbidden(
            _("Das Source Scenarios dürfen nicht mit allen Nachfolgern heruntergeladen werden.")
        )

    # Limit export so we can be sure load is not exploding.
    MAX_NR_SCENARIOS = 5
    scenarios = [scenario]
    count = 1

    def increase_count(count) -> int:
        if count > MAX_NR_SCENARIOS:
            raise PermissionDenied(
                _(f"Der gleichzeitige Export von mehr als {MAX_NR_SCENARIOS} ist nicht gestattet")
            )
        return count + 1

    # Get all parent scenarios
    for _i in range(count, MAX_NR_SCENARIOS):
        if scenario.parent is None:
            break
        # By inserting parents at 0 we keep the correct order for importing later
        # This way referenced scenarios exist when creating child scenarios
        scenarios.insert(0, scenario.parent)
        count = increase_count(count)
        scenario = scenario.parent

    scenario = Scenario.objects.get(task_id=task_id)
    stack = list(scenario.scenario_set.all())
    for _i in range(count, MAX_NR_SCENARIOS):
        if not stack:
            break
        scenario = stack.pop(0)
        scenarios.append(scenario)
        count = increase_count(count)
        stack.extend(list(scenario.scenario_set.all()))

    exporter = ScenarioJSONImporterExporter()
    for scenario in scenarios:
        visit_all_scenario_queries(exporter, scenario)
    json_data = exporter.renderJSON()
    return HttpResponse(json_data, content_type="application/json")


def export_scenario(request, task_id: str):
    """Allow admins and authorized users to download a json export of their scenario"""
    # Raise an exception if user is not authorized for this task_id
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    scenario = Scenario.objects.get(task_id=task_id)
    child = None
    if scenario.scenario_type == EnumScenarioType.MUTATION:
        task_id = get_unique_task_id()
        child = merge_scenario(scenario.id, task_id)
        scenario = child
    exporter = ScenarioJSONImporterExporter()
    visit_all_scenario_queries(exporter, scenario)
    json_data = exporter.renderJSON()
    # If a child was created delete it
    if child is not None:
        child.delete()
    return HttpResponse(json_data, content_type="application/json")


def import_scenario_tree(request):
    if not request.user.is_authenticated:
        return HttpResponseForbidden(_("Importing data is only allowed for logged in Users"))

    if request.method == "GET":
        return render(request, "ebustoolbox/import_scenario.html")

    if request.method == "POST":
        assert request.FILES["scenario_json"]
        importer = ScenarioJSONImporterExporter()
        importer.loads(in_memory_file=request.FILES["scenario_json"])

        importer.generate_instances()
        for scenario in importer.object_data["Scenario"]:
            if Scenario.objects.filter(task_id=scenario.task_id).exists():
                new_task_id = get_unique_task_id()
                logger.warning(
                    f"task_id {scenario.task_id} already exists in the database. "
                    f"Imported Scenario will get a new task_id of {new_task_id}"
                )
                scenario.task_id = new_task_id

        # bulk create instances and the db generated ids to appropriately set the foreign keys
        importer.bulk_create_and_adjust_foreign_keys()

        importer.create_many_to_many()
        scenario_ids = [scenario.id for scenario in importer.object_data["Scenario"]]
        Scenario.objects.filter(id__in=scenario_ids).update(manager=request.user)

        core.deepcopy.reset_postgres_auto_increments([Scenario._meta.app_label])
        return HttpResponse(
            _(
                f"Szenarios wurden erfolgreich importiert mit folgenden ids <br>"
                f"{'<br>'.join([s.task_id for s in importer.object_data['Scenario']])}. "
            )
        )
    return HttpResponseBadRequest(_("Use POST or GET"))


def import_scenario(request):
    # Normal importing is deprecated for normal users. Use
    if not request.user.is_superuser:
        return HttpResponseForbidden(
            _(
                "Import of single scenarios is not supported for normal users. You can Import Scenario Trees instead."
            )
        )

    if request.method == "GET":
        return render(request, "ebustoolbox/import_scenario.html")

    if request.method == "POST":
        assert request.FILES["scenario_json"]
        importer = ScenarioJSONImporterExporter()
        importer.loads(in_memory_file=request.FILES["scenario_json"])

        importer.generate_instances()
        assert (
            len(importer.object_data["Scenario"]) == 1
        ), "Importing is only supported for single scenarios"
        scenario: Scenario = importer.object_data["Scenario"][0]
        if Scenario.objects.filter(task_id=scenario.task_id).exists():
            new_task_id = get_unique_task_id()
            logger.warning(
                f"task_id {scenario.task_id} already exists in the database. "
                f"Imported Scenario will get a new task_id of {new_task_id}"
            )
            scenario.task_id = new_task_id
            scenario.manager = request.user
        if scenario.scenario_type not in (
            EnumScenarioType.SIMULATION,
            EnumScenarioType.MUTATION,
            EnumScenarioType.SOURCE,
        ):
            return HttpResponseBadRequest(
                _(f"{scenario.scenario_type} is not supported for importing.")
            )

        importer.adjust_foreign_keys()
        importer.bulk_create()
        importer.create_many_to_many()

        scenario_ids = [scenario.id for scenario in importer.object_data["Scenario"]]
        Scenario.objects.filter(id__in=scenario_ids).update(manager=request.user)

        core.deepcopy.reset_postgres_auto_increments([Scenario._meta.app_label])
        task_id = scenario.task_id
        redirect_suggestion = ""
        if (
            not Event.objects.filter(scenario=scenario).exists()
            and scenario.scenario_type == EnumScenarioType.SIMULATION
        ):
            redirect_suggestion = _(
                f"<a href={reverse('simba:run_simulation', args=[task_id])} > run the simulation</a>"
            )
        else:
            redirect_suggestion = _(
                f"View <a href={reverse('simba:result', args=[task_id])}>results</a>"
            )
        return HttpResponse(
            _(f"Scenario succesfully imported with task_id {task_id}. ") + redirect_suggestion
        )
    return HttpResponseBadRequest(_("Use POST or GET"))
