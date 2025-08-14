import logging
import traceback
import dateutil.parser as parser
import datetime

from django.conf import settings
import pytz
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing, mail
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F, QuerySet, Sum, Value, FloatField, Q
from django.db.models.functions import Cast, Coalesce
from django.db.transaction import atomic
from django.utils.translation import gettext as _
from django.forms import formset_factory, widgets
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
    VehicleTypeForm,
    VehicleTypeSelectionForm,
    FileUploadForm,
    ScenarioSelection,
    ManualTcoForm,
    ManualLcaForm,
    DepotChargingAreaForm,
)
from .tasks import merge_scenario
from .import_export import ScenarioJSONImporterExporter, visit_all_scenario_queries

from .util import get_unique_task_id

from . import data
import ebustoolbox
import ebustoolbox.tasks
from ebustoolbox.models import (
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
    annotate_distance,
)

logger = logging.getLogger("custom")


def progress_scenario(request: HttpRequest, progress_id, template_name):
    context = {"progress_id": progress_id, "status": "", "current_progress": 0}

    context |= {"finished": False}
    try:
        # scenario = Scenario.objects.get(task_id=progress_id)
        progress = Progress.objects.get(task_id=progress_id)
        context["progress"] = progress
    except ObjectDoesNotExist:
        response = render(request, "core/progress.html", context)
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
    if progress.success:
        hx_trigger = "success"
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
    # Todo Define what admins should see and refactor function with new get_user_scenario_qs
    if not user.is_authenticated:
        # Query for default scenario
        default_scenario = Scenario.objects.filter(defaultscenario=DefaultScenario.objects.first())
        return default_scenario
    scenario_qs = Scenario.objects.filter(scenario_type=EnumScenarioType.MUTATION).annotate(
        order_id=Cast(F("manager_id"), FloatField()) - user.id,
    )

    user_scenarios = get_user_scenario_qs(user, scenario_qs=scenario_qs)
    # Get the Scenario related to the Singleton DefaultScenario as queryset
    default_scenario = Scenario.objects.filter(
        defaultscenario=DefaultScenario.objects.first()
    ).annotate(order_id=Value(float("inf"), output_field=FloatField()))
    all_scenarios = user_scenarios.union(default_scenario)

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
    def get_permission(user, task_id):
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

        # Get a User as manager or none
        manager = None
        if self.request.user.is_authenticated:
            manager = self.request.user
        # If schedule reading failed before a scenario already exists
        scenario, _ = Scenario.objects.get_or_create(task_id=task_id, manager=manager)
        scenario.name = cleaned_data["scenario_name"]
        scenario.description = cleaned_data["description"]
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
                    (scenario.id, 3, files, cleaned_data, progress.id),
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
            if not Scenario.objects.get(task_id=scenario_uuid) in get_sorted_mutation_scenarios(
                self.request.user
            ):
                raise Http404
            mutation_scenario = Scenario.objects.get(task_id=scenario_uuid)
            assert mutation_scenario.scenario_type == EnumScenarioType.MUTATION
            copied_mutation = tasks.create_scenario_copy_for_user(mutation_scenario)
            copied_mutation.name = scenario.name
            copied_mutation.name_short = scenario.name_short
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
        raise Http404(_("No access"))
    return scenario


