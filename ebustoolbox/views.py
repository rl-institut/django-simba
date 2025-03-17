import logging
import traceback
import uuid
from datetime import timedelta, datetime, timezone as tz

import pytz
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing, mail
from django.core.exceptions import ObjectDoesNotExist
from django.db.transaction import atomic
from django.forms import formset_factory
from django.http import FileResponse, HttpResponse, JsonResponse, HttpRequest, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils import timezone
from django.views.generic import TemplateView, FormView
from django.views.decorators.http import require_GET, require_POST
from eflips.depot.api import simulate_scenario  # noqa

from core.models import Progress

from celery.result import AsyncResult

# Unused import of dash_app needed to register app
from dash_app import dash_app, ids  # noqa: F401
from django_mapengine.views import MapEngineMixin
from . import tasks, schedule_readers, forms
from .forms import (
    ElectrificationOptionsForm,
    SimulationParameters,
    VehicleTypeForm,
    VehicleTypeSelectionForm,
    FileUploadForm,
    ScenarioSelection,
    ManualTcoForm,
    ManualLcaForm,
    DepotChargingAreaForm,
)
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
    ScenarioDescription,
    StationMutation,
    StationElectrificationExclusions,
)

logger = logging.getLogger("custom")


def progress2(request: HttpRequest, progress_id):
    context = {"progress_id": progress_id, "status": "", "current_progress": 0}
    context |= {"finished": False}
    try:
        progress = Progress.objects.get(task_id=progress_id)
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
    response = render(request, "core/progress.html", context)
    response["HX-Trigger"] = hx_trigger
    response.status_code = status_code
    return response


def get_unique_progress_or_none(scenario_task_id):
    progress_db = Progress.objects.filter(scenario__task_id=scenario_task_id)
    assert len(progress_db) <= 1, "Only single progress of a scenario upload progress should exist"
    return progress_db.first()


class TripsView(FormView):

    template_name = "ebustoolbox/trips.html"
    form_class = forms.TripsForm
    success_name = "simba:vehicles"

    def get_context_data(self, **kwargs):
        context = super(TripsView, self).get_context_data(**kwargs)
        task_id = kwargs.get("task_id")
        if task_id:
            # scenario is created so we pass the progress id so a progress bar can be shown
            progress_db = get_unique_progress_or_none(kwargs.get("task_id"))
            context["progress_id"] = progress_db.task_id if progress_db else None
            # progress might not yet be created in the db, but it might have been passed
            # as kwargs
            if context["progress_id"] is None:
                context["progress_id"] = kwargs.get("progress_id")
            scenario = Scenario.objects.get(task_id=task_id)
            form_data = {
                "scenario_name": scenario.name,
                "description": ScenarioDescription.objects.get(scenario=scenario),
                # ToDo: where is this setting stored
                "existing_scenario": None,
            }
            files = [UploadedFile.objects.filter(scenario=scenario).first()]
            form = forms.TripsForm(data=form_data, files=files)
            context["form"] = form

        scenarios = [DefaultScenario.objects.first().scenario]
        if User.objects.filter(id=self.request.user.id).exists():
            # ToDo-> Refactor to function to be reused in all scenario fetches
            user = self.request.user
            # Scenarios where the user is the manager
            scenarios_as_manager = Scenario.objects.filter(manager=user)
            scenarios.extend(scenarios_as_manager)
            # Scenarios accessible through UserGroup
            scenarios_in_groups = Scenario.objects.filter(usergroup__users=user)
            scenarios.extend(scenarios_in_groups)
        context["scenarios"] = scenarios
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
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        """Handles successful form submission."""
        cleaned_data = form.cleaned_data
        task_id = get_unique_task_id()
        # Create a scenario and description

        # Get a User as manager or none
        manager = None
        if self.request.user.is_authenticated:
            manager = self.request.user

        scenario = Scenario.objects.create(
            name=cleaned_data["scenario_name"], task_id=task_id, manager=manager
        )
        parent, scenario = tasks.get_parent(scenario)
        _ = ScenarioDescription.objects.create(
            scenario=scenario, description=cleaned_data["description"]
        )
        data_file = form.files["data_file"]
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
            if file_suffix == "csv":
                # change the file naming according to SimbaScheduleReader
                async_result = tasks.init_db_with_trips.apply_async(
                    (scenario.id, 1, {"file_path": files["data_file"]}, {})
                )
            elif file_suffix == "zip":

                async_result = tasks.init_db_with_trips.apply_async(
                    (scenario.id, 3, files, cleaned_data)
                )
            else:
                raise NotImplementedError(f"Unsupported FileType file_suffix {file_suffix}")

            progress_id = async_result.task_id
        elif form.existing_scenario:
            raise NotImplementedError("Using an existing Scenario needs implementation")
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
        response["HX-Location"] = reverse("simba:trips", args=[str(task_id), 1])
        return response

    def form_invalid(self, form):
        """Handles form validation errors."""
        return super().form_invalid(form)


