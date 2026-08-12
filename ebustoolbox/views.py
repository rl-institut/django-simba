import datetime
import dateutil.parser as parser
import logging
import traceback
import zipfile

from django.conf import settings
from django.core.exceptions import PermissionDenied
import pytz
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing, mail, serializers
from django.db.models import F, QuerySet, Sum, Value, FloatField, Q, Avg
from django.db.models.functions import Cast, Coalesce
from django.db.transaction import atomic, set_rollback
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

from ebusdjango.my_celery import app
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
)
from .tasks import deepcopy_scenario, merge_scenario
from .import_export import ScenarioJSONImporterExporter, visit_all_scenario_queries

from .util import get_unique_task_id, to_zip

from ebus_map.managers import X, Y

from . import data
import ebustoolbox
import ebustoolbox.tasks
from ebustoolbox import impact
from ebustoolbox.impact import ensure_fleet_topology
from ebustoolbox.models import (
    AreaInformation,
    AreaType,
    ChargingPointType,
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
    Depot,
    Station,
    EnumChargeType,
    Event,
    EventType,
    Consumption,
    Trip,
    SimulationTemperatures,
    VehicleTypeSelection,
    VehicleTypeMutation,
    StationMutation,
    EnumScenarioType,
    EnumNotificationType,
    annotate_distance,
    EnumEnergySource,
)

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
    failed = result.state in ["REVOKED", "FAILURE"]
    not_exists = result.state == "PENDING"
    reserved = False
    if not_exists:
        for r in (app.control.inspect().reserved() or {}).values():
            ids = set(x["id"] for x in r)
            if str(progress.task_id) in ids:
                reserved = True
    if reserved:
        progress.status = "In Warteschlange"
        progress.save()
    if failed or (not_exists and not reserved):
        # the celery task is not running. The progress will not be updated. This has to be fixed.
        progress.refresh_from_db()
        if progress.running:
            progress.running = False
            progress.status = "Abgebrochen" if failed else "Aufgabe nicht gefunden"
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
        if request.session.get(str(progress.scenario.task_id), None) is None:
            context["first_success"] = True
            request.session[str(progress.scenario.task_id)] = int(
                datetime.datetime.now().timestamp()
            )

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
            logger.info(
                f"S.ID:{scenario.id}: {parent_vt} has no linked vehicle type. Creating a linked vehicle type"
            )
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