class VehiclesView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/vehicles.html"
    success_name = "simba:stations"

    def get_context_data(self, **kwargs):
        scenario = self.scenario
        context = super().get_context_data(**kwargs)
        data = {}
        if self.request.method == "POST":
            data = self.request.POST
        middlepoint = tasks.get_middlepoint(scenario)
        lon, lat = None, None
        startdate = datetime.datetime(year=2015, month=1, day=1)
        # Historical dwd data goes mostly till end of 2024 and does not include the current year
        enddate = datetime.datetime(year=2024, month=12, day=31)
        # TODO: define default weatherstation in central germany
        weatherstation = WeatherStation.objects.first()
        # Only pick a weather station close to the system,
        # if there are at least data for 80% of time
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
        sim_range, _ = SimulationRange.objects.get_or_create(scenario=scenario)
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

        default_vehicle_types = get_user_vehicle_types(self.request.user)

        # annotate vehicle types with consumption at average speed km/h, 0 incline and 50% lol
        for vt in default_vehicle_types:
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
            vt_select, _ = VehicleTypeSelection.objects.get_or_create(vehicle_type=vt)
            selection = VehicleTypeSelectionForm(
                data,
                prefix=f"selection_{vt.id}",
                vehicle_type=vt,
                choices_queryset=default_vehicle_types,
                instance=vt_select,
            )
            modification = VehicleTypeForm(data, instance=vt, prefix=f"mutation_{vt.id}")
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
            d_vt = VehicleTypeSelection.objects.get(vehicle_type=instance).default_vehicle_type
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
        # The forms are valid. Update the stations and exclude stations
        # from electrification
        ebustoolbox.tasks.update_stations_and_exclusion(
            context["stations_forms"].values(), scenario.simba_options["cs_power_opps"]
        )
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
        lca_form = forms.CostInputModeForm(data=data, prefix="envRadio")
        context["env_mode_form"] = lca_form
        # Radio Button Values
        context["radio_values"] = dict()
        for choice in forms.CostInputModeForm.CHOICES:
            val = choice[0]
            context["radio_values"][val] = choice[0]

        for prefix in ["costs", "env"]:
            context[prefix + "_fileUpload"] = FileUploadForm(data=data, prefix=prefix)
            form = ScenarioSelection(data=data, queryset=selectable_scenarios, prefix=prefix)
            form.is_valid()
            context[prefix + "_scenario_selection"] = form

        context["costs_manual"] = ManualTcoForm(data=data, prefix="costs")
        context["env_manual"] = ManualLcaForm(data=data, prefix="env")

        return context

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        all_valid = True

        for form, prefix in zip(
            [context["cost_mode_form"], context["env_mode_form"]],
            ["costs", "env"],
        ):
            valid = form.is_valid()
            all_valid = all_valid & valid
            if not valid:
                logger.debug("Invalid Costs Form provided")

                break
            match form.cleaned_data["input_mode"]:
                case "no_input":
                    pass
                case "file_upload":
                    file_form = context[prefix + "_fileUpload"]
                    if not file_form.is_valid():
                        logger.debug("Invalid Costs File Form provided")
                        all_valid = False
                        break
                    raise NotImplementedError("file upload is not yet implemented")
                    pass
                case "reference_scenario":
                    scenario_selection = context[prefix + "_scenario_selection"]
                    if not scenario_selection.is_valid():
                        logger.debug("Invalid Costs Scenario Selection provided")
                        all_valid = False
                        break
                    raise NotImplementedError("scenario_selection is not yet implemented")
                case "manual":
                    manual_form = context[prefix + "_manual"]
                    if not manual_form.is_valid():
                        logger.debug("Invalid Cost Manual Form provided")
                        all_valid = False
                        break
                    raise NotImplementedError("manual_form is not yet implemented")
                case _:
                    raise NotImplementedError(f"Mode {form.cleaned_data['input_mode']}")
        if not all_valid:
            return self.render_to_response(context)

        return redirect(reverse(self.success_name, args=[kwargs["task_id"]]))


class DepotsView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/depots.html"
    success_name = "simba:summary"

    def get_context_data(self, **kwargs):
        scenario = self.scenario
        context = super().get_context_data(**kwargs)
        data = {}
        if self.request.method == "POST":
            data = self.request.POST
        # ToDo: Depots could get queried for the sim_range. below method returns the stations
        # from the parent, this needs fixing if this functionality is desired.
        # Instead the stations from the scenario should be returned. this can be done
        # through the StationMutation
        # depots_query = get_depots(scenario)
        depots_query = Station.objects.filter(scenario=scenario, charge_type=EnumChargeType.DEPOT)
        context["depots"] = {depot.id: depot for depot in depots_query}
        context["forms"] = dict()
        for depot in depots_query:
            formset_prefix = f"depot_area_{depot.id}"
            if self.request.method == "GET":
                formset_data = {
                    f"{formset_prefix}-TOTAL_FORMS": "1",
                    f"{formset_prefix}-INITIAL_FORMS": "1",
                }
                data.update(formset_data)
            context["forms"][depot.id] = dict()

            stations_mode_form = forms.DepotCalculationForm(
                data, prefix=f"depot_calc_mode_{depot.id}"
            )
            # change the type of station mode form since in this case its not a radio
            # but just hidden value toggle
            # Use a text input in this case
            stations_mode_form.base_fields["calculation_mode"].widget = widgets.TextInput()

            context["forms"][depot.id]["calculation_mode_form"] = stations_mode_form
            context["forms"][depot.id]["depot_info_form"] = forms.DepotInfoForm(
                data, instance=depot, prefix=f"depot_info_{depot.id}"
            )
            context["forms"][depot.id]["depot_area_forms"] = formset_factory(
                DepotChargingAreaForm,
            )(
                data,
                prefix=formset_prefix,
            )
        return context

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        all_valid = True
        all_forms = dict()
        for depot_id, form_dict in context["forms"].items():
            form = form_dict["calculation_mode_form"]
            all_forms[depot_id] = list()
            all_forms[depot_id].append(form)
            if not form.is_valid():
                logger.info("Invalid Depots Calculation Mode Form Provided")
                continue
            mode = form.cleaned_data["calculation_mode"]
            if mode == "automatic":
                continue
            elif mode == "manual":
                all_forms[depot_id].append(form_dict["depot_info_form"])
                depot_area_forms = [form for form in form_dict["depot_area_forms"].forms]
                assert len(depot_area_forms) > 0
                all_forms[depot_id].extend(depot_area_forms)
            else:
                raise NotImplementedError(f"Mode {mode} not supported")

        for depot_id, d_forms in all_forms.items():
            for form in d_forms:
                if not form.is_valid():
                    logger.info(f"Invalid Depots {form} provided")
                    all_valid = False

        if all_valid:
            for depot_id, d_forms in all_forms.items():
                forms_ = list(filter(lambda x: isinstance(x, forms.DepotInfoForm), d_forms))
                if len(forms_) == 1:
                    instance = forms_[0].save()
                elif len(forms_) > 1:
                    raise Exception("There should only be a single DepotInfoForm per depot")

                forms_ = list(filter(lambda x: isinstance(x, DepotChargingAreaForm), d_forms))
                for form in forms_:
                    instance = Station.objects.get(id=depot_id)
                    for key, value in form.cleaned_data.items():
                        setattr(instance, key, value)
                    instance.save()
                    # ToDo only the first form is handled since a station only has a single
                    # area right now
                    break

            # Todo: Implement Database stuff of multiple areas and calcuation mode
            logger.warning(
                "Depot forms are valid, but are not implemented yet. Unclear how Manual and "
                "automatic calculation and area definition should be stored"
            )
            response = redirect(reverse(self.success_name, args=[self.scenario.task_id]))
            return response

        return self.render_to_response(context)