def get_scenario_and_assert_authorization(request, task_id):
    scenario = get_object_or_404(Scenario, task_id=task_id)
    if scenario.manager and scenario.manager != request.user:
        raise Http404
    return scenario


class VehiclesView(TemplateView):
    template_name = "ebustoolbox/vehicles.html"
    form_class = forms.SimulationParameters
    success_name = "simba:stations"

    def get_context_data(self, scenario, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get context for Simulation Range
        simulation_parameters_form = self.form_class()
        trips = Trip.objects.filter(scenario=scenario.parent).order_by("departure_time")
        start_date = trips.first().departure_time.date().isoformat()
        start_time = trips.first().departure_time.time().isoformat()
        end_date = trips.last().arrival_time.date().isoformat()
        end_time = trips.last().arrival_time.time().isoformat()
        sim_range = SimulationRange.objects.filter(scenario=scenario).first()
        if sim_range:
            assert SimulationRange.objects.filter(scenario=scenario).count() == 1
            # Times are provided to the context not via form, since different widgets
            # are used.
            initial_start_date = sim_range.start.date().isoformat()
            initial_start_time = sim_range.start.time().isoformat()
            initial_end_date = sim_range.end.date().isoformat()
            initial_end_time = sim_range.end.time().isoformat()
            simulation_parameters_form = self.form_class(
                data={"temperature": sim_range.temperature}
            )
        else:
            initial_start_date = start_date
            initial_start_time = start_time
            initial_end_date = end_date
            initial_end_time = end_time
        context |= {"min_date": start_date, "max_date": end_date}
        context |= {"start_date": initial_start_date, "end_date": initial_end_date}
        context |= {"initial_start_time": initial_start_time, "initial_end_time": initial_end_time}
        context |= {"task_id": scenario.task_id, "form": simulation_parameters_form}

        # Get context for vehicle types
        default_scenario = DefaultScenario.objects.first().scenario
        default_vehicle_types = VehicleType.objects.filter(
            scenario=default_scenario, opportunity_charging_capable=True
        )
        vehicle_modification = self.generate_vehicle_modification_forms(scenario)

        context["vehicle_modification"] = vehicle_modification
        context["choice_vts"] = default_vehicle_types

        # Todo @Moritz/Stefan/Ludger Is this needed?
        # check if can be skipped by seeing if vehicle types have relevant data
        # This is an edge case if the scenario is not created through the web interface
        # skippable = reverse("simba:depots", args=[str(task_id)])
        # for vt in vehicle_types:
        #     if vt.consumption is None:
        #         skippable = False
        #         break
        # context["skippable"] = skippable
        return context

    @staticmethod
    def generate_vehicle_modification_forms(scenario):
        parent = scenario.parent
        default_scenario = DefaultScenario.objects.first().scenario
        parent_vehicle_types = VehicleType.objects.filter(scenario=parent)
        # Get all default vehicle types. Only Opportunity charging capable for now
        # Expand the query for desired vehicle types which can be selected
        default_vehicle_types = VehicleType.objects.filter(
            scenario=default_scenario, opportunity_charging_capable=True
        )
        # if the child / mutation scenario has no vehicle types create them
        # ToDo could be cleaner. Check instead if a VehicleTypeMutation exists
        # for each parent vehicle type, with a mutated vehicle type of this scenario. PseudoCode:
        # for each parent_vt
        # VehicleTypeMutation(
        #       original_vehicle_type=parent_vt,
        #       mutated_vehicle_type__scenario=scenario)
        #       .exists()
        child_vehicle_types = VehicleType.objects.filter(scenario=scenario)
        if child_vehicle_types.count() == 0:
            for vt in parent_vehicle_types:
                org_vt_id = vt.id
                vt.id = None
                vt.scenario = scenario
                vt.save()
                org_vt = VehicleType.objects.get(id=org_vt_id)
                VehicleTypeMutation.objects.create(
                    original_vehicle_type=org_vt, mutated_vehicle_type=vt
                )
        for vt in child_vehicle_types:
            vt_select, _ = VehicleTypeSelection.objects.get_or_create(vehicle_type=vt)
        vehicle_modification = {}
        for vt in child_vehicle_types:
            vehicle_modification[vt.id] = {
                "vehicle_type": vt,
                "vehicle_choices": default_vehicle_types,
                "selection": VehiclesView.get_VehicleTypeSelectionForm()({}, vt),
                "vehicle_modification": VehiclesView.get_VehicleTypeForm()({}, vt),
            }
        return vehicle_modification

    def get(self, request, *args, **kwargs):
        scenario = get_scenario_and_assert_authorization(request, kwargs["task_id"])
        return self.render_to_response(self.get_context_data(scenario, **kwargs))

    @staticmethod
    def get_VehicleTypeForm():
        def builder(request_post, vehicle_type):
            return VehicleTypeForm(
                request_post, instance=vehicle_type, prefix=f"mutation_{vehicle_type.id}"
            )

        return builder

    @staticmethod
    def get_VehicleTypeSelectionForm():
        """Form builder for vehicle type selection.

        Validation can automatically validate against vehicle_type and selected vehicle_type,
        e.g. the default vehicle type.
        This gurantees only the server side defined choices can be made.
        """
        default_scenario = DefaultScenario.objects.first().scenario
        default_vehicle_types = VehicleType.objects.filter(
            scenario=default_scenario, opportunity_charging_capable=True
        )

        def builder(request_post, vehicle_type):
            return VehicleTypeSelectionForm(
                request_post,
                prefix=f"selection_{vehicle_type.id}",
                vehicle_type=vehicle_type,
                choices_queryset=default_vehicle_types,
            )

        return builder

    def post(self, request, *args, **kwargs):
        scenario = get_scenario_and_assert_authorization(request, kwargs.get("task_id"))

        _format = "%Y-%m-%d %H:%M:%S"
        start_dt = datetime.strptime(
            f"{request.POST['start-date']} {request.POST['start-time']}", _format
        )
        start_dt_utc = start_dt.replace(tzinfo=pytz.UTC)
        end_dt = datetime.strptime(
            f"{request.POST['end-date']} {request.POST['end-time']}", _format
        )
        end_dt_utc = end_dt.replace(tzinfo=pytz.UTC)
        forms = []
        form = SimulationParameters(
            data={
                "start": start_dt_utc,
                "end": end_dt_utc,
                "temperature": request.POST["temperature"],
            }
        )
        if form.is_valid():
            instance = form.save(commit=False)
            instance.scenario = scenario
            if SimulationRange.objects.filter(scenario=scenario).exists():
                instance.id = SimulationRange.objects.get(scenario=scenario).id

            if (
                tasks.get_rotations_by_start_end(
                    scenario.parent, instance.start, instance.end
                ).count()
                == 0
            ):
                form.errors.append("In dieser Zeitspanne starten keine Umläufe.")
            else:
                # Not Valid
                instance.save()
        forms.append(form)

        context = self.get_context_data(scenario=scenario, **kwargs)
        vehicle_modification = context["vehicle_modification"]

        context["form"] = form

        # Validate vehicle forms
        # Expand the query for desired vehicle types which can be selected
        # ToDo: post data could be filled a get_context_data
        for vt_id, d_values in vehicle_modification.items():
            vt = VehicleType.objects.get(id=vt_id)
            form = self.get_VehicleTypeSelectionForm()(request.POST, vt)
            d_values["selection"] = form
            forms.append(form)
            form = self.get_VehicleTypeForm()(request.POST, vt)
            d_values["vehicle_modification"] = form
            forms.append(form)

        if all(f.is_valid() for f in forms):
            return self.forms_valid(forms, scenario)

        else:
            for f in forms:
                if not f.is_valid():
                    logger.debug(f"{f} is invalid")
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

        VehicleTypeForms = list(filter(lambda x: x._meta.model == VehicleType, forms))
        for form in VehicleTypeForms:
            # Mutate the vehicle according to the selected default vehicle
            instance = form.instance
            d_vt = VehicleTypeSelection.objects.get(vehicle_type=instance).default_vehicle_type
            d_vt.id = instance.id
            d_vt.scenario = instance.scenario
            d_vt.name = instance.name
            d_vt.name_short = instance.name_short
            d_vt.save()
            # Overwrite the instance with the data from the form
            VehicleTypeForm(self.request.POST, instance=d_vt, prefix=form.prefix).save()
        response = redirect(reverse(self.success_name, args=[scenario.task_id]))
        return response

    def form_invalid(self, form, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs, form=form))