def get_sorted_selectable_scenarios(user) -> QuerySet[Scenario]:
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
    scenario_qs = Scenario.objects.filter(
        scenario_type__in=[EnumScenarioType.MUTATION, EnumScenarioType.SOURCE_FILE]
    ).annotate(
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
    context["viewname"] = view
    context["task_id"] = task_id
    context["hx_trigger"] = "htmx:afterSettle from:body throttle:1s"

    return render(request, "ebustoolbox/partials/notifications_multi.html", context)


class TripsView(FormView):
    template_name = "ebustoolbox/trips.html"
    form_class = forms.TripsForm
    success_name = "simba:filter_scenario"

    def get_context_data(self, request, **kwargs):
        context = super(TripsView, self).get_context_data(**kwargs)
        assert context["form"], "Form view should return context with applied form"
        task_id = kwargs.get("task_id")
        if task_id:
            _ = get_scenario_and_assert_authorization(self.request, task_id)
            # scenario is created so we pass the progress id so a progress bar can be shown
            progress_db = get_unique_progress_or_none(kwargs.get("task_id"))
            context["progress_id"] = progress_db.task_id if progress_db else None

        scenarios = list(get_sorted_selectable_scenarios(self.request.user))
        userScenarios = list(filter(lambda x: x.manager == request.user, scenarios))
        groupScenarios = list(
            filter(
                lambda x: x.manager != request.user
                and x.scenario_type != EnumScenarioType.PUBLIC_DATA,
                scenarios,
            )
        )
        publicScenarios = list(
            filter(lambda x: x.scenario_type == EnumScenarioType.PUBLIC_DATA, scenarios)
        )
        context["scenarios"] = scenarios
        context["userScenarios"] = userScenarios
        context["groupScenarios"] = groupScenarios
        context["publicScenarios"] = publicScenarios
        context["requested"] = request.GET.get("s")
        return context

    def get(self, request, *args, **kwargs):
        task_id = kwargs.get("task_id")
        first = kwargs.get("first", 0)
        if task_id and first != 1:
            progress_db = get_unique_progress_or_none(task_id)
            if progress_db and progress_db.success:
                response = redirect(reverse(self.success_name, args=[str(task_id)]))
                response["HX-Location"] = reverse(self.success_name, args=[str(task_id)])
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

    def form_valid(self, form):  # noqa
        """Handles successful form submission."""
        cleaned_data = form.cleaned_data
        task_id = self.kwargs.get("task_id", get_unique_task_id())
        task_id = get_unique_task_id()

        # Get a User as manager or none
        manager = None
        if self.request.user.is_authenticated:
            manager = self.request.user
        # If schedule reading failed before a scenario already exists
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
                # Distinguish VDV (.x10 inside) from BVG-XML (.xml inside) by sniffing the
                # uploaded zip's entry names.
                zip_path = files["data_file"][0]
                try:
                    with zipfile.ZipFile(zip_path) as zf:
                        names = [n.lower() for n in zf.namelist()]
                except zipfile.BadZipFile:
                    names = []
                has_x10 = any(n.endswith(".x10") for n in names)
                has_xml = any(n.endswith(".xml") for n in names)
                if has_x10:
                    async_result = tasks.init_db_with_trips.apply_async(
                        (scenario.id, 3, {"x10_zip_file": files["data_file"]}, {}, progress.id),
                        task_id=progress_id,
                    )
                elif has_xml:
                    async_result = tasks.init_db_with_trips.apply_async(
                        (scenario.id, 4, {"xml_zip_file": files["data_file"]}, {}, progress.id),
                        task_id=progress_id,
                    )
                else:
                    progress.success = False
                    progress.running = False
                    progress.errors.append(
                        _(
                            "Das Zip-Archiv enthält weder .x10- (VDV 451/452) noch "
                            ".xml-Dateien (BVG-XML Linienfahrplan)."
                        )
                    )
                    progress.save()
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
            if Scenario.objects.get(task_id=scenario_uuid) not in get_sorted_selectable_scenarios(
                self.request.user
            ):
                raise Http404
            selected_scenario = Scenario.objects.get(task_id=scenario_uuid)
            assert selected_scenario.scenario_type in [
                EnumScenarioType.MUTATION,
                EnumScenarioType.SOURCE_FILE,
                EnumScenarioType.PUBLIC_DATA,
            ]
            if self.request.user.is_authenticated:
                selected_scenario.manager = self.request.user
            else:
                selected_scenario.manager = None
            if selected_scenario.scenario_type != EnumScenarioType.SOURCE_FILE:
                copied_mutation = tasks.create_scenario_copy_for_user(selected_scenario)
                copied_mutation.name = scenario.name
                copied_mutation.name_short = scenario.name_short
                if selected_scenario.scenario_type != EnumScenarioType.SOURCE_FILE:
                    copied_mutation.scenario_type = EnumScenarioType.MUTATION
                else:
                    copied_mutation.scenario_type = EnumScenarioType.SOURCE
                copied_mutation.description = scenario.description
                copied_mutation.save()
                scenario.delete()
                scenario = copied_mutation
                response = HttpResponse()
                response["HX-Redirect"] = reverse(self.success_name, args=[str(scenario.task_id)])
                return response
            else:
                assert selected_scenario.scenario_type == EnumScenarioType.SOURCE_FILE
                prev_id = selected_scenario.id
                selected_scenario.task_id = get_unique_task_id()
                source_scenario, unused_variable = deepcopy_scenario(selected_scenario)
                source_scenario.scenario_type = EnumScenarioType.SOURCE
                source_scenario.parent_id = prev_id
                source_scenario.save()
                scenario.parent = source_scenario
                scenario.simba_options = vars(tasks.get_args(scenario))
                scenario.scenario_type = EnumScenarioType.MUTATION
                scenario.save()
                response = HttpResponse()
                response["HX-Redirect"] = reverse(self.success_name, args=[str(scenario.task_id)])
                return response
        else:
            raise NotImplementedError
        progress_db = Progress.objects.filter(task_id=progress_id).first()
        if progress_db and progress_db.success:
            # Processing the scenario finished
            response = HttpResponse()
            response["HX-Location"] = reverse(self.success_name, args=[str(task_id)])
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


class FilterView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/filter_scenario.html"
    success_name = "simba:vehicles"

    @staticmethod
    def get_notifications(task_id):
        scenario = get_object_or_404(Scenario, task_id=task_id)
        # TODO: show only a subset of notifications or all notifications?
        # Show notifications of the schedule reading which are linked to the source
        notifications = Notification.objects.filter(scenario=scenario.parent)
        return notifications

    def get_context_data(self, request, **kwargs):
        context = super(FilterView, self).get_context_data(**kwargs)
        if self.request.method == "POST":
            self.data = request.POST.copy()  # mutable
        elif self.request.method == "GET":
            self.data = request.GET.copy()  # mutable
        else:
            raise Http404()

        context |= __class__.get_simulation_parameters_context(self.data, self.scenario)
        return context

    @staticmethod
    def parse_start_end_utc_from_POST(data):
        start_dt = parser.parse(f"{data['start-date']} {data['start-time']}")
        start_dt_utc = start_dt.replace(tzinfo=pytz.UTC)
        end_dt = parser.parse(f"{data['end-date']} {data['end-time']}")
        end_dt_utc = end_dt.replace(tzinfo=pytz.UTC)
        return start_dt_utc, end_dt_utc

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
        if data:
            start, end = __class__.parse_start_end_utc_from_POST(data)
            initial_start_date = start.date().isoformat()
            initial_start_time = start.time().isoformat()
            initial_end_date = end.date().isoformat()
            initial_end_time = end.time().isoformat()
            # NOTE: data is a query dict
            data["start"], data["end"] = start, end
            # SimulationFilterForm creates a filter for the parent data
            simulation_parameters_form = forms.SimulationFilterForm(scenario=scenario, data=data)
        else:
            initial_start_date = start_date
            initial_start_time = start_time
            initial_end_date = end_date
            initial_end_time = end_time

            # SimulationFilterForm creates a filter for the scenario.parent data
            simulation_parameters_form = forms.SimulationFilterForm(
                scenario=scenario,
                initial={
                    "start": start,
                    "end": end,
                },
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

    def get(self, request, *args, **kwargs):
        if Scenario.objects.filter(parent=self.scenario.parent).count() > 1:
            # If the scenario has children, changing the source_scenario is not allowed
            return redirect(reverse(self.success_name, args=[self.scenario.task_id]))

        context = self.get_context_data(request, **kwargs)
        form: forms.SimulationFilterForm = context["simulation_parameters_form"]
        if form.is_valid():
            with atomic():
                __class__.delete_with_filter(self.scenario.parent, form)
                context["simulation_parameters_form"] = forms.SimulationFilterForm(
                    scenario=self.scenario, data=self.data
                )
                set_rollback(True)

        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        if Scenario.objects.filter(parent=self.scenario.parent).count() > 1:
            # If the scenario has children, changing the source_scenario is not allowed
            return HttpResponseForbidden(
                _(
                    "Das Szenario kann nicht wiederholt gefiltert werden. Erstellen Sie stattdessen eine neue Variante"
                )
            )
        context = self.get_context_data(request, **kwargs)
        form: forms.SimulationFilterForm = context["simulation_parameters_form"]
        if form.is_valid():
            with atomic():
                __class__.delete_with_filter(self.scenario.parent, form)
                rot_exists = Rotation.objects.filter(scenario=self.scenario.parent).exists()
                if rot_exists:
                    print("success")
                    response = redirect(reverse(self.success_name, args=[self.scenario.task_id]))
                    return response
                else:
                    form.add_error(
                        field=None, error=_("Eine Simulation benötigt mindestens einen Umlauf")
                    )
                    set_rollback(True)
        return self.render_to_response(context)

    @staticmethod
    def get_rotation_count(request, task_id):
        scenario = get_scenario_and_assert_authorization(request, task_id)
        q = request.GET.copy()  # mutable

        start, end = __class__.parse_start_end_utc_from_POST(
            q
        )  # pass the QueryDict or a proper dict
        q["start"], q["end"] = start, end

        form = forms.SimulationFilterForm(scenario=scenario, data=q)
        form.is_valid()
        with atomic():
            __class__.delete_with_filter(scenario.parent, form)
            count = Rotation.objects.filter(scenario=scenario.parent).count()
            set_rollback(True)
        context = {"count": count, "errors": form.errors}
        import time

        time.sleep(1)
        return render(request, "ebustoolbox/partials/rotation_count.html", context)

    @staticmethod
    def delete_with_filter(scenario, form: forms.SimulationFilterForm):
        # Remove rotations from the timespan
        start = form.cleaned_data["start"]
        end = form.cleaned_data["end"]
        tasks.trim_scenario(scenario, end - start, start)
        # Used for clearing up depots without rotations
        dep_ids = form.cleaned_data["depot_select"]
        line_ids = form.cleaned_data["line_select"]
        if line_ids:
            Rotation.objects.filter(scenario=scenario).exclude(
                trip__route__line_id__in=line_ids
            ).delete()
        if dep_ids:
            depots = Station.objects.filter(
                scenario=scenario, charge_type=EnumChargeType.DEPOT
            ).exclude(id__in=dep_ids)
            dep_ids = depots.values_list("id", flat=True)
        else:
            dep_ids = []
        tasks.trim_depots(scenario, dep_ids)


class VehiclesView(ScenarioMixIn, TemplateView):
    template_name = "ebustoolbox/vehicles.html"
    success_name = "simba:stations"

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
        context |= self.get_vehicles_context(data, scenario)
        sim_temps, unused_variable = SimulationTemperatures.objects.get_or_create(scenario=scenario)
        context["temperatures_form"] = forms.SimulationTemperaturesForm(
            instance=sim_temps, data=data
        )
        context |= dict(
            weatherstation=weatherstation,
            distance=getattr(weatherstation, "distance", None),
            startYear=startdate.year,
            endYear=enddate.year,
            startDate=startdate.isoformat(),
            endDate=enddate.isoformat(),
        )

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

    def post(self, request, *args, **kwargs):
        forms = []
        scenario = self.scenario
        context = self.get_context_data(scenario=scenario, **kwargs)
        simulation_parameters_form = context["temperatures_form"]
        if simulation_parameters_form.is_valid():
            sim_range: SimulationTemperatures = simulation_parameters_form.save()
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
            logger.info(f"S.ID:{scenario.id}: At least one invalid VehicleForm.")
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
                logger.info(f"S.ID:{scenario.id}:Used {d_vt.name} since user chose diesel heating")
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
                logger.info(f"S.ID:{scenario.id}:{mutated_vt=} will not use a consumption table")
                mutated_vt.save()
                vc = VehicleClass.objects.filter(
                    scenario=mutated_vt.scenario,
                    consumption__isnull=False,
                    vehicle_types=mutated_vt,
                )
                assert vc.count() == 1
                vc = vc.first()
                vc.vehicle_types.remove(mutated_vt)
                logger.info(f"S.ID:{scenario.id}:{vc=}, {vc.vehicle_types.all()=}")
            else:
                logger.info(
                    f"S.ID:{scenario.id}:{mutated_vt=} will use a consumption table. "
                    "Constant consumption is deleted"
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
    success_name = "simba:lca"
    template_name = "ebustoolbox/costs.html"

    def _build_tco_forms(self, data):
        """Build one form per row that owns a ``tco_parameters`` column.

        Initials come from the stored rows, which ``ensure_fleet_topology`` has already
        seeded from ``defaults/impact/tco.json``, so the page shows the JSON defaults
        until the user overrides them.

        :param data: ``request.POST`` on submit, ``None`` on a plain view.
        :returns: A dict for the template context.
        """
        scenario = self.scenario

        scenario_defaults = impact.scenario_tco_defaults()
        scenario_form = forms.ScenarioTcoForm(
            data=data,
            prefix="scenario",
            initial=forms.ScenarioTcoForm.initial_from(scenario.tco_parameters, scenario_defaults),
        ).mark_defaults(scenario_defaults)

        # The values every battery-electric vehicle type follows unless its own card
        # says otherwise. Battery values are nested one level down; the two forms are
        # separate because they write to two different tables.
        common = impact.vehicle_common_parameters(scenario)
        common_battery = common.get(impact.BATTERY_KEY)
        common_form = forms.VehicleTypeTcoForm(
            data=data,
            prefix="vt-common",
            initial=forms.VehicleTypeTcoForm.initial_from(common),
        ).mark_defaults(impact.vehicle_type_tco_defaults_beb())
        common_battery_form = forms.BatteryTypeTcoForm(
            data=data,
            prefix="bt-common",
            initial=forms.BatteryTypeTcoForm.initial_from(common_battery),
        ).mark_defaults(impact.battery_type_tco_defaults(None))

        stored_overrides = impact.vehicle_overrides(scenario)
        vehicle_rows = []
        for vehicle_type in scenario.vehicletype_set.order_by("id"):
            is_diesel = vehicle_type.energy_source == EnumEnergySource.DIESEL
            form_class = forms.DieselVehicleTypeTcoForm if is_diesel else forms.VehicleTypeTcoForm

            # A form is built for every vehicle type, not only the deviating ones, so
            # that flipping a card to "abweichen" needs no round trip. Whether it is
            # validated and saved is decided by the toggle below.
            # What this card reverts to: the shared block if it could inherit one,
            # otherwise its own JSON defaults. A deviating electric type is measured
            # against what it would have had by following, not against tco.json.
            baseline = impact.vehicle_type_tco_defaults(vehicle_type) if is_diesel else common
            row = {
                "vehicle_type": vehicle_type,
                "form": form_class(
                    data=data,
                    prefix=f"vt-{vehicle_type.id}",
                    initial=form_class.initial_from(
                        vehicle_type.tco_parameters,
                        impact.vehicle_type_tco_defaults(vehicle_type),
                    ),
                ).mark_defaults(baseline),
                "battery_form": None,
                # Diesel types cannot follow the shared block: tco.json prices them
                # differently, so there is nothing sensible for them to inherit.
                "can_follow": not is_diesel,
                "deviates": True,
            }
            if not is_diesel:
                checkbox = self._deviation_checkbox_name(vehicle_type)
                row["checkbox_name"] = checkbox
                row["deviates"] = (
                    checkbox in data
                    if data is not None
                    else impact.vehicle_key(vehicle_type) in stored_overrides
                )

            battery_type = vehicle_type.battery_type
            if battery_type is not None:
                battery_baseline = (
                    impact.battery_type_tco_defaults(vehicle_type.name_short)
                    if is_diesel
                    else common_battery
                )
                row["battery_form"] = forms.BatteryTypeTcoForm(
                    data=data,
                    prefix=f"bt-{battery_type.id}",
                    initial=forms.BatteryTypeTcoForm.initial_from(
                        battery_type.tco_parameters,
                        impact.battery_type_tco_defaults(vehicle_type.name_short),
                    ),
                ).mark_defaults(battery_baseline)
            vehicle_rows.append(row)

        charging_point_rows = []
        for charging_point_type in ChargingPointType.objects.filter(scenario=scenario).order_by(
            "name_short"
        ):
            point_defaults = impact.charging_point_type_tco_defaults(charging_point_type.name_short)
            charging_point_rows.append(
                {
                    "charging_point_type": charging_point_type,
                    "is_depot": charging_point_type.name_short == impact.DEPOT_CPT_NAME_SHORT,
                    "form": forms.ChargingPointTypeTcoForm(
                        data=data,
                        prefix=f"cpt-{charging_point_type.id}",
                        initial=forms.ChargingPointTypeTcoForm.initial_from(
                            charging_point_type.tco_parameters, point_defaults
                        ),
                    ).mark_defaults(point_defaults),
                }
            )

        # Already resolved against the JSON defaults by impact, so initial_from needs
        # no second fallback here.
        infrastructure = impact.charging_infrastructure_parameters(scenario)
        infrastructure_defaults = impact.load_tco_defaults()["charging_infrastructure"]
        infrastructure_forms = {
            key: form_class(
                data=data,
                prefix=f"infra-{key}",
                initial=form_class.initial_from(infrastructure[key]),
            ).mark_defaults(infrastructure_defaults[key])
            for key, form_class in forms.CHARGING_INFRASTRUCTURE_FORMS.items()
        }

        return {
            "scenario_form": scenario_form,
            "general_groups": self._general_groups(scenario_form),
            "common_form": common_form,
            "common_battery_form": common_battery_form,
            "vehicle_rows": vehicle_rows,
            "charging_point_rows": charging_point_rows,
            "charging_categories": self._charging_categories(
                charging_point_rows, infrastructure_forms
            ),
            "infrastructure_forms": infrastructure_forms,
        }

    @staticmethod
    def _general_groups(scenario_form):
        """Group the seventeen scenario-wide fields into readable panels.

        Wartung lives here rather than under the vehicle or charging point panels
        where the design puts it, because eflips-impact cannot express it anywhere
        else: ``vehicle_maint_cost`` is one scenario-level pair keyed
        ``{diesel, electricity}`` (``tco/calculation.py:592``) and cannot deviate per
        vehicle type, and ``infra_maint_cost`` is a single number per charging point
        (``:641``) that cannot be split between depot and station.
        """
        groups = [
            (
                _("Projekt & Finanzierung"),
                ("project_duration", "interest_rate", "inflation_rate", "eta_avail"),
            ),
            (_("Personal"), ("staff_cost", "cost_escalation_rate_staff")),
            (
                _("Energie"),
                (
                    "fuel_cost_electricity",
                    "cost_escalation_rate_electricity",
                    "fuel_cost_diesel",
                    "cost_escalation_rate_diesel",
                ),
            ),
            (
                _("Wartung"),
                (
                    "vehicle_maint_cost_electricity",
                    "vehicle_maint_cost_diesel",
                    "infra_maint_cost",
                ),
            ),
            (
                _("Fixkosten je Fahrzeug"),
                ("insurance", "taxes", "cost_escalation_rate_insurance"),
            ),
            (_("Allgemeine Teuerung"), ("cost_escalation_rate_general",)),
        ]
        return [
            {"title": title, "fields": [scenario_form[name] for name in names]}
            for title, names in groups
        ]

    @staticmethod
    def _charging_categories(charging_point_rows, infrastructure_forms):
        """Pair each charging point type with the site it is built on.

        The two are priced very differently — around 120 000 € for a depot charging
        point against 2 400 000 € for the depot itself — and until now sat in
        unrelated parts of the page.
        """
        by_depot = {row["is_depot"]: row["form"] for row in charging_point_rows}
        return [
            {
                "key": "depot",
                "title": _("Ladepunkte Depot"),
                "point_form": by_depot.get(True),
                "site_form": infrastructure_forms["depot"],
                "site_title": _("Depot (Standort)"),
            },
            {
                "key": "station",
                "title": _("Ladepunkte Station"),
                "point_form": by_depot.get(False),
                "site_form": infrastructure_forms["station"],
                "site_title": _("Ladestation (Standort)"),
            },
        ]

    @staticmethod
    def _deviation_checkbox_name(vehicle_type) -> str:
        """Return the name of the "abweichen" checkbox for one vehicle type."""
        return f"vt-{vehicle_type.id}-deviates"

    def _build_tco_presets(self, context):
        """Build the "copy from another scenario" payload for the template.

        Maps input id to value, so the browser only has to assign values by id: the
        percent scaling and the matching of the other scenario's vehicle types (by
        ``name_short``) are done here, by the same code that renders the fields.

        :param context: The forms built by :meth:`_build_tco_forms`.
        :returns: ``{scenario id: {"name": str, "values": {input id: value}}}``.
        """

        def collect(form_class, prefix, parameters, into, defaults=None):
            other = form_class(prefix=prefix, initial=form_class.initial_from(parameters, defaults))
            for name in other.fields:
                into[other[name].auto_id] = other.initial.get(name)

        scenarios = Scenario.objects.filter(scenario_type=EnumScenarioType.MUTATION)
        presets = {}
        for other_scenario in get_user_scenario_qs(self.request.user, scenarios):
            if other_scenario.id == self.scenario.id:
                continue
            values: dict = {}
            collect(
                forms.ScenarioTcoForm,
                "scenario",
                other_scenario.tco_parameters,
                values,
                impact.scenario_tco_defaults(),
            )

            other_common = impact.vehicle_common_parameters(other_scenario)
            collect(forms.VehicleTypeTcoForm, "vt-common", other_common, values)
            collect(
                forms.BatteryTypeTcoForm,
                "bt-common",
                other_common.get(impact.BATTERY_KEY),
                values,
            )

            other_vehicle_types = {
                vehicle_type.name_short or vehicle_type.name: vehicle_type
                for vehicle_type in other_scenario.vehicletype_set.all()
            }
            for row in context["vehicle_rows"]:
                mine = row["vehicle_type"]
                source = other_vehicle_types.get(mine.name_short or mine.name)
                if source is None:
                    continue  # vehicle type does not exist in the other scenario
                collect(
                    row["form"].__class__,
                    row["form"].prefix,
                    source.tco_parameters,
                    values,
                    impact.vehicle_type_tco_defaults(source),
                )
                if row["battery_form"] is not None and source.battery_type is not None:
                    collect(
                        forms.BatteryTypeTcoForm,
                        row["battery_form"].prefix,
                        source.battery_type.tco_parameters,
                        values,
                        impact.battery_type_tco_defaults(source.name_short),
                    )

            other_infrastructure = impact.charging_infrastructure_parameters(other_scenario)
            for key, form in context["infrastructure_forms"].items():
                collect(form.__class__, form.prefix, other_infrastructure[key], values)

            presets[other_scenario.id] = {"name": other_scenario.name, "values": values}
        return presets

    def _all_tco_forms(self, context):
        """Return the forms that have to validate before the page can be saved.

        A vehicle type that follows the shared block is skipped: its inputs are on the
        page so the card can be flipped without a round trip, but they are hidden, and
        an unfilled hidden input must not block a submit.
        """
        all_forms = [
            context["scenario_form"],
            context["common_form"],
            context["common_battery_form"],
            *context["infrastructure_forms"].values(),
        ]
        all_forms += [row["form"] for row in context["charging_point_rows"]]
        for row in context["vehicle_rows"]:
            if not row["deviates"]:
                continue
            all_forms.append(row["form"])
            if row["battery_form"] is not None:
                all_forms.append(row["battery_form"])
        return all_forms

    def get_context_data(self, **kwargs):
        # None, not {}: an empty dict is still "bound" as far as Django is concerned,
        # so on a GET every required field would already be in error and every input
        # would render with aria-invalid before the user has typed anything.
        data = self.request.POST if self.request.method == "POST" else None

        # The fleet rows a TCO calculation needs are created here rather than in the
        # toolchain so their default values exist while the user is still in the
        # wizard and can be edited before the simulation starts. Idempotent, and runs
        # after ScenarioMixIn.dispatch has checked the user's permission.
        ensure_fleet_topology(self.scenario)
        self.scenario.refresh_from_db()

        context = super().get_context_data(**kwargs)
        context["cost_mode_form"] = forms.CostInputModeForm(data=data, prefix="costsRadio")
        context["radio_values"] = {
            choice[0]: choice[0] for choice in forms.CostInputModeForm.CHOICES
        }
        context.update(self._build_tco_forms(data))
        context["cost_presets"] = self._build_tco_presets(context)
        return context

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        mode_form = context["cost_mode_form"]
        if not mode_form.is_valid():
            # Without surfacing this the page silently re-renders and the click looks
            # like it did nothing at all.
            logger.debug(f"Invalid Costs Form provided: {mode_form.errors.as_json()}")
            context["cost_errors"] = mode_form.errors
            return self.render_to_response(context)

        match mode_form.cleaned_data["input_mode"]:
            case "no_input":
                # Normally nothing to save: ensure_fleet_topology has already written
                # the defaults from defaults/impact/tco.json onto every row. But if the
                # user saved manual values earlier, that call skipped them, so asking
                # for the defaults again has to release the scenario first.
                self._reset_tco_to_defaults()
            case "manual":
                tco_forms = self._all_tco_forms(context)
                if not all(form.is_valid() for form in tco_forms):
                    errors = {}
                    for form in tco_forms:
                        for field, messages in form.errors.items():
                            label = form.fields[field].label or field.replace("_", " ")
                            errors[f"{form.prefix}.{label}"] = messages
                    logger.debug(f"Invalid TCO parameters provided: {errors}")
                    context["cost_errors"] = errors
                    return self.render_to_response(context)
                self._save_tco_forms(context)
            case _:
                raise NotImplementedError(f"Mode {mode_form.cleaned_data['input_mode']}")
        return redirect(reverse(self.success_name, args=[kwargs["task_id"]]))

    @atomic()
    def _reset_tco_to_defaults(self):
        """Hand the scenario's TCO values back to ``defaults/impact/tco.json``.

        Clearing the marker is enough to undo an earlier manual save: the values the
        user entered are still in the columns, but every one of them is overwritten by
        :func:`ebustoolbox.impact.ensure_fleet_topology` on the next call.
        """
        scenario = self.scenario
        parameters = dict(scenario.tco_parameters or {})
        if parameters.pop(impact.USER_EDITED_KEY, None) is None:
            return

        # Dropped rather than left behind: while the marker is clear nothing reads it,
        # so a stale block here would only look authoritative to the next reader. That
        # includes the shared vehicle block, whose absence puts every vehicle type back
        # on the JSON defaults.
        parameters.pop(impact.CHARGING_INFRASTRUCTURE_KEY, None)
        parameters.pop(impact.WEBUS_KEY, None)
        scenario.tco_parameters = parameters
        scenario.save(update_fields=["tco_parameters"])
        impact.ensure_fleet_topology(scenario)
        logger.info(f"S.ID:{scenario.id}:TCO parameters reset to the defaults")

    @atomic()
    def _save_tco_forms(self, context):
        """Write the validated forms back onto the rows they came from.

        Field names match the schema eflips-impact reads, so each form maps to exactly
        one ``tco_parameters`` column and no translation is needed. Values are merged
        into the stored dict rather than replacing it, so keys the form does not cover
        (such as the simulated ``average_electricity_consumption``) survive.

        Vehicle types that follow the shared block are not written here at all. Their
        rows are filled in by ``ensure_fleet_topology`` at the end, which copies the
        block down — the same way station rows are derived from the scenario-level
        charging infrastructure values.
        """
        scenario = self.scenario
        common = context["common_form"].to_tco_parameters()
        common[impact.BATTERY_KEY] = context["common_battery_form"].to_tco_parameters()
        overrides = {
            impact.vehicle_key(row["vehicle_type"])
            for row in context["vehicle_rows"]
            if row["can_follow"] and row["deviates"]
        }

        # Marked as the user's: ebustoolbox.impact stops re-seeding these rows from
        # defaults/impact/tco.json. Has to be set before the ensure_fleet_topology
        # call below, which would otherwise undo everything this method just wrote.
        scenario.tco_parameters = impact.mark_tco_parameters_edited(
            {
                **(scenario.tco_parameters or {}),
                **context["scenario_form"].to_tco_parameters(),
                impact.CHARGING_INFRASTRUCTURE_KEY: {
                    key: form.to_tco_parameters()
                    for key, form in context["infrastructure_forms"].items()
                },
                impact.WEBUS_KEY: impact.webus_block(common, overrides),
            }
        )
        scenario.save(update_fields=["tco_parameters"])

        for row in context["vehicle_rows"]:
            if not row["deviates"]:
                continue
            vehicle_type = row["vehicle_type"]
            vehicle_type.tco_parameters = {
                **(vehicle_type.tco_parameters or {}),
                **row["form"].to_tco_parameters(),
            }
            vehicle_type.save(update_fields=["tco_parameters"])

            if row["battery_form"] is not None:
                battery_type = vehicle_type.battery_type
                battery_type.tco_parameters = {
                    **(battery_type.tco_parameters or {}),
                    **row["battery_form"].to_tco_parameters(),
                }
                battery_type.save(update_fields=["tco_parameters"])

        for row in context["charging_point_rows"]:
            charging_point_type = row["charging_point_type"]
            charging_point_type.tco_parameters = {
                **(charging_point_type.tco_parameters or {}),
                **row["form"].to_tco_parameters(),
            }
            charging_point_type.save(update_fields=["tco_parameters"])

        # Station rows are derived from the two scenario-level sets just saved.
        impact.ensure_fleet_topology(scenario)


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
                        standard_block_length=8,
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
            logger.info(f"S.ID:{scenario.id}:Returning {progress=} in context")

        start, end = data.get_start_end_time(scenario.parent)
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
        context["sim_duration"] = (
            f"{german_weekdays[start.weekday()]} {start.strftime(_format)} - "
            f"{german_weekdays[end.weekday()]} {end.strftime(_format)}"
        )
        sim_temps = SimulationTemperatures.objects.get(scenario=scenario)
        context["temperature_average"] = sim_temps.temperature_average
        context["temperature_extreme"] = sim_temps.temperature_extreme
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

        context["electrified_stations"] = [
            station.name for station in scenario_stations.filter(is_electrified=True).order_by("id")
        ]
        excluded = Station.objects.filter(scenario=scenario, is_electrifiable=False)
        excluded_ids = excluded.values_list("id", flat=True)
        context["automatic_stations"] = scenario_stations.filter(
            is_electrified=False, is_electrifiable=True
        ).order_by("id")
        context["excluded_stations"] = scenario_stations.filter(id__in=excluded_ids).order_by("id")
        context["depot_wishes"] = (
            DepotConfigurationWish.objects.filter(scenario=scenario)
            .prefetch_related("areainformation_set")
            .prefetch_related("areainformation_set__vehicle_type")
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
        scenario = get_object_or_404(Scenario, task_id=task_id)
        context["task_id"] = task_id
        scenario = get_object_or_404(Scenario, task_id=task_id)
        context["avg_scenario"] = Scenario.objects.filter(
            parent=scenario,
            scenario_type=EnumScenarioType.SIMULATION,
            simulationtype__sim_type=EnumSimulationType.DEFAULT,
        ).first()
        extScenario = Scenario.objects.filter(
            parent=scenario,
            scenario_type=EnumScenarioType.SIMULATION,
            simulationtype__sim_type=EnumSimulationType.SIZING,
        ).first()
        context["ext_scenario"] = extScenario
        context["depots"] = Depot.objects.filter(scenario=extScenario)
        context["scenario"] = scenario
        notifications = Notification.objects.filter(scenario=scenario)
        context["notifications"] = tasks.get_notfications_dict(notifications)
        center = self.get_scenario_center(scenario)
        # Update mapengine_setup JS sees the center
        context["mapengine_setup"] = {**context["mapengine_setup"], "center": center}
        return context


def get_depots(scenario, start: datetime.datetime, td: datetime.timedelta):
    # Get filtered depots by simrange
    parent = scenario.parent
    rots = tasks.get_rotations_by_timespan(parent, td, start)
    station_ids = (
        Trip.objects.filter(rotation__in=rots)
        .values_list("route__departure_station", "route__arrival_station")
        .distinct()
    )
    station_ids = set(x for pair in station_ids for x in pair)
    depots_query = Station.objects.filter(id__in=station_ids)
    depots_query = depots_query.filter(charge_type=EnumChargeType.DEPOT).order_by("id")
    return depots_query


@require_POST
def cancel_upload(request: HttpRequest, task_id: str):
    # cause a SoftTimeLimitExceeded in task and redirect to schedule upload
    AsyncResult(task_id).revoke(terminate=True, signal="SIGUSR1")
    return redirect(reverse("simba:schedule"))


def merge_and_run(request: HttpRequest, task_id: str):
    scenario = get_scenario_and_assert_authorization(request, task_id)

    simulation_progress = Progress.objects.filter(
        scenario=scenario,
        progress_type=EnumProgress.RUNNING_SIMULATION,
    )

    # Users should not keep failed scenarios
    # This way the children of a scenario can be uniquely linked to their parent
    # (TODO: Discuss)
    logger.info(f"S.ID:{scenario.id}:Deleting failed previous child-scenarios")
    logger.info(str(Scenario.objects.filter(parent=scenario).delete()))

    # delete old notifications
    logger.info(f"S.ID:{scenario.id}:Deleting failed previous Simulation Notifications")
    logger.info(
        str(
            Notification.objects.filter(
                scenario=scenario,
                notification_type__in=[
                    EnumNotificationType.STATION_OPTIMIZATION_SKIPPED,
                    EnumNotificationType.UNSTABLE_DEPOT_WARNING,
                    EnumNotificationType.DELAYED_TRIP_WARNING,
                    EnumNotificationType.UNEXPECTED_ERROR,
                    EnumNotificationType.ADDED_ELECTRIFICATION,
                    EnumNotificationType.LOW_SOC_BLOCKS,
                ],
            ).delete()
        )
    )

    if not request.user.is_superuser:
        # Delete failed scenarios
        if simulation_progress.filter(running=True).exists():
            error_text = _("Starting multiple Simulations from the same source is not allowed")
            logger.info(error_text)
            return HttpResponseForbidden(error_text)

        if simulation_progress.filter(success=True).exists():
            error_text = _("Starting a Simulation which was sucessfully simulated is not allowed")
            logger.info(error_text)
            return HttpResponseForbidden(error_text)

    sim_task_id = get_unique_task_id()

    simulation_progress.delete()
    progress = Progress.objects.create(
        scenario=scenario,
        progress_type=EnumProgress.RUNNING_SIMULATION,
        task_id=get_unique_task_id(),
    )
    logger.info(f"S.ID:{scenario.id}:Running Toolchain.")
    sizing_task_id = get_unique_task_id()
    # create scenario from mutation and parent and simulate it
    try:
        async_result = tasks.run_and_merge_scenarios.apply_async(
            (scenario.id, sim_task_id, sizing_task_id),
            task_id=str(progress.task_id),
        )
        assert async_result.task_id == progress.task_id, "Task ids are expected to be equal"
        assert async_result.task_id != sim_task_id, "Task ids are expected to be equal"
    except Exception:
        progress.errors.append(
            _("Ein unerwarteter Fehler ist aufgetreten. Wenden Sie sich an ihren Administrator")
        )
        progress.set_failed()
        logger.error(traceback.format_exc())

    context = {}
    context["progress_id"] = progress.task_id
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
        logger.info(f"S.ID:{scenario.id}:Running Toolchain.")
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
        scenario_type__in=[EnumScenarioType.MUTATION, EnumScenarioType.SOURCE_FILE]
    )
    scenarios = (
        get_user_scenario_qs(request.user, scenario_qs=base_qs)
        .prefetch_related("scenario_set")
        .reverse()
    )
    # get task status from task_id for each scenario
    scenario_list = list()
    for scenario in scenarios:
        # The progress task_id is now unique.
        # During run and merge each simulation gets progress which is set
        progress = Progress.objects.filter(
            scenario=scenario, progress_type=EnumProgress.RUNNING_SIMULATION
        )
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

        # Give each scenario a reference to its "variante".
        # For Mutations its a reference to itself.
        # For Source Files its also a reference to themselves since they are handled differently
        # Simulations used to get a reference to their parents but they are not shown in the dashboard
        # Simulations are instead linked through their mutation/parent, since results show
        # two scenarios (extreme and average)

        scenario.variante = scenario.id
        match scenario.scenario_type:
            case EnumScenarioType.MUTATION:
                pass
            case EnumScenarioType.SOURCE_FILE:
                scenario.state = "ready_for_copy"
            case _:
                logger.error(
                    f"Dashboard lookup of variante for scenario {scenario} failed unexpectedly"
                )

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
        scenario_type=EnumScenarioType.MUTATION, finished__isnull=False
    )
    scenarios = get_user_scenario_qs(request.user, scenario_qs=base_qs)
    scenario_dict = dict()
    for scenario in scenarios:
        if not scenario.finished:
            # filter after union not supported
            continue
        sim_scenario = Scenario.objects.filter(
            parent=scenario, simulationtype__sim_type=EnumSimulationType.DEFAULT
        ).first()
        if not sim_scenario:
            # Missing Simulation Scenario. Can't compare and continue
            continue
        # TODO compare with the right child scenario
        stations = sim_scenario.station_set.all()
        num_electrified_opps = stations.filter(charge_type=EnumChargeType.OPPORTUNITY).count()
        # sum up charging places at depots, defaults to 0 for null values
        num_cs_deps = stations.filter(charge_type=EnumChargeType.DEPOT).aggregate(
            cs=Coalesce(Sum("amount_charging_places"), 0)
        )["cs"]
        rotations = sim_scenario.rotation_set.all()
        events = sim_scenario.event_set.all()
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

        scenario_dict[sim_scenario.id] = {
            _("Name"): sim_scenario.name,
            _("Erstellt"): sim_scenario.created.strftime("%d.%m.%Y"),
            _("Fahrzeuge"): sim_scenario.vehicle_set.count(),
            _("Umläufe"): sim_scenario.rotation_set.count(),
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


def get_critical_rotations(request, task_id: str):
    """Returns data about rotations (critical vs. non-critical)"""
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    s = Scenario.objects.get(task_id=task_id)
    df = data.get_critical_rotations_as_dataframe(s.id, None)
    if file_format == "json":
        data_obj = (
            df.groupby("SOC_category").size().reset_index(name="count").to_dict(orient="records")
        )
        return JsonResponse({"data": data_obj}, safe=True)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_bustype(request, task_id: str):
    """Returns data about vehicle type distribution"""
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    s = Scenario.objects.get(task_id=task_id)
    df = data.get_vehicle_types(s.id, None)
    if file_format == "json":
        return JsonResponse({"data": df.to_dict(orient="records")}, safe=True)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_soc_data(request, task_id: str):
    """
    Returns SOC (State of Charge) data over time.
    """
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    s = Scenario.objects.get(task_id=task_id)

    if file_format == "json":
        return JsonResponse(data.get_soc_as_json(task_id), safe=True)
    elif file_format == "csv":
        csv_text = data.get_soc_as_dataframe(s.id, data.get_all_buses(task_id))
        return HttpResponse(csv_text.to_csv(index=False), content_type="text/csv")
    raise HttpResponse(status=400)


def get_binned_soc_data(request, task_id: str):
    """
    Returns binned SOC histogram data over time, forward-filled to hourly resolution,
    ensuring one (the lowest) SOC entry per vehicle per hour.
    """
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    df = data.get_binned_soc(task_id)
    if file_format == "json":
        return JsonResponse({"data": df.to_dict(orient="records")}, safe=True)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_power_draw(request, task_id: str):
    """
    Returns power draw data over time by station ID for selected buses.
    """
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    response_data = data.get_power_draw(request, task_id)

    return JsonResponse({"data": response_data})


def get_stats(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    response_data = data.get_stats(task_id)
    return JsonResponse(response_data)


def get_speed_hist(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    df = data.get_speed_hist(task_id)
    if file_format == "json":
        return JsonResponse(df.to_dict(orient="list"), safe=True)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_dist_hist(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "").lower()
    response_data = data.get_dist_hist(task_id)
    if not file_format:
        return JsonResponse(response_data, safe=True)
    if file_format == "json":
        return JsonResponse(response_data["data"], safe=True)
    return HttpResponse(status=400)


def get_power_draw_and_occ(request, task_id: str, depot_id: int | None = None):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "").lower()
    response_data = data.get_power_draw_and_occ(task_id, depot_id)
    if not file_format:
        response_data["data"] = response_data["data"].to_dict(orient="records")
        return JsonResponse(response_data, safe=True)
    df = response_data["data"]
    if file_format == "json":
        return JsonResponse({"data": df.to_dict(orient="records")}, safe=True)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_gantt(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    scenario = Scenario.objects.get(task_id=task_id)
    df = data.recent_memoizer(data.get_gantt, scenario.id)(scenario.id)
    if file_format == "json":
        return JsonResponse({"data": df.to_dict(orient="records")}, safe=True)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_tco(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    tco = data.get_tco(task_id)  # tco_result JSON field from Scenario
    if file_format == "json":
        return JsonResponse(tco)
    elif file_format == "csv":
        return HttpResponse(tco.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


def get_lca(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    # JSON only: the payload is nested, so there is no single sensible CSV of it.
    return JsonResponse(data.get_lca(task_id))


def get_piecharts(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()

    # Get the two DataFrames
    critical_df, bus_df = data.get_combined_piecharts(task_id)

    if file_format == "json":
        # Convert DataFrames to nested dicts
        json_data = {
            "critical_rotations": dict(zip(critical_df["category"], critical_df["count"])),
            "bus_types": dict(zip(bus_df["category"], bus_df["count"])),
        }
        return JsonResponse(json_data, safe=True)

    elif file_format == "csv":
        # Build CSV string manually, because of empty row between tables
        lines = []

        # Header and rows for critical rotations
        lines.append(",".join(critical_df.columns))
        for row in critical_df.itertuples(index=False):
            lines.append(",".join(str(x) for x in row))

        # Empty row
        lines.append("")

        # Header and rows for bus types
        lines.append(",".join(bus_df.columns))
        for row in bus_df.itertuples(index=False):
            lines.append(",".join(str(x) for x in row))

        csv_string = "\n".join(lines)
        response = HttpResponse(csv_string, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="piecharts.csv"'
        return response
    return HttpResponse(status=400)


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
    if (
        scenario.scenario_type == EnumScenarioType.SOURCE_FILE
        or scenario.scenario_type == EnumScenarioType.SOURCE
    ):
        return HttpResponseForbidden(
            _("Das Scenario ohne Filter darf nicht mit allen Nachfolgern heruntergeladen werden.")
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
    response = HttpResponse(json_data, content_type="application/json")
    response["Content-Disposition"] = f"attachment; filename=scenario_{scenario.name}_data.json"
    return response


def export_scenario(request):
    """Allow admins and authorized users to download a json export of their scenario"""
    # Raise an exception if user is not authorized for this task_id
    task_ids = [_id for _id in request.GET.getlist("task_id")]
    if not task_ids:
        return Http404(_("Sie müssen mindestens eine task_id als GET Request angeben"))

    for task_id in task_ids:
        permission = AuthorizedMixIn.get_permission(request.user, task_id)
        if not permission:
            return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))

    data = {}
    for task_id in task_ids:
        scenario = Scenario.objects.get(task_id=task_id)
        child = None
        if scenario.scenario_type == EnumScenarioType.MUTATION:
            task_id = get_unique_task_id()
            child = merge_scenario(scenario.id, task_id)
            scenario = child
        exporter = ScenarioJSONImporterExporter()
        visit_all_scenario_queries(exporter, scenario)
        json_data = exporter.renderJSON()
        file_name = f"scenario_{scenario.simulationtype_set.first().sim_type}_{task_id[:3]}.json"
        data[file_name] = json_data
        # If a child was created delete it
        if child is not None:
            child.delete()
    if len(task_ids) == 1:
        response = HttpResponse(json_data, content_type="application/json")
        response["Content-Disposition"] = f"attachment; filename=scenario_{scenario.name}.json"
    else:
        zipped_bytes = to_zip(list(data.keys()), list(data.values()))
        response = HttpResponse(zipped_bytes.getvalue(), content_type="application/json")
        response["Content-Disposition"] = "attachment; filename=scenarios.zip"
    return response


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
            scenario: Scenario
            if scenario.scenario_type == EnumScenarioType.PUBLIC_DATA:
                return HttpResponseForbidden("Public Data Scenarios can not be imported")
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
            _(f"Scenario successfully imported with task_id {task_id}. ") + redirect_suggestion
        )
    return HttpResponseBadRequest(_("Use POST or GET"))


def loadTester(request, task_id: str):
    if not request.user.is_authenticated:
        raise Http404()
    if not request.user.is_superuser:
        raise Http404()
    scenarios = request.GET.getlist("scenario")
    scenario = Scenario.objects.get(task_id=task_id)
    context = {}
    context["scenario"] = scenario
    context["scenarios"] = scenarios
    if request.method == "GET":
        progresses = Progress.objects.filter(scenario=scenario, running=True)
        context["progresses"] = progresses
        return render(request, "ebustoolbox/load_test.html", context)
    elif request.method == "POST":
        repeats = int(request.POST.get("repeats", 0))
        progresses = []
        for i in range(repeats):
            scenario.task_id = get_unique_task_id()
            s, _ = deepcopy_scenario(scenario)
            progress = Progress.objects.create(
                scenario=s,
                progress_type=EnumProgress.RUNNING_SIMULATION,
                task_id=get_unique_task_id(),
            )
            progresses.append(progress)
            try:
                tasks.run_and_merge_scenarios.apply_async(
                    (s.id, get_unique_task_id(), get_unique_task_id()),
                    task_id=str(progress.task_id),
                )
            except Exception:
                progress.errors.append(
                    _(
                        "Ein unerwarteter Fehler ist aufgetreten. Wenden Sie sich an ihren Administrator"
                    )
                )
                progress.set_failed()
                logger.error(traceback.format_exc())
        context["progresses"] = progresses
        return render(request, "ebustoolbox/load_test.html", context)
    return HttpResponse("wrong method")


def delete_scenario(request, task_id):
    if request.method != "POST":
        return HttpResponse(status=405)  # method not allowed
    scenario = get_object_or_404(Scenario, task_id=task_id)
    if scenario.manager and scenario.manager != request.user:
        return HttpResponse(status=403)  # forbidden: only manager may delete scenario
    scenario.delete()
    return redirect(reverse("simba:dashboard"))


def get_cumulative_energy(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    response_data = data.get_cumulative_energy(task_id)
    return JsonResponse(response_data)


def get_rotation_table_data(request, task_id: str):
    permission = AuthorizedMixIn.get_permission(request.user, task_id)
    if not permission:
        return HttpResponseForbidden(_("Sie haben keinen Zugriff auf diese Seite"))
    file_format = request.GET.get("format", "json").lower()
    df = data.get_rotation_table_data(task_id)
    if file_format == "json":
        return JsonResponse(df.to_dict(orient="records"), safe=False)
    elif file_format == "csv":
        return HttpResponse(df.to_csv(index=False), content_type="text/csv")
    return HttpResponse(status=400)


class LcaView(ScenarioMixIn, TemplateView):
    """The Ökobilanz wizard step, between Kosten and Depots.

    A placeholder: the LCA parameters are not editable yet, so the only action is
    taking the defaults from ``defaults/impact/lca.json``. The table on the page shows
    which rows they reached.

    Seeding here writes to the scenario the wizard edits, so the values are in place
    before the simulation and travel to the simulated scenario with the rows that hold
    them. The toolchain seeds again on its own, so skipping this step costs nothing --
    it only makes the values visible and the moment repeatable.
    """

    success_name = "simba:depots"
    template_name = "ebustoolbox/lca.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scenario = self.scenario

        # Reached through the reverse managers rather than the model classes, so this
        # view needs no import the rest of the module does not already have.
        context["vehicle_types"] = scenario.vehicletype_set.order_by("id")
        context["battery_types"] = scenario.batterytype_set.order_by("id")
        context["charging_point_types"] = scenario.chargingpointtype_set.order_by("id")
        context["lca_rows"] = [
            *context["vehicle_types"],
            *context["battery_types"],
            *context["charging_point_types"],
        ]
        return context

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        from ebustoolbox import impact

        # Two submits land here: the defaults button, which stays on the step so its
        # effect can be seen, and the wizard's "Weiter", which carries no name.
        if "lca_defaults" not in request.POST:
            return redirect(reverse(self.success_name, args=[kwargs["task_id"]]))

        scenario = self.scenario
        # The rows have to exist before they can be parameterised, and a scenario that
        # never went through the costs page has none.
        impact.ensure_fleet_topology(scenario)
        impact.ensure_lca_parameters(scenario)
        return redirect(reverse("simba:lca", args=[kwargs["task_id"]]))