class SummaryView(AuthorizedMixIn, TemplateView):
    template_name = "ebustoolbox/summary.html"

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
            # context["show_rerun"]= (not progress.running and not progress.success) or progress.errors

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

    def get_context_data(self, **kwargs):
        context = super(ResultView, self).get_context_data(**kwargs)
        task_id = kwargs.get("task_id")
        if task_id is None:
            raise Http404
        task_id = str(task_id)
        context["task_id"] = task_id
        scenario = get_object_or_404(Scenario, task_id=task_id)
        context["scenario"] = scenario

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

    if not request.user.is_superuser:
        if simulation_progess.filter(running=True).exists():
            error_text = _("Starting multiple Simulations from the same source is not allowed")
            logger.info(error_text)
            return HttpResponseForbidden(error_text)

        if simulation_progess.filter(success=True).exists():
            error_text = _("Starting a Simulation which was sucessfully simulated is not allowed")
            logger.info(error_text)
            return HttpResponseForbidden(error_text)

    sim_task_id = get_unique_task_id()
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
    from django.core import serializers

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
        scenario_type=EnumScenarioType.SIMULATION,
    )
    scenarios = get_user_scenario_qs(request.user, scenario_qs=base_qs)
    # get task status from task_id for each scenario
    scenario_list = list()
    for scenario in scenarios:
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
    vehicle_name_dict, _ = data.get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    s = Scenario.objects.get(task_id=task_id)

    df = data.get_critical_rotations_as_dataframe(s.id, buses)

    # Aggregate category counts
    category_counts = (
        df["SOC_category"]
        .value_counts()
        .reindex([_("Nicht kritisch"), _("kritisch")], fill_value=0)
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
    vehicle_name_dict, _ = data.get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    s = Scenario.objects.get(task_id=task_id)

    df = data.get_vehicle_types(s.id, buses)
    if len(df) == 0:
        return JsonResponse({"data": []})

    return JsonResponse(
        {"data": [{"value": row["count"], "name": row["name"]} for _, row in df.iterrows()]}
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


def get_gantt_data(request, task_id: str):
    categories, gantt_data = data.get_event_gantt_as_json(task_id)

    return JsonResponse({"categories": categories, "data": gantt_data})


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


def get_soc_gantt(request, task_id: str):
    vehicles, records = data.get_soc_gantt_as_json(task_id)

    return JsonResponse({"vehicles": vehicles, "records": records})


def export_scenario(request, task_id: str):
    """Allow admins and authorized users to download a json export of their scenario"""
    # Raise an exception if user is not authorized for this task_id
    AuthorizedMixIn.get_permission(request.user, task_id)
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


def import_scenario(request):
    if not request.user.is_authenticated:
        return HttpResponseForbidden(_("Importing data is only allowed for logged in Users"))

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
                _(f"{scenario.scenario_type} is not supported for exporting.")
            )
        importer.adjust_foreign_keys()
        importer.bulk_create()
        importer.create_many_to_many()
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