class StationsView(TemplateView):
    template_name = "ebustoolbox/stations.html"
    success_name = "simba:costs"

    @staticmethod
    def get_station_prefix(station):
        return f"station_{station.id}"

    def get_context_data(self, **kwargs):
        scenario = get_scenario_and_assert_authorization(self.request, kwargs["task_id"])
        context = {}
        context |= {
            "stations": {
                stat.id: stat
                for stat in Station.objects.filter(scenario=scenario).exclude(
                    charge_type=EnumChargeType.DEPOT
                )
            }
        }
        data = self.request.POST
        if self.request.method != "POST":
            data = {}
        form = forms.StationModeForm(data)
        context["calculation_mode_form"] = form
        choice = form.CHOICES[0][0]
        assert choice == "automatic"
        context["automatic_value"] = choice

        choice = form.CHOICES[1][0]
        assert choice == "constant_power"
        context["constant_power_value"] = choice

        choice = form.CHOICES[2][0]
        assert choice == "manual"
        context["manual_value"] = choice

        context["charging_power_form"] = forms.ChargingPowerForm(data)
        context["stations_forms"] = dict()
        context["stations_exclude_forms"] = dict()
        for station in context["stations"].values():
            context["stations_forms"][station.id] = forms.StationForm(
                data, instance=station, prefix=self.get_station_prefix(station)
            )
            context["stations_exclude_forms"][station.id] = forms.StationExcludedForm(
                data, prefix=self.get_station_prefix(station)
            )

        return context

    def get(self, request, *args, **kwargs):
        # Make sure the scenario has its own stations which are linked to its parent scenario
        scenario = get_scenario_and_assert_authorization(self.request, kwargs["task_id"])
        station_mutations = StationMutation.objects.filter(
            original_station__scenario=scenario.parent, mutated_original_station__scenario=scenario
        )
        if station_mutations.count() != Station.objects.filter(scenario=scenario.parent).count():
            # Not every station from the parent scenario is linked to a new station
            # Delete the current stations, create new ones and link them
            ebustoolbox.tasks.create_station_mutations(scenario)
        return self.render_to_response(
            self.get_context_data(**kwargs),
        )

    def post(self, request, *args, **kwargs):
        scenario = get_scenario_and_assert_authorization(self.request, kwargs["task_id"])
        context = self.get_context_data(**kwargs)
        calculation_mode_form = context["calculation_mode_form"]
        if not calculation_mode_form.is_valid():
            return self.render_to_response(**kwargs)
        match calculation_mode_form.cleaned_data["calculation_mode"]:
            case "automatic":
                # Reset the scenario to its parent state. No Stations are excluded
                StationElectrificationExclusions.objects.filter(scenario=scenario).delete()
                ebustoolbox.tasks.create_station_mutations(scenario)
                pass
            case "constant_power":
                form = context["charging_power_form"]
                if not form.is_valid():
                    return self.render_to_response(context)
                stations = Station.objects.filter(scenario=scenario)
                for station in stations:
                    station.power_total = form.cleaned_data["power_total"]
                Station.objects.bulk_update(stations, ["power_total"])
            case "manual":
                all_valid = True
                for station_id, station_form in context["stations_forms"].items():
                    exclusion_form = context["stations_exclude_forms"][station_id]
                    valid = exclusion_form.is_valid()
                    all_valid = all_valid & valid
                    # Only station_forms of not excluded stations must be valid
                    if valid and not exclusion_form.cleaned_data["is_excluded"]:
                        valid = station_form.is_valid()
                        if not valid:
                            # If the station is not set to excluded or electrified its
                            # automatic -> therefore it does not need a proper station_form
                            if not station_form.cleaned_data["is_electrified"]:
                                continue
                            # it is set to electrified but does not have a proper station_form
                            # post will not be accepted
                            all_valid = False
                            print(station_form.errors)
                if not all_valid:

                    return self.render_to_response(context)
                # The forms are valid. Update the stations and exclude stations
                # from electrification
                ebustoolbox.tasks.update_stations_and_exclusion(context, scenario)
            case _:
                raise NotImplementedError
        response = redirect(reverse(self.success_name, args=[kwargs["task_id"]]))
        return response


class CostsView(TemplateView):
    success_name = "simba:depots"
    template_name = "ebustoolbox/costs.html"

    def get_context_data(self, **kwargs):
        scenario = get_scenario_and_assert_authorization(self.request, kwargs["task_id"])  # noqa
        data = {}
        if self.request.method == "POST":
            data = self.request.POST

        context = {}
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
            [context["cost_mode_form"], context["env_mode_form"]], ["costs", "env"]
        ):
            valid = form.is_valid()
            all_valid = all_valid & valid
            if not valid:
                break
            match form.cleaned_data["input_mode"]:
                case "no_input":
                    pass
                case "file_upload":
                    file_form = context[prefix + "_fileUpload"]
                    if not file_form.is_valid():
                        all_valid = False
                        break
                    raise NotImplementedError("file upload is not yet implemented")
                    pass
                case "reference_scenario":
                    scenario_selection = context[prefix + "_scenario_selection"]
                    if not scenario_selection.is_valid():
                        all_valid = False
                        break
                    raise NotImplementedError("scenario_selection is not yet implemented")
                case "manual":
                    manual_form = context[prefix + "_manual"]
                    if not manual_form.is_valid():
                        all_valid = False
                        break
                    raise NotImplementedError("manual_form is not yet implemented")
                case _:
                    raise NotImplementedError(f"Mode {form.cleaned_data['input_mode']}")
        if not all_valid:
            return self.render_to_response(context)

        return redirect(reverse(self.success_name, args=[kwargs["task_id"]]))


class DepotsView(TemplateView):
    template_name = "ebustoolbox/depots.html"
    success_name = "simba:summary"

    def get_context_data(self, **kwargs):
        scenario = get_scenario_and_assert_authorization(self.request, kwargs.get("task_id"))

        context = {}
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
            context["forms"][depot.id]["calculation_mode_form"] = forms.StationModeForm(
                data, prefix=f"depot_calc_mode_{depot.id}"
            )
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
        task_id = kwargs["task_id"]
        _ = get_scenario_and_assert_authorization(request, task_id)

        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        task_id = kwargs["task_id"]
        _ = get_scenario_and_assert_authorization(request, task_id)
        context = self.get_context_data(**kwargs)
        all_valid = True
        all_forms = dict()
        for depot_id, form_dict in context["forms"].items():
            form = form_dict["calculation_mode_form"]
            all_forms[depot_id] = list()
            all_forms[depot_id].append(form)
            if not form.is_valid():
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
                    all_valid = False
                    print(form.errors)

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
            response = redirect(reverse(self.success_name, args=[task_id]))
            return response

        return self.render_to_response(context)


class SummaryView(TemplateView):
    template_name = "ebustoolbox/summary.html"

    def get_context_data(self, scenario, **kwargs):
        context = {}
        context["scenario"] = scenario
        context["scenario_description"] = ScenarioDescription.objects.get(scenario=scenario)
        sim_range = SimulationRange.objects.get(scenario=scenario)
        german_weekdays = {
            0: "Mo",
            1: "Di",
            2: "Mi",
            3: "Do",
            4: "Fr",
            5: "Sa",
            6: "So",
        }
        _format = "%d:%m:%Y, %H:%M"
        start = sim_range.start
        end = sim_range.end
        context["sim_duration"] = (
            f"{german_weekdays[start.weekday()]} {start.strftime(_format)} - "
            f"{german_weekdays[end.weekday()]} {end.strftime(_format)}"
        )
        context["temperature"] = sim_range.temperature
        context["vehicle_types"] = VehicleType.objects.get(scenario=scenario)
        scenario_stations = Station.objects.filter(scenario=scenario).exclude(
            charge_type=EnumChargeType.DEPOT
        )
        context["electrified_stations"] = scenario_stations.filter(is_electrified=True)
        excluded = StationElectrificationExclusions.objects.filter(scenario=scenario)
        excluded_ids = [x.station.id for x in excluded]
        context["automatic_stations"] = scenario_stations.filter(is_electrified=False).exclude(
            id__in=excluded_ids
        )
        context["excluded_stations"] = scenario_stations.filter(id__in=excluded_ids)
        context["depots"] = Station.objects.filter(
            scenario=scenario, charge_type=EnumChargeType.DEPOT
        )
        return context

    def get(self, request, *args, **kwargs):
        task_id = kwargs["task_id"]
        scenario = get_scenario_and_assert_authorization(request, task_id)

        return self.render_to_response(self.get_context_data(scenario))

    def post(self, request, *args, **kwargs):
        task_id = kwargs["task_id"]
        scenario = get_scenario_and_assert_authorization(request, task_id)
        context = self.get_context_data(scenario)

        return self.render_to_response(context)


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


def get_depots_view(request: HttpRequest, task_id):
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

    depots_query = get_depots(scenario)
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


#
#
# def progress(request: HttpRequest, progress_id, progress_type: str):
#     context = {"progress_id": progress_id, "status": "", "current_progress": 0}
#     context |= {"finished": False}
#     try:
#         progress = Progress.objects.get(task_id=progress_id)
#     except ObjectDoesNotExist:
#         response = render(request, "progress.html", context)
#         return response
#
#     context["current_progress"] = max(progress.get_progress(), 1)
#     context["status"] = progress.status
#     status_code = 200
#     hx_trigger = "running"
#     if progress.success or not progress.running or len(progress.errors) != 0:
#         context["errors"] = progress.errors
#         # End polling
#         status_code = 286
#         context["finished"] = True
#         hx_trigger = "notRunning"
#     response = render(request, "progress.html", context)
#     if context["finished"] and len(context["errors"]) == 0:
#         task_id = progress.scenario.task_id
#         response["HX-Redirect"] = reverse(progress_type, args=[task_id, "true"])
#     response["HX-Trigger"] = hx_trigger
#     response.status_code = status_code
#     return response


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


def progress_bar_element(request: HttpRequest, progress_id: uuid.UUID, callback: callable):
    context = {"progress_id": progress_id, "status": "", "current_progress": 0}
    context |= {"finished": False}
    try:
        progress = Progress.objects.get(task_id=progress_id)
    except ObjectDoesNotExist:
        logger.info(f"Progress element {progress_id} does not exist")
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
    response["HX-Trigger"] = hx_trigger
    response.status_code = status_code
    return response
