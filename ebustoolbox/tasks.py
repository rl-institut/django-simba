from collections.abc import Callable
import csv
import shutil
import traceback
import warnings
from argparse import Namespace
from copy import deepcopy, copy
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from pathlib import Path
from typing import List
from django import conf
import environ
from uuid import UUID as UUIDType, uuid4
from celery import shared_task, uuid
import math
import zipfile as zf


import django.apps
from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.gis.db.models import Collect
from django.db import connections
from django.db.models.functions import Lead
from django.db.models import F, Max, Count, Min, QuerySet, Window, OuterRef, Subquery
from django.db.transaction import atomic
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import escape
from django.utils.timezone import make_aware, is_aware
from django.utils.translation import gettext as _

from eflips.depot.api import (  # noqa
    DelayedTripException,
    UnstableSimulationException,
    simulate_scenario,
    generate_optimal_depot_layout,
)
import core.deepcopy
from ebustoolbox.impact import (
    apply_tco_mutation,
    attach_charging_point_types,
    calculate_lca,
    calculate_tco,
    ensure_fleet_topology,
    ensure_lca_parameters,
)
from ebusdjango.util import get_static_file_path
import ebustoolbox.util
import simba.optimizer_util
import simba.station_optimization
import simba.simulate
import simba.util
from core.models import EnumProgress, Progress
from simba.data_container import DataContainer
from simba.schedule import Schedule as SimbaSchedule
from . import schedule_readers, forms
from .models import (
    copy_model_instance,
    AreaInformation,
    DepotConfigurationWish,
    EnumSimulationType,
    User,
    Route,
    Consumption,
    Vehicle,
    UploadedFile,
    Station,
    VehicleType,
    Rotation,
    Trip,
    Scenario,
    SimulationType,
    EnumChargeType,
    EnumVoltageLevel,
    Line,
    charge_type_from_simba_to_db,
    charge_type_from_db_to_station,
    Temperatures,
    Event,
    EventType,
    VehicleClass,
    DefaultScenario,
    Depot,
    UserGroup,
    SimulationTemperatures,
    DepotSelection,
    VehicleTypeMutation,
    VehicleTypeSelection,
    StationMutation,
    Notification,
    EnumNotificationLevels,
    EnumNotificationType,
    EnumScenarioType,
)
from .schedule_readers import ScheduleReader

from spice_ev import report as spice_ev_report
from spice_ev.scenario import Scenario as SimbaScenario
from spice_ev.strategy import STRATEGIES

logger = logging.getLogger("custom")

# ToDo: Any better solutions?
DEFAULT_TEMPERATURE = 20  # °C
IMPLEMENTED_MODES = {"sim", "station_optimization", "station_optimization_single_step"}
DEFAULT_LOADED_MASS = 0
DEFAULT_ALLOWED_LOAD = 1000
STANDBY_BUFFER = timedelta(minutes=5)

# NOTE: 1% is not a very small number, but the balanced strategy can have deltas of at least 0.6%
EPS = 1e-3  # a small number, used to allow for difference when comparing floats


def apply_vehicle_type(
    target_vehicle_type: VehicleType, source_vehicle_type: VehicleType
) -> VehicleType:
    """Use a source vehicle type and apply the attributes to a target vehicle type.

    Scenario, name and name short of the target are not copied over.
    VehicleClasses of source are copied as well as consumptions which are linked to vehicle classes
    """
    vehicle_classes = source_vehicle_type.vehicle_classes.all()
    source_vehicle_type.id = target_vehicle_type.id
    source_vehicle_type.scenario = target_vehicle_type.scenario
    source_vehicle_type.name = target_vehicle_type.name
    source_vehicle_type.name_short = target_vehicle_type.name_short
    # the source battery might point to a battery.
    # Since vehicle types should not be shared (custom tco_parameters)
    # the battery_type needs to be copied.
    new_battery = source_vehicle_type.battery_type
    new_battery.id = None
    new_battery.save()
    source_vehicle_type.battery_type = new_battery

    source_vehicle_type.save()
    # Copy vehicle_classes and consumption and add the new vehicle type to it
    for vehicle_class in vehicle_classes:
        # Cast consumptions to list to evaluate them early
        consumptions = list(vehicle_class.consumption_set.all())

        vehicle_class.id = None
        vehicle_class.scenario = target_vehicle_type.scenario
        vehicle_class.save()
        vehicle_class.vehicle_types.add(source_vehicle_type)
        if consumptions:
            assert len(consumptions) == 1
            c = consumptions[0]
            c.id = None
            c.scenario = target_vehicle_type.scenario
            c.vehicle_class = vehicle_class
            c.save()
    return source_vehicle_type


@atomic()
def input_files_to_database(cleaned_data: dict, request: HttpRequest):
    """Fill the database with the inputs from the form

    :param cleaned_data: cleaned data
    :param request: Request with uploaded files
    :return:
    """
    django_scenario = scenario_to_db(cleaned_data, request)
    schedule_reader = schedule_readers.SimbaScheduleReader(
        file_path=r"ebustoolbox/static/ebustoolbox/examples/trips_example.csv"
    )
    _ = schedule_reader.write_to_db(django_scenario.id)

    # # Write the Consumption to the DB
    consumption_path = Path(django_scenario.simba_options["consumption_path"])
    consumption_file_to_db(consumption_path, django_scenario)
    for vt in VehicleType.objects.filter(scenario=django_scenario):
        VehicleClass.objects.get(scenario=django_scenario).vehicle_types.add(
            vt, through_defaults=None
        )

    assign_new_vehicles_to_db(django_scenario)

    return django_scenario


def consumption_file_to_db(consumption_path: Path, django_scenario: Scenario) -> None:
    """Writes the Consumption to the database and connects it with the scenario"""

    delim = simba.util.get_csv_delim(consumption_path)
    consumption_names = ["consumption", "consumption_kwh_per_km"]

    with open(consumption_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delim)
        columns = copy(reader.fieldnames)
        consumption_found = False
        cons = None
        for cons in consumption_names:
            if cons in columns:
                consumption_found = True
                break
        if not consumption_found:
            text = f"No column named {consumption_names} was found in {consumption_path.stem}"
            raise AssertionError(text)
        columns.remove(cons)
        datapoints = []
        values = []
        for i, row in enumerate(reader):
            data = []
            try:
                for field in columns:
                    data_point = row[field]
                    data.append(float(data_point))
                val = row[cons]
                val = float(val)
            except ValueError:
                if val == "" or data_point == "":
                    warnings.warn(
                        f"Row {i} in {consumption_path.stem} contains a missing value. "
                        f"This row and following rows will be ignored."
                    )
                    break
                else:
                    raise
            values.append(val)
            datapoints.append(data)

    # VehicleClass that will be linked with this Consumption
    vehicle_class, _ = VehicleClass.objects.get_or_create(
        scenario=django_scenario,
        name=consumption_path.name,
    )
    vehicle_class.save()
    Consumption.objects.create(
        name=consumption_path.name,
        scenario=django_scenario,
        columns=columns,
        data_points=datapoints,
        values=values,
        vehicle_class=vehicle_class,
    )


def temperatures_to_db(
    temperature_file_path: Path,
    django_scenario: Scenario,
    use_only_time: bool,
) -> None:
    """Writes the temperatures to the database and connects it with the scenario"""
    delim = simba.util.get_csv_delim(temperature_file_path)
    with open(temperature_file_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delim)
        times = []
        temperatures = []
        for row in reader:
            times.append(datetime.fromisoformat(row["time"]))
            temperatures.append(row["temperature"])
        temperatures_instance = Temperatures(
            scenario=django_scenario,
            name=temperature_file_path.name,
            use_only_time=use_only_time,
            datetimes=times,
            data=temperatures,
        )
        temperatures_instance.make_aware()
        temperatures_instance.save()


def get_notfications_dict(
    notifications: QuerySet[Notification],
) -> dict[str, list[Notification]]:
    return {
        "error": list(
            notifications.filter(
                level=EnumNotificationLevels.ERROR,
            )
        ),
        "warning": list(
            notifications.filter(
                level=EnumNotificationLevels.WARNING,
            )
        ),
        "info": list(
            notifications.filter(
                level=EnumNotificationLevels.INFO,
            )
        ),
    }


def get_schedule_from_db(
    django_scenario: Scenario,
) -> tuple[simba.schedule.Schedule, Namespace]:
    """Takes a django Scenario and returns the SimBA Schedule and arguments

    Can be used to run a previously stored Django Scenario again straight from the database without
    using files, by returning schedule and args.

    :param django_scenario: Scenario
    :type django_scenario: .models.Scenario
    :return: (SimBA Schedule, args)
    :rtype: (simba.schedule.Schedule, Namespace)
    """
    data_container = DataContainer()

    # get SimBA station_data
    station_data = get_station_data_from_db(django_scenario)
    data_container.add_station_geo_data(station_data)

    # get SimBA electrified stations from db
    stations_dict = get_electrified_stations_from_db(django_scenario)
    data_container.add_stations(stations_dict)

    # get SimBA vehicle_types from db
    vehicle_types = get_vehicle_types_from_db(django_scenario)
    data_container.add_vehicle_types(vehicle_types)

    # get SimBA rotations and trips from db
    trip_dicts = get_trip_dictionaries_from_db(django_scenario, station_data)
    data_container.trip_data = trip_dicts

    consumptions = Consumption.objects.filter(scenario__in=[django_scenario, None])
    for consumption in consumptions:
        data_container.add_consumption_data(consumption.to_simba_name(), consumption.to_df())

    args = get_args(django_scenario=django_scenario)
    schedule, args = simba.simulate.pre_simulation(args, data_container)

    # If the database contains vehicle assignments, overwrite the assigned
    rot_query = Rotation.objects.filter(scenario=django_scenario).prefetch_related("vehicle")
    if all(rot.vehicle for rot in rot_query):
        for rot in rot_query:
            schedule.rotations[rot.id].vehicle_id = rot.vehicle.to_simba_name()
    elif any(rot.vehicle for rot in rot_query):
        logger.warning(
            f"S.ID:{django_scenario.id}:Some rotations in the database contain vehicles, others do not. "
            "Database assignments will be ignored."
        )

    # SimBA does not disallow opportunity charging for rotations.
    # By default, it is allowed for all rotations.
    # If the database contains information about not allowing opportunity charging,
    # the schedule is overwritten here
    for rot in rot_query:
        schedule.rotations[rot.id].allow_opp_charging_for_oppb = rot.allow_opportunity_charging

    # Database should contain assigned vehicles already
    for rot in schedule.rotations.values():
        assert rot.vehicle_id is not None

    return schedule, args


def get_trip_dictionaries_from_db(django_scenario, station_data) -> list:
    """Create SimBA rotations with trips from a database with a scenario as a key

    :param django_scenario: Django scenario
    :param station_data: dictionary with all stations and elevation
    :return: list of trip dictionaries
    :rtype: list
    """
    lines_dict = {line.id: line for line in Line.objects.filter(scenario=django_scenario)}
    simba_trips = list()
    temperatures = Temperatures.objects.filter(scenario=django_scenario)
    temperature = None
    if temperatures.exists():
        assert len(temperatures) == 1, "A scenario can only have a single linked Temperature object"
        temperature = temperatures.first()
    # Get a function which produces temperatures with a trip as input
    get_temperature = get_temperature_function(temperature)
    warning_dict = {
        "missing_allowed_load": True,
        "missing_temperature": True,
        "missing_loaded_mass": True,
        "level_of_loading_out_of_range": True,
    }

    for rot in Rotation.objects.filter(scenario=django_scenario).select_related(
        "vehicle_type", "vehicle"
    ):
        vehicle_type = rot.vehicle_type.id
        charging_type = (
            EnumChargeType.OPPORTUNITY.value
            if rot.vehicle_type.opportunity_charging_capable
            else EnumChargeType.DEPOT.value
        )

        # Use the id/pk instead of the name, since names might not be unique, when database is
        # filled with non SimBA ingesters
        simba_id = rot.id

        vehicle_classes = VehicleClass.objects.filter(vehicle_types=vehicle_type)
        consumption_classes = vehicle_classes.exclude(consumption__isnull=True)
        assert (
            len(consumption_classes) <= 1
        ), "A VehicleType can only have a single VehicleClass with a consumption attached"
        lut_consumption = False
        if len(consumption_classes) == 1:
            lut_consumption = True

        try:
            calc_allowed_load = rot.vehicle_type.allowed_mass - rot.vehicle_type.empty_mass
        except TypeError:
            calc_allowed_load = None
        allowed_load = calc_allowed_load or DEFAULT_ALLOWED_LOAD
        # select related means later db access can be skipped
        if lut_consumption:
            warning_dict = validate_lut_consumption_inputs(
                temperatures, calc_allowed_load, rot, warning_dict
            )
        query = (
            Trip.objects.filter(rotation=rot)
            .select_related("route__arrival_station", "route__departure_station", "route__line")
            .order_by("arrival_time")
        )

        for trip in query:
            loaded_mass = trip.loaded_mass or DEFAULT_LOADED_MASS
            level_of_loading = None
            if allowed_load is not None:
                level_of_loading = loaded_mass / allowed_load
                if lut_consumption:
                    warning_dict = validate_trip_lut_consumption_inputs(
                        trip, loaded_mass, level_of_loading, warning_dict
                    )
            line_id = None
            try:
                line_id = trip.route.line.id
            except AttributeError:
                pass
            line = lines_dict[line_id].name if line_id else None
            simba_trip_dict = {
                "rotation_id": simba_id,
                "departure_time": trip.departure_time,
                "departure_name": trip.route.departure_station.to_simba_name(),
                "arrival_time": trip.arrival_time,
                "arrival_name": trip.route.arrival_station.to_simba_name(),
                "vehicle_type": str(vehicle_type),
                "charging_type": charging_type,
                "distance": trip.route.distance,
                "line": line,
                "height_diff": (
                    station_data[trip.route.arrival_station.to_simba_name()]["elevation"]
                    - station_data[trip.route.departure_station.to_simba_name()]["elevation"]
                ),
                "level_of_loading": level_of_loading,
                "mean_speed": trip.speed * 3.6,
                "temperature": get_temperature(trip),
            }

            simba_trips.append(simba_trip_dict)
    return simba_trips


def validate_trip_lut_consumption_inputs(trip, loaded_mass, level_of_loading, warning_dict) -> dict:
    if trip.loaded_mass is None:
        text = (
            f"{trip.id=} has no loaded mass but the vehicle_type which services this "
            "trip needs a loaded mass for consumption look up and is set to "
            f"{loaded_mass}."
        )
        if warning_dict["missing_loaded_mass"]:
            warning_dict["missing_loaded_mass"] = False
            logger.warning(
                f"S.ID:{trip.scenario.id}:"
                + text
                + "\n This message is only shown once as warning."
            )
        else:
            logger.debug(text)
    if 1 < level_of_loading or 0 > level_of_loading:
        text = f"Level of loading is out of [0,1] range for {trip.id=}"
        if warning_dict["level_of_loading_out_of_range"]:
            warning_dict["level_of_loading_out_of_range"] = False
            logger.warning(
                f"S.ID:{trip.scenario.id}:"
                + text
                + "\n This message is only shown once as warning."
            )
        else:
            logger.debug(text)
    return warning_dict


def validate_lut_consumption_inputs(temperatures, calc_allowed_load, rot, warning_dict) -> dict:
    if not temperatures.exists():
        text = (
            f"Vehicle Type {rot.vehicle_type.id} uses a consumption LUT for "
            "consumption calculation but the scenario has no Temperature object for "
            "temperature lookup. Default value for temperature of "
            f"{DEFAULT_TEMPERATURE} °C is used."
        )
        if warning_dict["missing_temperature"]:
            warning_dict["missing_temperature"] = False
            logger.warning(
                f"S.ID:{rot.scenario.id}:" + text + "\n This message is only shown once as warning."
            )
        else:
            logger.debug(text)

    if calc_allowed_load is None:
        text = (
            f"{rot.id=} is serviced by a vehicle_type with a consumption lut. "
            "The vehicle_type does not contain the allowed and empty mass. "
            f"The allowed load will be set to {DEFAULT_ALLOWED_LOAD} kg."
        )
        if warning_dict["missing_allowed_load"]:
            warning_dict["missing_allowed_load"] = False
            logger.warning(
                f"S.ID:{rot.scenario.id}:" + text + "\n This message is only shown once as warning."
            )
        else:
            logger.debug(text)

    return warning_dict


def get_temperature_function(temperature: Temperatures | None) -> Callable[[Trip], float]:
    """Return a function which produces temperatures based on a Trip input

    If the passed temperature arg is None, the DEFAULT_TEMPERATURE constant will be used instead.
    """
    if temperature is not None:

        def get_temperature(trip) -> float:
            middle_time = trip.departure_time + 0.5 * (trip.arrival_time - trip.departure_time)
            # get pseudo mean temperature by using center and boundary temperatures
            temp = (
                0.5 * temperature.get_interpolated_temperature(middle_time)
                + 0.25 * temperature.get_interpolated_temperature(trip.arrival_time)
                + 0.25 * temperature.get_interpolated_temperature(trip.departure_time)
            )
            return temp

        return get_temperature
    return lambda _: DEFAULT_TEMPERATURE


def get_uuid():
    return uuid()


def get_vehicle_types_from_db(django_scenario) -> dict:
    """Create SimBA rotations with trips from database with scenario as key

    :param django_scenario: Django scenario
    :return: vehicle_types
    :rtype: dict
    """
    vehicle_types = dict()
    for vehicle_type in VehicleType.objects.filter(scenario=django_scenario):
        charge_type = (
            EnumChargeType.OPPORTUNITY.value
            if vehicle_type.opportunity_charging_capable
            else EnumChargeType.DEPOT.value
        )
        try:
            vehicle_types[str(vehicle_type.id)]
        except KeyError:
            vehicle_types[str(vehicle_type.id)] = dict()

        mileage = vehicle_type.consumption
        query = VehicleClass.objects.filter(vehicle_types=vehicle_type).exclude(consumption=None)
        if len(query) > 0:
            assert mileage is None
            assert len(query) == 1
            mileage = Consumption.objects.get(vehicle_class=query[0]).to_simba_name()

        vehicle_types[str(vehicle_type.id)][charge_type] = {
            "name": vehicle_type.name,
            "capacity": vehicle_type.battery_capacity,
            "charging_curve": vehicle_type.charging_curve,
            "min_charging_power": vehicle_type.minimum_charging_power,
            "v2g": (vehicle_type.v2g_curve is not None),
            # ToDo use vehicle to grid curve
            # vehicle_to_grid_curve ....
            "mileage": mileage,
            "battery_efficiency": vehicle_type.charging_efficiency,
        }
    return vehicle_types


def get_electrified_stations_from_db(django_scenario) -> dict:
    """Create SimBA electrified stations from database with scenario as key

    :param django_scenario: Django scenario
    :return: electrified_stations
    :rtype: dict
    """
    stations_dict = dict()
    for station in Station.objects.filter(scenario=django_scenario, is_electrified=True):
        stat_dict = {
            "type": charge_type_from_db_to_station(station.charge_type.lower(), is_station=True),
            "n_charging_stations": station.amount_charging_places,
            "cs_power_deps_oppb": station.power_per_charger,
            "cs_power_deps_depb": station.power_per_charger,
            "cs_power_opps": station.power_per_charger,
            "gc_power": station.power_total,
            "voltage_level": station.voltage_level,
            "min_power": 0,
        }
        stat_dict_cleaned = {
            k: v for k, v in stat_dict.items() if v is not None or k == "n_charging_stations"
        }
        stations_dict[station.to_simba_name()] = stat_dict_cleaned
    return stations_dict


def get_station_data_from_db(django_scenario) -> dict:
    """Create station_data from database with scenario as key

    :param django_scenario: Django scenario
    :return: station_data
    :rtype: dict
    """
    station_data = dict()
    for station in Station.objects.filter(scenario=django_scenario):
        try:
            station_data[station.to_simba_name()] = {
                "long": station.geom.x,
                "lat": station.geom.y,
                "elevation": station.geom.z,
            }
        except AttributeError:
            station_data[station.to_simba_name()] = {
                "long": 0,
                "lat": 0,
                "elevation": 0,
            }

    return station_data


def get_args(django_scenario) -> Namespace:
    """Creates arguments from django Scenario

    Creates arguments for SimBA by getting default arguments from SimBA, updating them with
    the options from the django_scenario and

    :param django_scenario: Scenario in the django database
    :type django_scenario: models.Scenario
    :return:
    """
    logger.debug(
        f"S.ID:{django_scenario.id}:Setting default arguments for scenario {django_scenario.id}"
    )
    # Get parser from SimBA
    parser = simba.util.get_parser()
    # Read the parse values, in this case the default values
    args, _ = parser.parse_known_args()

    p = get_static_file_path(__package__, "examples/default_optimizer.cfg")
    args.optimizer_config_path = str(p)
    if not p.is_file():
        logger.info(
            f"S.ID:{django_scenario.id}:default_optimizer.cfg not found. "
            "Optimizer config will use default values."
        )
    # Overwrite args with scenario specific data
    if django_scenario.simba_options is not None:
        logger.debug(
            f"Overwriting default arguments with {len(django_scenario.simba_options)} "
            f"values from the database"
        )
        vars(args).update(vars(Namespace(**django_scenario.simba_options)))

    # turn of plotting
    args.skip_plots = True
    args.skip_flex_report = True
    args = simba.util.replace_deprecated_arguments(args)

    # arguments relevant to SpiceEV, setting automatically to reduce clutter in config
    simba.util.mutate_args_for_spiceev(args)

    return args


def scenario_to_db(cleaned_data, request) -> Scenario:
    scenario = Scenario.objects.create(
        name=cleaned_data["title"], task_id=ebustoolbox.util.get_unique_task_id()
    )
    args = dict(cleaned_data)
    args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(",")))
    # decimal -> float
    for k, v in args.items():
        if type(v) is Decimal:
            args[k] = float(v)
    # set default files if not given
    for k, v in {
        "schedule_path": "trips_example.csv",
        "electrified_stations_path": "electrified_stations.json",
        "vehicle_types_path": "vehicle_types.json",
        "station_data_path": "all_stations.csv",
        "outside_temperature_over_day_path": "default_temp_summer.csv",
        "consumption_path": "energy_consumption_example.csv",
        "temperature_time_series_path": "temperature_time_series.csv",
        "level_of_loading_over_day_path": "default_level_of_loading_over_day.csv",
        "cost_parameters_path": "cost_params.json",
        "optimizer_config_path": "default_optimizer.cfg",
    }.items():
        if args[k]:
            # uploaded file: store in upload folder
            f = UploadedFile.objects.create(scenario=scenario, file=request.FILES[k])
            args[k] = f.file.path
            continue
        p = get_static_file_path(__package__, "examples/" + v)
        if not p.exists():
            logger.warning(f"FILE ERROR: {k} COULD NOT BE SET ({str(p)})")
            continue
        args[k] = str(p)
    scenario.simba_options = args
    scenario.save()

    return scenario


def vehicles_to_db(vehicle_types: dict, scenario: Scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: SimBA Schedule
    :param scenario: django model Scenario
    :return: None
    """

    # TODO: Get real data
    DEFAULT_WIDTH = 2.54
    DEFAULT_HEIGHT = 3.375

    for name, v_type in vehicle_types.items():
        for charge_name, charge_type in v_type.items():
            consumption = None
            mileage_text = charge_type.get("mileage")

            add_to_vehicle_class = False
            try:
                consumption = float(mileage_text)
            except ValueError:
                # The mileage can be a link/ str to a consumption_table.In this case link
                # the VehicleClass with this name to this vehicle
                add_to_vehicle_class = True
                pass
            params = dict(
                name=charge_type.get("name", "unnamed bus"),
                name_short=name,
                scenario=scenario,
                opportunity_charging_capable=(charge_name.lower() == "oppb"),
                battery_capacity=charge_type["capacity"],
                charging_efficiency=charge_type.get("battery_efficiency", 0.95),
                minimum_charging_power=charge_type.get("min_charging_power"),
                charging_curve=charge_type["charging_curve"],
                v2g_curve=charge_type.get("v2g_curve", None),
                consumption=consumption,
                length=charge_type.get("length", 0),
                width=DEFAULT_WIDTH,
                height=DEFAULT_HEIGHT,
            )
            vt = VehicleType.objects.create(**params)
            if add_to_vehicle_class:
                VehicleClass.objects.get(scenario=scenario, name=mileage_text).vehicle_types.add(
                    vt, through_defaults=None
                )


def update_electrified_stations_db(electrified_stations, scenario):
    """Update stations which are electrified with info from electrified_stations dictionary"""
    notifications = []
    for name, ele_station in electrified_stations.items():
        # TODO: loop over stations
        station = Station.objects.get(id=Station.get_id_from_simba_name(name), scenario=scenario)
        if not station.is_electrified:
            notification = Notification(
                # Notifications should be saved to the mutation.
                # If the toolchain is run without a parent the notification is saved to the scenario
                scenario=scenario.parent or scenario,
                sender="SimBA-Optimizier from tasks.py",
                level=EnumNotificationLevels.INFO,
                notification_type=EnumNotificationType.ADDED_ELECTRIFICATION,
                message=_(
                    f"Die Stationsoptimierung hat {station.name} als geeignete Station "
                    "erkannt und ihr eine Elektrifizierung hinzugefügt."
                ),
            )
            notifications.append(notification)

        station.is_electrified = True

        charge_type = ele_station.get("type")
        # SimBA calls station types opps and deps which is not the same as
        # EnumChargeTypes. This needs a translation.
        station.charge_type = charge_type_from_simba_to_db(charge_type)

        station.voltage_level = ele_station.get(
            "voltage_level", scenario.simba_options.get("default_voltage_level")
        )
        station.amount_charging_places = ele_station.get("n_charging_stations")
        # ToDo how do we handle differences in charging power depending on oppb or depb
        if station.charge_type == EnumChargeType.OPPORTUNITY.value:
            power_per_charger = ele_station.get("cs_power_opps")
            power_per_charger = power_per_charger or scenario.simba_options.get("cs_power_opps")

        else:
            power_per_charger = ele_station.get("cs_power_deps_oppb")
            logger.warning(
                f"S.ID:{scenario.id}:Station {station.name} does not have a power per charger"
            )
            if power_per_charger is None:
                assert station.power_per_charger is None

        station.power_per_charger = power_per_charger
        station.power_total = ele_station.get(
            "gc_power", scenario.simba_options.get("gc_power_" + charge_type)
        )
        if station.power_total is None:
            logger.warning(
                f"S.ID:{scenario.id}:Station {station.name} does not have a power_total Value"
            )
        station.save()
    Notification.objects.bulk_create(notifications)


def generate_zipped_scenario(task_id: str):
    _celery_generate_zipped_scenario.apply_async((str(task_id),), task_id=task_id)


def _generate_zipped_scenario(task_id: str):
    task_id = str(task_id)
    folder_path = Path(settings.UPLOAD_PATH, task_id)
    output_path = settings.MEDIA_ROOT / (task_id + ".zip")
    if not folder_path.exists():
        logger.error("input folder for zipping not found")
        return
    if output_path.is_file():
        logger.info("Zip already exists")
        return
    shutil.make_archive(output_path.with_suffix(""), "zip", folder_path)


def get_parent(scenario):
    if scenario.parent:
        return scenario.parent
    task_id = scenario.task_id
    # Create a parent by making the current scenario a parent
    # Make sure we get a new reference to not overwrite scenario in the outer context
    parent = scenario
    parent.task_id = ebustoolbox.util.get_unique_task_id()
    parent.save()

    child = create_empty_child_scenario(parent, task_id=task_id)
    # parent.name = "Parent of " + parent.name
    parent.save()

    return parent, child


@shared_task(bind=True)
def init_db_with_trips(
    self, scenario_id: int, reader_num: int, files: dict, cleaned_data, progress_id: int
):
    progress = Progress.objects.get(id=progress_id)
    # files is a dict with values of (path, file_id)
    progress.status = _("Gestartet")
    progress.save()
    file_paths = {key: value[0] for key, value in files.items()}
    schedule_reader_factory = schedule_readers.get_schedule_reader_factory(reader_num)
    schedule_reader: ScheduleReader = schedule_reader_factory(**file_paths, **cleaned_data)
    # The progress is linked to the child scenario.
    schedule_reader.set_observer(progress)
    try:
        # Allow for compression of 0.5
        max_uncompressed_size = conf.settings.MAX_FILE_SIZE_B * 2
        [
            ebustoolbox.util.validate_zip(
                zf.ZipFile(f), conf.settings.MAX_ZIP_FILES_NR, max_uncompressed_size, 5
            )
            for f in file_paths.values()
            if Path(f).suffix == ".zip"
        ]
        schedule_reader_factory = schedule_readers.get_schedule_reader_factory(reader_num)
        schedule_reader: ScheduleReader = schedule_reader_factory(**file_paths, **cleaned_data)
        # The progress is linked to the child scenario.
        schedule_reader.set_observer(progress)
        # This is going to be the mutation scenario
        scenario = Scenario.objects.get(id=scenario_id)
        progress.scenario = scenario
        progress.save()
        # parent scenario has all the content
        parent = scenario.parent
        delete_old_scenario_data(parent)
        # Read the file and write it to database
        progress.refresh_from_db()
        progress.success = schedule_reader.write_to_db(parent.id)
        scenario.simba_options = vars(get_args(parent))
        find_and_make_depots(parent)

        # Delete rotations which are not consistent and create a notification
        # Do this after creating depots, since depots are part of consistent rotations
        make_consistent(parent)
        scenario.scenario_type = EnumScenarioType.MUTATION
        parent.scenario_type = EnumScenarioType.SOURCE
        scenario.save()
        parent.save()

        parent.scenario_type = EnumScenarioType.SOURCE_FILE
        parent.task_id = ebustoolbox.util.get_unique_task_id()
        # Applying these fixes to the source file (applying it to the parent before copying it)
        # Means we do not have to worry about it if we pick a scenario from the dropdown later
        transform_depot_stations(parent)
        ScheduleStationMerger.transform_zero_duration_trips(parent)
        source_file_scenario, unused_variable = deepcopy_scenario(parent)
        source_file_scenario.name += _(" [ohne Simulationsfilter]")
        source_file_scenario.save()
        parent.refresh_from_db()
        parent.parent = source_file_scenario
        parent.save()
        # Parent contains the trip data so check the consistency of the parent and not the mutation.
        if not (is_consistent(parent)):
            logger.error(
                f"S.ID:{scenario.id}:Scenario does not seem to be consistent with assumptions"
            )
            raise Exception("Scenario does not seem to be consistent with assumptions")
        parent.save()
        progress.save()
    except Exception as e:
        logger.error(traceback.format_exc())
        progress.status = _("Fehlgeschlagen")
        progress.success = False
        progress.errors.append(str(e))
    finally:
        try:
            progress.errors.extend(schedule_reader.get_errors())
        except:  # noqa
            pass
        progress.status = _("Fertig")
        if not progress.success:
            progress.status = _("Fehlgeschlagen")
        # delete all uploaded files
        try:
            for file_path, file_id in files.values():
                UploadedFile.objects.get(id=file_id).delete()
        except Exception:
            logger.error(traceback.format_exc())
        progress.running = False
        progress.save()


def trim_scenario(scenario, time_delta, start_time=None):
    rotations = get_rotations_by_timespan(scenario, time_delta, start_time)
    rotations_to_remove = Rotation.objects.filter(scenario=scenario).exclude(id__in=rotations)
    logger.info(
        f"S.ID:{scenario.id}:Deleting {rotations_to_remove.count()} rotations out of sim range"
    )
    rotations_to_remove.delete()


def get_rotations_by_start_end(scenario, start: datetime, end: datetime) -> QuerySet[Rotation]:
    rotations = (
        Rotation.objects.filter(scenario=scenario)
        .annotate(first_departure=Min("trip__departure_time"))
        .filter(first_departure__gte=start)
        .filter(first_departure__lte=end)
    )
    return rotations


def get_rotations_by_timespan(
    scenario: Scenario, time_delta, start_time=None
) -> QuerySet[Rotation]:
    if start_time is None:
        trips = Trip.objects.filter(scenario=scenario).order_by("departure_time")
        start_time = trips.first().departure_time
    latest_start = start_time + time_delta
    rotations = (
        Rotation.objects.filter(scenario=scenario)
        .annotate(first_departure=Min("trip__departure_time"))
        .filter(first_departure__gte=start_time)
        .filter(first_departure__lte=latest_start)
    )

    return rotations


@atomic()
def delete_old_scenario_data(scenario: Scenario):
    Rotation.objects.filter(scenario=scenario).delete()
    Station.objects.filter(scenario=scenario).delete()
    VehicleType.objects.filter(scenario=scenario).delete()
    Vehicle.objects.filter(scenario=scenario).delete()
    Trip.objects.filter(scenario=scenario).delete()
    Route.objects.filter(scenario=scenario).delete()
    Line.objects.filter(scenario=scenario).delete()


@shared_task(bind=True)
def _celery_generate_zipped_scenario(self, task_id: str):
    _generate_zipped_scenario(task_id)


def run_ebus_toolchain(task_id):
    async_result = _run_ebus_toolchain.apply_async((str(task_id),), task_id=str(task_id))
    return async_result


def merge_scenario(mutation_id, simulation_task_id):
    """Create a simulation scenario from a mutation scenario.

    Mutations are applied from the mutation to the parent.
    Only works if the parent is a Scenario of type SOURCE
    The new scenario is saved and returned
    """
    mutation_scenario = Scenario.objects.get(id=mutation_id)
    parent = mutation_scenario.parent
    assert parent is not None
    assert parent.scenario_type == EnumScenarioType.SOURCE

    # Create a deepcopy of the parent / source scenario.
    # Apply mutations from the mutation scenario to this copy.
    simulation_scenario = create_child_from_mutation(parent, mutation_scenario)
    simulation_scenario.scenario_type = EnumScenarioType.SIMULATION
    simulation_scenario.name = mutation_scenario.name
    simulation_scenario.task_id = simulation_task_id
    simulation_scenario.save()
    return simulation_scenario


# TODO: catch exceptions and pass to progress if exists
@shared_task(bind=True)
def run_and_merge_scenarios(
    self,
    mutation_id: int,
    default_simulation_task_id: UUIDType,
    sizing_scenario_task_id: UUIDType,
):

    progress, created = Progress.objects.get_or_create(task_id=self.request.id)
    progress.reset()
    # We expect 10 steps of work, with 5 steps per simulation.
    # each step increments current_work by 1
    progress.total_work = 11
    progress.save()

    logger.info(f"Creating an extreme scenario {sizing_scenario_task_id} for the first Simulation")
    # Create a basic merge from the mutation and the source
    sizing_scenario = merge_scenario(mutation_id, sizing_scenario_task_id)
    sizing_scenario.manager = Scenario.objects.get(id=mutation_id).manager
    sizing_scenario.save()
    from core.models import EnumProgress

    sizing_progress = Progress.objects.create(
        scenario=sizing_scenario,
        task_id=uuid4(),
        progress_type=EnumProgress.RUNNING_SIMULATION,
        running=True,
    )
    SimulationType.objects.create(scenario=sizing_scenario, sim_type=EnumSimulationType.SIZING)
    # Swap the consumption so in the first run the max consumption is used
    swap_consumption_w_max_consumption(sizing_scenario)
    # If a LUT is used change the temperature to the extreme Temperature
    sim_range = SimulationTemperatures.objects.get(scenario_id=mutation_id)
    Temperatures.objects.filter(scenario=sizing_scenario).delete()
    # Create temperature instance
    Temperatures.create_constant_temperatures(sizing_scenario, sim_range.temperature_extreme)

    logger.info(f"S.ID:{sizing_scenario.id}:Simulating scenario with high consumption")
    # Run the sizing scenario with these applied changes
    assign_new_vehicles_to_db(sizing_scenario)

    if "station_optimization" not in sizing_scenario.simba_options.get("modes"):
        Notification.objects.create(
            # Notifications should be saved to the mutation.
            # If the toolchain is run without a parent the notification is saved to the scenario
            scenario=sizing_scenario.parent or sizing_scenario,
            sender="WeBus Scenario Merge",
            level=EnumNotificationLevels.INFO,
            notification_type=EnumNotificationType.STATION_OPTIMIZATION_SKIPPED,
            message=_(
                "Die Stationsoptimierung wurde übersprungen, "
                "da keine Stationen auf 'automatisch' gesetzt wurden."
            ),
        )

    _run_ebus_toolchain.apply(
        (sizing_scenario_task_id, progress.task_id), task_id=sizing_scenario_task_id, throw=True
    )

    logger.info(
        f"S.ID:{sizing_scenario.id}:Copying result of first Simulation as basis for the second."
    )
    # The sizing scenario is supposed to be the basis of the average scenario
    # Create a copy of the scenario
    sizing_scenario.task_id = default_simulation_task_id
    average_scenario, stack = deepcopy_scenario(sizing_scenario)
    sizing_scenario.refresh_from_db()
    assert sizing_scenario.task_id != default_simulation_task_id

    average_progress = Progress.objects.create(
        scenario=average_scenario,
        task_id=uuid4(),
        progress_type=EnumProgress.RUNNING_SIMULATION,
        running=True,
    )
    logger.info(
        f"S.ID:{average_scenario.id}:Simulating scenario {average_scenario.task_id} with average consumption"
    )
    # Swap the consumptions back
    swap_consumption_w_max_consumption(average_scenario)
    # Apply the average temperature
    Temperatures.objects.filter(scenario=average_scenario).delete()
    # Create temperature instance
    Temperatures.create_constant_temperatures(average_scenario, sim_range.temperature_average)
    # Run the average scenario with these applied changes.
    # Do not optimize Stations this run. In most cases the optimization should not do anything.
    # In some circumstances the average scenario might be badly setup and have higher consumptions.
    # In these cases we do not want to extend electrification but only simulate the users wishes.
    average_scenario.simba_options["modes"] = "sim"
    average_scenario.save(update_fields=["simba_options"])
    SimulationType.objects.filter(scenario=average_scenario).update(
        sim_type=EnumSimulationType.DEFAULT
    )

    assign_new_vehicles_to_db(average_scenario)
    _run_ebus_toolchain.apply(
        (default_simulation_task_id, progress.task_id),
        task_id=default_simulation_task_id,
        throw=True,
    )
    # Give each scenario a Progress which succeeded
    sizing_progress.set_success()
    average_progress.set_success()
    progress.set_success()
    scenario = Scenario.objects.get(id=mutation_id)
    scenario.finished = timezone.now()
    scenario.save()


def swap_consumption_w_max_consumption(scenario: Scenario) -> None:
    """Swap the consumptions of the VehicleTypes

    This is used to toggle the extreme and average scenario
    """

    vts = VehicleType.objects.filter(scenario=scenario)
    for vt in vts:
        if vt.consumption is not None:
            consumptions = Consumption.objects.filter(vehicle_class__vehicle_types=vt)
            assert consumptions.count() == 0

            tmp = vt.consumption
            vt.consumption = vt.max_consumption
            vt.max_consumption = tmp
            vt.save()


def run_toolchain_from_scenario(django_scenario: Scenario, assign_vehicles=False):
    """Run a Scenario from the database with SimBA

    The provided scenario must contain all information including Temperatures, Vehicle_Types,
    station information and electrified_station information. Mutations are NOT applied.
    :param django_scenario: Scenario which is simulated
    :param assign_vehicles: boolean if the vehicles should be added to rotations.
    Previous assignments will be deleted
    :return:
    """
    if assign_vehicles:
        assign_new_vehicles_to_db(django_scenario)
    async_result = run_ebus_toolchain(django_scenario.task_id)
    return async_result


def run_simba_scenario(
    django_scenario: Scenario | int,
    assign_vehicles=False,
    db_url=None,
    simba_scenario=None,
    mode="sim",
):
    """Run a Scenario from the database with SimBA

    The provided scenario must contain all information including Temperatures, Vehicle_Types,
    station information and electrified_station information.
    :param django_scenario: Scenario which is simulated
    :param assign_vehicles: boolean if the vehicles should be added to rotations.
    :param db_url: url of database to be used. Defaults to django default
    :type db_url: str
    Previous assignments will be deleted
    :return:
    """

    if db_url is not None:
        # Other database needs to be added to connections. Use same database settings as default,
        # then overwrite db_url. Might be problematic with multithreading
        connections.databases[db_url] = deepcopy(connections.databases["default"])
        connections.databases[db_url] |= environ.Env().db_url_config(db_url)
    try:
        if db_url is not None:
            # overwrite all managers so they use the specified db
            for model in django.apps.apps.app_configs["ebustoolbox"].models.values():
                model.objects = model.objects.using(db_url)

        if isinstance(django_scenario, int):
            django_scenario = Scenario.objects.get(id=django_scenario)
        if assign_vehicles:
            assign_new_vehicles_to_db(django_scenario, db_url)
        simba_schedule_db, args_db = get_schedule_from_db(django_scenario)
        simba_schedule, scenario = run_simba(
            simba_schedule_db,
            args_db,
            django_scenario,
            mode=mode,
            scenario=simba_scenario,
        )
    finally:
        # Always reset the database to default
        for model in django.apps.apps.app_configs["ebustoolbox"].models.values():
            model.objects = model.objects.using("default")
    return simba_schedule, scenario


# custom exceptions for simulate_depot_strategy
class SimulationEventsMissingException(Exception):
    pass


class SimulationUnknownVehicleException(Exception):
    pass


class SimulationDepotsMissingException(Exception):
    pass


class SimulationDoubleArrivalException(Exception):
    pass


class SimulationDepartureFailException(Exception):
    pass


class SimulationExecutionFailException(Exception):
    pass


class SimulationZeroEventDurationException(Exception):
    pass


class SimulationNegativeEventDurationException(Exception):
    pass


class SimulationLateEventException(Exception):
    pass


def create_spiceev_scenario_dict(scenario: Scenario, split_vehicles=False) -> dict:  # noqa: C901
    events = scenario.event_set.filter(event_type=EventType.CHARGING_DEPOT)
    if not events.exists():
        raise SimulationEventsMissingException("SpiceEV scenario generation: no events found")

    args = get_args(scenario)
    start_simulation = events.order_by("time_start").first().time_start
    stop_simulation = events.order_by("time_end").last().time_end  # might be updated

    # SpiceEV vehicle types
    vehicle_types = {
        vehicle_type.id: {  # use ID as key, as name is not guaranteed to be unique
            "name": vehicle_type.name,
            "capacity": vehicle_type.battery_capacity,
            "charging_curve": vehicle_type.charging_curve,
            "min_charging_power": vehicle_type.minimum_charging_power,
            "v2g": (vehicle_type.v2g_curve is not None),
            "battery_efficiency": vehicle_type.charging_efficiency,
        }
        for vehicle_type in scenario.vehicletype_set.all()
    }

    vehicles = dict()
    vehicle_soc = get_initial_vehicle_soc(scenario)
    for vehicle in scenario.vehicle_set.all():
        vehicles[vehicle.to_simba_name()] = {
            "connected_charging_station": None,
            "soc": vehicle_soc[vehicle.id],
            "vehicle_type": vehicle.vehicle_type_id,
        }

    stations = get_electrified_stations_from_db(scenario)
    grid_connectors = dict()
    for name, station in stations.items():
        if station["type"] != "deps":
            # station["n_charging_stations"] seems to be more of a guideline, can't filter 0
            continue
        grid_connectors[name] = {
            "max_power": station.get("gc_power", args.gc_power_deps),
            "number_cs": station["n_charging_stations"],
            "power_per_charger": station.get("cs_power_deps_depb", args.cs_power_deps_depb),
            "voltage_level": station["voltage_level"],
            "cost": {"type": "fixed", "value": 1},
        }
    if len(grid_connectors) == 0:
        raise SimulationDepotsMissingException("SpiceEV scenario generation: no depots found")

    # get all depot events
    spice_ev_events = get_spiceev_events_from_scenario(
        scenario, skip_oppb=True, split_vehicles=split_vehicles
    )
    if len(spice_ev_events) == 0:
        raise SimulationEventsMissingException("SpiceEV scenario generation: no events found")

    # allocate vehicles to specific charging points at stations
    # order events by start time, departure events first
    spice_ev_events.sort(key=lambda e: (e["start_time"], e["event_type"] == "departure"))
    vehicle_to_cs = dict()  # LUT for connected vehicle -> (station name, charging station)
    # LUT station name -> maximum number of occupied CS at the same time.
    # May set number to None for unrestricted CS
    max_cs_dict = {gc: gc_info["number_cs"] for gc, gc_info in grid_connectors.items()}
    # LUT station name -> CS with vehicles
    occupied_cs = {gc: set() for gc in grid_connectors}
    # LUT station name -> CS with no vehicles
    unoccupied_cs = {gc: set() for gc in grid_connectors}

    for event in spice_ev_events:
        vid = event["vehicle_id"]

        if event["event_type"] == "arrival":
            if split_vehicles:
                # vehicle is split into multiple -> create new vehicle info
                # new vehicle ID are of form "parentVID#NR"
                # ignore number after last #, but there may be other # before in vehicle name
                parent_vid = "#".join(vid.split("#")[:-1])
                if parent_vid not in vehicles:
                    raise SimulationUnknownVehicleException(f"Unknown vehicle ID {vid}")
                if vid in vehicles:
                    raise Exception("Vehicles not split enough")
                # assume perfect charging at last station, reaching desired soc
                # (which is the same for all depots)
                vehicles[vid] = {
                    "connected_charging_station": None,
                    "soc": event["arrival_soc"],
                    "vehicle_type": vehicles[parent_vid]["vehicle_type"],
                }

            if vehicle_to_cs.get(vid) is not None:
                raise SimulationDoubleArrivalException(
                    f"SpiceEV scenario generation: double arrival {event}"
                )
            # find unoccupied charging station
            # station still means Station, not charging station
            station = event["update"]["connected_charging_station"]
            try:
                # default: pick any unoccupied station
                cs_id = unoccupied_cs[station].pop()
            except KeyError:
                # no unoccupied station available: create new one
                new_idx = len(occupied_cs[station])
                cs_id = f"{station}_{new_idx}"
                max_cs = max_cs_dict[station]
                if max_cs is not None and new_idx + 1 >= max_cs:
                    # max number of CS of station exceeded
                    logger.warning(
                        f"S.ID:{scenario.id}:SpiceEV scenario generation: Station {station} "
                        f"exceeds maximum number of charging stations ({max_cs})."
                    )
                    # disable further warnings
                    max_cs_dict[station] = None
            # take note in lookup tables for future reference (departure)
            occupied_cs[station].add(cs_id)
            vehicle_to_cs[vid] = (station, cs_id)
            # update event station from Station (GC) name to charging station
            event["update"]["connected_charging_station"] = cs_id
        elif event["event_type"] == "departure":
            try:
                station, cs_id = vehicle_to_cs[vid]
            except KeyError:
                raise SimulationDepartureFailException(
                    f"SpiceEV scenario generation: departure without arrival {event}"
                )
            # clear occupied state
            occupied_cs[station].remove(cs_id)
            unoccupied_cs[station].add(cs_id)
            vehicle_to_cs[vid] = None

            # simulation will always end after last charging is finished
            stop_simulation = max(stop_simulation, datetime.fromisoformat(event["start_time"]))

    # create needed charging stations
    charging_stations = dict()
    for station, station_info in grid_connectors.items():
        for cs_id in occupied_cs[station] | unoccupied_cs[station]:
            charging_stations[cs_id] = {
                "max_power": station_info["power_per_charger"],
                "parent": station,
            }

    # compute number of intervals: simulate whole last timestep
    n_intervals = -int((start_simulation - stop_simulation) // timedelta(minutes=args.interval))
    # and one more timestep, since vehicle soc are taken at begin of each timestep
    n_intervals += 1
    if split_vehicles:
        # remove original vehicles from simulation, retain only split vehicles
        for vehicle in scenario.vehicle_set.all():
            del vehicles[vehicle.to_simba_name()]

    return {
        "scenario": {
            "start_time": start_simulation.isoformat(),
            "interval": args.interval,  # minutes
            "n_intervals": n_intervals,
        },
        "components": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": grid_connectors,
            "charging_stations": charging_stations,
            # batteries and photovoltaics ignored
        },
        "events": {
            "vehicle_events": spice_ev_events,
            "grid_operator_signals": [],
        },
        "args": vars(args).copy(),
    }


def get_initial_vehicle_soc(scenario: Scenario) -> dict:
    # get initial soc of all vehicles (saved in vehicle events)
    # returns dict Vehicle ID -> float
    vehicles = scenario.vehicle_set.all()
    events = scenario.event_set.order_by("time_start")
    vehicle_soc = dict()
    for vehicle in vehicles:
        first_vehicle_event = events.filter(vehicle=vehicle).first()
        if first_vehicle_event is not None:
            vehicle_soc[vehicle.id] = first_vehicle_event.soc_start
        else:
            vehicle_soc[vehicle.id] = None
    return vehicle_soc


def get_spiceev_events_from_scenario(scenario, skip_oppb=False, split_vehicles=False):
    """
    Create SpiceEV-like event dictionaries for a Scenario

    skip_oppb: only use depot events
    split_vehicles: each charging event is independent from others,
        generating a new vehicle for every charge
    """

    events = scenario.event_set.order_by("time_start")
    event_list = list()
    if not events.exists():
        return event_list
    # all events known at scenario start
    scenario_start_time = events.first().time_start

    vehicle_soc = get_initial_vehicle_soc(scenario)

    # avoid non-station events from older simulations
    events = events.filter(station_id__isnull=False)
    # prefetch stations and vehicles from events (less queries, faster lookup)
    events = events.select_related("station", "vehicle")
    # get all charging events
    charging_events = events.filter(event_type=EventType.CHARGING_DEPOT)
    if not skip_oppb:
        charging_events = charging_events.union(
            events.filter(event_type=EventType.CHARGING_OPPORTUNITY)
        )
    # for split_vehicles: how many new vehicles have been created from original?
    # vid -> count
    vehicle_counter = dict()
    # iterate over events in-order, creating SpiceEV event-dicts for each charging event
    for event in charging_events:
        vid = event.vehicle.to_simba_name()
        if split_vehicles:
            v_nr = vehicle_counter.get(vid, 0)
            vehicle_counter[vid] = v_nr + 1
            vid = f"{vid}#{v_nr}"

        # find adjacent standby event (can still charge)
        next_event = Event.objects.filter(
            event_type=EventType.STANDBY_DEPARTURE,
            vehicle_id=event.vehicle_id,  # vehicle is linked to scenario
            subloc_no=event.subloc_no,  # vehicle must not have moved
            time_start=event.time_end,
        ).first()
        # create arrival event
        arrival_event = {
            "signal_time": scenario_start_time.isoformat(),
            "start_time": event.time_start.isoformat(),
            "vehicle_id": vid,
            "event_type": "arrival",
            "arrival_soc": event.soc_start,
            "update": {
                "connected_charging_station": event.station.to_simba_name(),
                "estimated_time_of_departure": None,  # updated later
                "soc_delta": (
                    0 if split_vehicles else event.soc_start - vehicle_soc[event.vehicle_id]
                ),
                "desired_soc": event.soc_end,
            },
        }

        # create departure event (end of charging/standby, not necessarily leaving station)
        departure_time = event.time_end
        if next_event:
            # check for additional standby events
            if Event.objects.filter(
                event_type=EventType.STANDBY_DEPARTURE,
                vehicle_id=event.vehicle_id,
                subloc_no=event.subloc_no,
                time_start=next_event.time_end,
            ).exists():
                logger.warning(
                    f"S.ID:{scenario.id}:Multiple standby departure events back-to-back for "
                    f"{event.vehicle_id} at {next_event.time_end.isoformat()}"
                )
            # use standby departure time, with some buffer
            event = next_event
            # departure_time = next_event.time_end
            departure_time = max(departure_time, next_event.time_end - STANDBY_BUFFER)

        arrival_event["update"]["estimated_time_of_departure"] = departure_time.isoformat()
        event_list.append(arrival_event)

        event_list.append(
            {
                "signal_time": scenario_start_time.isoformat(),
                "start_time": departure_time.isoformat(),
                "vehicle_id": vid,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": None,
                },
            }
        )

        # TODO: Thos logic brakes if a single event can not deliver the needed charge
        # update soc
        vehicle_soc[event.vehicle_id] = event.soc_end

    return event_list


def simulate_depot_strategy(spice_ev_scenario_dict: dict, strategy: str) -> SimbaScenario:
    # run SpiceEV simuation with given strategy
    if strategy not in STRATEGIES:
        raise NotImplementedError(f"Strategy {strategy} not supported")

    spice_ev_scenario = SimbaScenario(spice_ev_scenario_dict)
    spice_ev_scenario.run(strategy, spice_ev_scenario_dict["args"])
    if spice_ev_scenario.step_i != spice_ev_scenario.n_intervals:
        raise SimulationExecutionFailException("SpiceEV simulation aborted, see above for details")
    return spice_ev_scenario


def abbreviate_list(long_list: list, tail_elements: int = 2, delimiter: str = ",", fmt="") -> str:
    delimiter += " "
    if not len(long_list) > tail_elements * 2:
        return "[ " + delimiter.join(map(str, long_list)) + " ]"
    return (
        "[ "
        + delimiter.join(format(x, fmt) for x in long_list[:tail_elements])
        + " ... "
        + delimiter.join(format(x, fmt) for x in long_list[-tail_elements:])
        + " ] "
        + f"{len(long_list)} elements"
    )


def replace_event_timeseries(event: Event, soc_ts: list) -> None:
    # replace Event soc timeseries with arbitrary list
    # ### sanity checks ### #
    if len(soc_ts) < 2:
        if not isinstance(event.timeseries, dict):
            event.timeseries = dict()
        event.timeseries["soc"] = [event.soc_start, event.soc_end]
        event.timeseries["time"] = [event.time_start.isoformat(), event.time_end.isoformat()]
        return

    # event soc should always be defined / not null
    # this is only the case when the event has at least 1 timestep -> Duration >=1 minute
    # these cases already returned early above with using the event.soc_ values directly for the
    # timeseries
    assert all([soc is not None for soc in soc_ts])

    # NOTE: start and end soc must remain the same (event.soc_end == next_event.soc_start)
    # Allow deviation in timeseries and soc_end. The original values are kept to avoid cascade.
    if not (abs(soc_ts[0] - event.soc_start) < EPS):
        logger.error(
            f"S.ID:{event.scenario.id}:Depot Charging Simulation diverged\n"
            f"Delta of {abs(soc_ts[0] - event.soc_start)} at {event}.\n"
            f"{event.soc_start} Start Soc\n Timeseries:\n{abbreviate_list(soc_ts, fmt='.2e')}"
        )
    if not (abs(soc_ts[-1] - event.soc_end) < EPS):
        logger.error(
            f"S.ID:{event.scenario.id}:Depot Charging Simulation diverged\n"
            f"Delta of {abs(soc_ts[-1] - event.soc_end)} at {event}.\n"
            f"{event.soc_end} END SOC\n Timeseries:\n{abbreviate_list(soc_ts, fmt='.2e')}"
        )

    # re-create timestamps series
    interval = (event.time_end - event.time_start) / (len(soc_ts) - 1)
    if not event.time_start < event.time_end:
        raise SimulationNegativeEventDurationException(f"{event.id=} has a negative duration")
    if not interval > timedelta(seconds=0):
        raise SimulationZeroEventDurationException(f"{event.id=} has zero duration")
    event.timeseries = {
        "time": [(event.time_start + i * interval).isoformat() for i in range(len(soc_ts))]
    }

    # soc and time lists must have same length
    assert len(soc_ts) == len(event.timeseries["time"])
    assert event.timeseries["time"][0] == event.time_start.isoformat()

    # handle floating point errors
    # there are cases above 1 milliseconds, so lets just make it a second to never worry about it
    assert abs(datetime.fromisoformat(event.timeseries["time"][-1]) - event.time_end) <= timedelta(
        seconds=1
    )
    # remove the floating point error
    event.timeseries["time"][-1] = event.time_end.isoformat()

    # save to DB
    event.timeseries["soc"] = soc_ts


def get_ts_index_from_time(scenario: SimbaScenario, time: datetime) -> int:
    # find index relative to scenario start time, rounded down
    return -((scenario.start_time - time) // scenario.interval)


def get_tail_index(arr: list) -> int:
    """
    Count number of same values at tail of list

    Examples:
    [1,2,3] -> 1
    [1,2,2] -> 2
    [2,2,2] -> 3
    [] -> 0
    """
    for i, x in enumerate(reversed(arr)):
        if x != arr[-1]:
            return i
    return len(arr)


def apply_depot_strategy(scenario: Scenario, strategy: str, split_vehicles=False) -> None:
    # simulate all depot charging in SpiceEV with new strategy, update timeseries
    spice_ev_scenario_dict = create_spiceev_scenario_dict(scenario, split_vehicles=split_vehicles)
    spice_ev_scenario = simulate_depot_strategy(spice_ev_scenario_dict, strategy)
    # attach vehicle soc to SpiceEV scenario
    spice_ev_report.generate_soc_timeseries(spice_ev_scenario)
    # update events with new soc timeseries
    events = scenario.event_set.filter(event_type=EventType.CHARGING_DEPOT).order_by("time_start")
    # keep track of changed events
    event_list = list()
    events_to_delete = list()
    interval = spice_ev_scenario.interval
    # for split_vehicles: how many new vehicles have been created from original?
    # vid -> count
    vehicle_counter = dict()
    for event in events:
        # charging might include following standby_departure
        next_event = Event.objects.filter(
            event_type=EventType.STANDBY_DEPARTURE,
            vehicle_id=event.vehicle_id,  # vehicle is linked to scenario
            subloc_no=event.subloc_no,  # vehicle must not have moved
            time_start=event.time_end,
        ).first()

        vid = event.vehicle.to_simba_name()
        # find timeseries timestep range (indices of relevant timesteps)
        ts_start = get_ts_index_from_time(spice_ev_scenario, event.time_start)
        departure_time = event.time_end
        if next_event is not None:
            departure_time = max(departure_time, next_event.time_end - STANDBY_BUFFER)
        ts_end = get_ts_index_from_time(spice_ev_scenario, departure_time)

        if split_vehicles:
            v_nr = vehicle_counter.get(vid, 0)
            vehicle_counter[vid] = v_nr + 1
            vid = f"{vid}#{v_nr}"

        # end timestep is inclusive in range, might be after end of SpiceEV scenario
        time_range = range(ts_start, ts_end + 1)
        socs = [
            spice_ev_scenario.vehicle_socs[vid][min(i, spice_ev_scenario.step_i - 1)]
            for i in time_range
        ]
        if next_event is None:
            # no standby: just replace SoC timeseries
            replace_event_timeseries(event, socs)
        else:
            # standby event exists: split charging and standby

            # find index when soc does not change anymore (end of charging)
            idx_stop_charging = len(socs) - get_tail_index(socs)

            ts_stop_charging = (
                spice_ev_scenario.start_time + (ts_start + idx_stop_charging) * interval
            )

            # make sure the calculated times are correct with a max error of the spiceev timestep
            if not (
                spice_ev_scenario.start_time + (ts_start) * interval <= event.time_start + interval
            ):
                raise SimulationLateEventException(
                    "SpiceEV charging start timestep diverges from event.time_start"
                )
            if not (
                spice_ev_scenario.start_time + (ts_start + idx_stop_charging) * interval
                <= next_event.time_end + interval
            ):

                raise SimulationLateEventException(
                    "SpiceEV charging end timestep diverges from event.time_start"
                )
            if next_event.time_end > ts_stop_charging > event.time_start:
                # The charging stopped inside the events:
                # keep both events
                socs_charging = socs[: idx_stop_charging + 1]
                socs_standby = socs[idx_stop_charging:]
                len_buffer = int((next_event.time_end - departure_time) / interval)
                socs_buffer = [socs[-1]] * len_buffer
                # adjust event start/end timestamps
                event.time_end = ts_stop_charging
                next_event.time_start = ts_stop_charging
                replace_event_timeseries(event, socs_charging)
                replace_event_timeseries(next_event, socs_standby + socs_buffer)
                event_list.append(next_event)
            elif ts_stop_charging > next_event.time_end:
                # the charging event stopped after the next event.
                # the event is deleted, the overlapping time is ignored
                # the first event expands to include the whole next_event
                logger.info(
                    f"{next_event} will be deleted. The previous event{event} will end at "
                    f"{next_event.time_end.isoformat()} instead of the SpiceEv simulated "
                    f"{ts_stop_charging.isoformat()}. "
                    f"{(ts_stop_charging-next_event.time_end).total_seconds()} s difference"
                )
                event.time_end = next_event.time_end
                replace_event_timeseries(event, socs)
                events_to_delete.append(next_event)
        event_list.append(event)

    Event.objects.bulk_update(event_list, ["timeseries", "time_start", "time_end"])
    Event.objects.filter(id__in=[e.id for e in events_to_delete]).delete()
    logger.info(f"S.ID:{scenario.id}:{events.count()} depot charging events updated")


def apply_depot_and_area_wishes(mutation: Scenario, child: Scenario, stack: dict) -> None:
    depot_configs = DepotConfigurationWish.objects.filter(scenario=mutation)
    # Assert uniqueness of the mutations
    new_depot_configs = []
    new_area_infos = []
    old_ids = [x.id for x in depot_configs]
    for depot_config in depot_configs:
        depot_config: DepotConfigurationWish
        search_station = StationMutation.objects.get(
            mutated_original_station=depot_config.station
        ).original_station
        depot_config.station_id = stack[Station][search_station.id]
        depot_config.scenario = child
        depot_config.id = None
        new_depot_configs.append(depot_config)

    new_depot_configs = DepotConfigurationWish.objects.bulk_create(new_depot_configs)
    for old_id, new_depot_config in zip(old_ids, new_depot_configs):
        area_infos = AreaInformation.objects.filter(
            scenario=mutation, depot_configuration_wish_id=old_id
        )
        for area_info in area_infos:
            area_info: AreaInformation
            area_info.scenario = child
            search_vt = VehicleTypeMutation.objects.get(
                mutated_vehicle_type=area_info.vehicle_type
            ).original_vehicle_type
            area_info.vehicle_type_id = stack[VehicleType][search_vt.id]
            area_info.depot_configuration_wish = new_depot_config
            area_info.id = None
            new_area_infos.append(area_info)

    AreaInformation.objects.bulk_create(new_area_infos)


def apply_station_mutation(mutation: Scenario, child: Scenario, stack: dict) -> None:
    station_mutations = StationMutation.objects.filter(scenario=mutation)

    # Assert uniqueness of the mutations
    station_mut_list = station_mutations.values_list("original_station", flat=True)
    assert len(station_mut_list) == len({station for station in station_mut_list})
    station_mut_list = station_mut_list.values_list("mutated_original_station", flat=True)
    assert len(station_mut_list) == len({station for station in station_mut_list})
    assert len(station_mut_list) == Station.objects.filter(scenario=mutation).count()
    for station_mutation in station_mutations:
        org_station = station_mutation.original_station
        mutated_station = station_mutation.mutated_original_station
        copied_station_id = stack[Station][org_station.id]
        mutated_station.id = copied_station_id
        mutated_station.scenario = child
        mutated_station.save()


def apply_vehicle_mutation(mutation: Scenario, child: Scenario, stack: dict) -> None:
    vehicle_type_mutations = VehicleTypeMutation.objects.filter(
        scenario=mutation,
    )
    vt_mut_list = vehicle_type_mutations.values_list("original_vehicle_type", flat=True)
    assert len(vt_mut_list) == len({vt for vt in vt_mut_list})
    vt_mut_list = vehicle_type_mutations.values_list("mutated_vehicle_type", flat=True)
    assert len(vt_mut_list) == len({vt for vt in vt_mut_list})
    assert len(vt_mut_list) == VehicleType.objects.filter(scenario=mutation).count()

    for vt_mut in vehicle_type_mutations:
        org_vt = vt_mut.original_vehicle_type
        vt = vt_mut.mutated_vehicle_type
        assert vt is not None and org_vt is not None
        copied_vt_id = stack[VehicleType][org_vt.id]
        instance = apply_vehicle_type(VehicleType.objects.get(id=copied_vt_id), vt)
        assert instance.id == copied_vt_id
        assert instance.scenario == child


def assign_new_vehicles_to_db(django_scenario: Scenario, db_name="default") -> None:
    """Assign a new vehicle to every rotation


    Already assigned vehicles are deleted
    :param django_scenario: Scenario that gets added vehicles and rotation assignments.
    :return: None
    """
    Vehicle.objects.using(db_name).filter(scenario=django_scenario).delete()
    rotations = []
    vehicles = []
    for i, r in enumerate(Rotation.objects.using(db_name).filter(scenario=django_scenario)):
        vt = r.vehicle_type
        v_name = "Vehicle_" + str(i)
        vehicle = Vehicle(scenario=django_scenario, vehicle_type=vt, name=v_name)
        vehicles.append(vehicle)
        rotations.append(r)

    # returned list of vehicles contains the pks needed for rotation creation
    vehicles = Vehicle.objects.bulk_create(vehicles)
    for vehicle, rotation in zip(vehicles, rotations):
        rotation.vehicle = vehicle
    Rotation.objects.bulk_update(rotations, ["vehicle"])


def deepcopy_scenario(scenario: Scenario) -> tuple[Scenario, dict]:
    """Deepcopy a scenario.

    Scenario to be deepcopied must have values which can be deepcopied without specific knowledge
    of implementation, e.g. if a value like the task_id has to be unique, the scenario has to
    be mutated before being deepcopied.
    :param scenario: Scenario to be deepcopied
    :type scenario: Scenario
    :return: deepcopied Scenario, stack which links original with copied instances
    """
    copied_instance, stack = core.deepcopy.deepcopy_and_sequence_reset(
        scenario,
        exclude_models={Scenario, User, Event, Progress, UserGroup, Notification},
        exclude_fields={
            DepotSelection._meta.get_field("depots"),
            VehicleTypeMutation._meta.get_field("original_vehicle_type"),
            StationMutation._meta.get_field("original_station"),
        },
        max_depth=1,
    )
    return copied_instance, stack


def create_empty_child_scenario(parent_scenario: Scenario, task_id):
    parent_id = parent_scenario.id
    # Decouple memory of parent and child
    new_child_scenario = Scenario.objects.get(id=parent_scenario.id)
    new_child_scenario.task_id = task_id
    new_child_scenario.parent_id = parent_id
    new_child_scenario.id = None
    new_child_scenario.save()
    return new_child_scenario


@atomic()
def create_scenario_copy_for_user(mutation_scenario: Scenario):
    assert isinstance(mutation_scenario, Scenario)
    assert mutation_scenario.parent is not None
    # Assert no deeper nesting than source file
    if mutation_scenario.parent.parent:
        assert mutation_scenario.parent.parent.parent is None
    mutation_scenario.task_id = ebustoolbox.util.get_unique_task_id()
    copied_scenario, stack = deepcopy_scenario(mutation_scenario)

    # TODO remove this and move copying into deepcopy with excluded fields instead
    vehicle_type_selections = VehicleTypeSelection.objects.filter(
        vehicle_type__scenario=mutation_scenario
    )
    for vts in vehicle_type_selections:
        new_vt_id = stack[VehicleType][vts.vehicle_type.id]
        vts.id = None
        vts.vehicle_type = VehicleType.objects.get(id=new_vt_id)
        vts.save()
    return copied_scenario


@atomic()
def create_child_from_mutation(parent_scenario: Scenario, mutation: Scenario) -> Scenario:
    """Create a child scenario from a mutation and parent scenario

    :param parent_scenario: Parent scenario
    :type parent_scenario: Scenario
    :param mutation: Mutation
    :type mutation: Scenario
    :return: Child scenario
    :rtype: Scenario
    """

    parent_scenario.task_id = ebustoolbox.util.get_unique_task_id()
    child, stack = deepcopy_scenario(parent_scenario)
    parent_scenario.refresh_from_db()
    child.parent = mutation
    child.simba_options.update(mutation.simba_options)
    if not child.simba_options:
        child.simba_options = vars(get_args(child))
    child.save()

    # Mutate child according to parent
    # Remove rotations from the timespan
    # trim_scenario(child, time_delta, sim_range.start)
    # # Used for clearing up depots without rotations
    # trim_depots(child, [])

    # Copy Temperatures
    temperatures_query = Temperatures.objects.filter(scenario=mutation)

    if temperatures_query.exists():
        assert temperatures_query.count() == 1
        temperature = temperatures_query.first()
        temperature.id = None
        temperature.scenario = child
        temperature.save()

    # child.simba_options.update(ele_dict)
    all_stations = Station.objects.filter(scenario=mutation)
    electrified_stations = Station.objects.filter(scenario=mutation, is_electrified=True)
    excluded_stations = Station.objects.filter(
        scenario=mutation, is_electrified=False, is_electrifiable=False
    )
    # Some stations are not electrified or excluded -->possible need for optimization
    if all_stations.count() > electrified_stations.count() + excluded_stations.count():
        logger.info(f"S.ID:{mutation.id}:Mode is set to optimization.")
        child.simba_options["modes"] = "sim,station_optimization"
    else:
        logger.info(f"S.ID:{mutation.id}:Mode is set to NO optimization.")
        child.simba_options["modes"] = "sim"

    apply_vehicle_mutation(mutation, child, stack)
    apply_station_mutation(mutation, child, stack)
    apply_depot_and_area_wishes(mutation, child, stack)
    # Must run after apply_vehicle_mutation and apply_station_mutation: those copy
    # whole rows, so the child's battery_type_id / charging_point_type_id still point
    # at the mutation's rows until this repoints them.
    apply_tco_mutation(mutation, child, stack)

    child.save()
    return child


@atomic()
def create_station_mutations(scenario):
    Station.objects.filter(scenario=scenario).delete()
    stations = []
    mutations = []
    # Create a station for each station in the parent scenario
    for station in Station.objects.filter(scenario=scenario.parent):
        mutations.append(station.id)
        station.scenario = scenario
        station.id = None
        stations.append(station)
    stations = Station.objects.bulk_create(stations)

    mutation_dict = {mutation: new_station.id for mutation, new_station in zip(mutations, stations)}

    # Create a station mutation which link the original and mutation
    station_mutations = []
    for original, mutation in mutation_dict.items():
        sm = StationMutation(
            scenario=scenario,
            original_station_id=original,
            mutated_original_station_id=mutation,
        )
        station_mutations.append(sm)
    StationMutation.objects.bulk_create(station_mutations)


@shared_task(bind=True)
def _run_ebus_toolchain(self, task_id, progress_id=None):
    """Run the tool chain"""
    db_scenario: Scenario = Scenario.objects.get(task_id=task_id)
    assert is_consistent(db_scenario)

    # With multiple simulations the progress is linked through the parent to its child scenarios
    if not progress_id:
        progress = Progress.objects.filter(
            scenario=db_scenario.parent, progress_type=EnumProgress.RUNNING_SIMULATION
        ).first()
        if not progress:
            logger.warning(
                f"S.ID:{db_scenario.id}:"
                "The toolchain did not find a progress belonging to the parent of the scenario. "
                "Creating a Progress bound to the simulation scenario instead"
            )
            progress = Progress.objects.create(
                scenario=db_scenario, progress_type=EnumProgress.RUNNING_SIMULATION, task_id=task_id
            )
    else:
        progress = Progress.objects.get(task_id=progress_id)

    # Clean up of previous notifications which can be produced during the simulation
    # without cleaning they might appear multiple times, from previous failed simulations
    Notification.objects.filter(
        scenario__in=[db_scenario, db_scenario.parent],
        notification_type__in=[
            EnumNotificationType.DELAYED_TRIP_WARNING,
            EnumNotificationType.UNEXPECTED_ERROR,
            EnumNotificationType.UNSTABLE_DEPOT_WARNING,
        ],
    ).delete()

    try:
        logger.info(f"S.ID:{db_scenario.id}:Getting schedule from db {datetime.now()}")
        schedule, args = get_schedule_from_db(db_scenario)

        # in the first run Depots can stay un electrified since simba does not do depot calculations
        # TODO: keep that?
        for depot in Depot.objects.filter(scenario=db_scenario):
            try:
                del schedule.stations[depot.station.to_simba_name()]
            except KeyError:
                pass
        progress.save()

        # call SimBA and eFLIPS
        # Currently the toolchain is called in the same way independent from input
        # SimBA Simulation
        # SimBA optimization if blocks are negative
        # eFLIPS Simulation
        # SimBA consolidation
        Event.objects.filter(scenario=db_scenario).delete()

        progress.status = _("Berechne Verbrauch")
        progress.save()
        modes = db_scenario.simba_options["modes"].split(",")
        assert modes[0] == "sim"
        schedule, simba_scenario = run_simba(
            schedule, args, db_scenario, mode=modes[0], scenario=None
        )
        progress.current_work += 1
        progress.save()

        if len(modes) > 1 and "station_optimization" in modes[1]:
            progress.status = "Elektrifziere notwendige Stationen"
            progress.save()
            try:
                schedule, simba_scenario = run_simba(
                    schedule, args, db_scenario, mode=modes[1], scenario=simba_scenario
                )
            except StationOpimizationImpossible:
                Notification.objects.create(
                    # Notifications should be saved to the mutation.
                    # If the toolchain is run without a parent the notification is saved to the scenario
                    scenario=db_scenario.parent or db_scenario,
                    sender="SimBA-Optimizier from tasks.py",
                    level=EnumNotificationLevels.INFO,
                    notification_type=EnumNotificationType.ADDED_ELECTRIFICATION,
                    # TODO: Add text to help section
                    message=_(
                        "Die Stationsoptimierung konnte das Szenario nicht optimieren. "
                        "Genauere Information finden sie in der Hilfe unter Stationsoptimierung"
                    ),
                )
                schedule, args = get_schedule_from_db(db_scenario)
            finally:
                # Create notifications for the user
                # if the optimizer could not achieve full electrification
                create_negative_block_notifications(db_scenario)
        else:
            logger.info(f"S.ID:{db_scenario.id}:Station optimization was skipped")

        progress.current_work += 1
        progress.status = _("Berechne das Depot")
        progress.save()
        notifications = []
        try:
            run_eflips(db_scenario, delete_existing_depot=True, progress=progress)
        except UnstableSimulationException as e:
            # TODO: handle it and pass information to user
            logger.error(f"S.ID:{db_scenario.id}:The simulation is unstable")
            logger.error(traceback.format_exception(e))
            notification = Notification(
                scenario=db_scenario.parent or db_scenario,
                sender="eflips-depot",
                level=EnumNotificationLevels.WARNING,
                notification_type=EnumNotificationType.UNSTABLE_DEPOT_WARNING,
                message=_(
                    "Das Szenario ist nicht stabil. Mit den gegebenen Randbedingungen "
                    "sinkt, der SoC bei wiederholten Iterationen. Eine Erhöhung der "
                    "Nachladeleistung kann das Problem beheben."
                ),
            )
            notifications.append(notification)
        except DelayedTripException as e:
            # TODO: handle it and pass information to user
            logger.error(f"S.ID:{db_scenario.id}:There are delays in the Simulation")
            logger.error(traceback.format_exception(e))
            # TODO: @TU what notification should the user receive
            notification = Notification(
                scenario=db_scenario.parent or db_scenario,
                sender="eflips-depot",
                level=EnumNotificationLevels.WARNING,
                notification_type=EnumNotificationType.DELAYED_TRIP_WARNING,
                message=_("Manche Fahrzeuge können nur verspätet abfahren"),
            )
            notifications.append(notification)
        except Exception as e:
            logger.error(f"S.ID:{db_scenario.id}:Eflips raised an unexpected Exception")
            logger.error(traceback.format_exception(e))
            notification = Notification(
                scenario=db_scenario.parent or db_scenario,
                sender="eflips-depot",
                level=EnumNotificationLevels.ERROR,
                notification_type=EnumNotificationType.UNEXPECTED_ERROR,
                message=_("Ein unerwarteter Fehler ist aufgetreten! "),
            )
            notifications.append(notification)
            # Let all progress no of the failed simulation
            set_scenario_progress_failed(db_scenario, e)
            raise
        finally:
            Notification.objects.bulk_create(notifications)

        progress.current_work += 1
        progress.save()
        eflips_assignment = get_assigned_vehicles(task_id)
        schedule.assign_vehicles_custom(eflips_assignment)

        # TODO: Keep that? / Set Depot values for final SimBA simulation?
        electrify_depot_station_w_default(db_scenario)
        #
        # get electrified stations from db, e.g. depot station from eFLIPS with
        # power
        stations_dict = get_electrified_stations_from_db(db_scenario)
        schedule.stations = stations_dict.copy()

        # Simba Run to add back the deleted events of eflips.
        # Since some blocks might start at lowered socs SimBA recalculation is appropriate.
        # This will shift the driving SOCs towards 0, but posssibly increase charge due to
        # higher charging rates at lower socs
        simba_scenario = run_simba(schedule, args, db_scenario, mode="sim", scenario=None)

        consolidate_socs(db_scenario)

        # NOTE: Consolidate results with a given strategy. EPS of 1% needed.
        # Force balanced_check so balanced strategy makes sure to achieve desired soc even with charging curves
        apply_depot_strategy(db_scenario, "balanced_check", split_vehicles=True)

        progress.current_work += 1
        progress.save()

        # Calculate TCO. Needs vehicle (driving events) to be set.
        logger.info(f"S.ID:{db_scenario.id}:Running eFLIPS TCO {datetime.now()}")
        progress.current_work += 1
        progress.status = _("Berechne TCO")
        progress.save()

        tco_result = eflips_calculate_tco(db_scenario)
        db_scenario.tco_result = tco_result
        db_scenario.save(update_fields=["tco_result"])

        # After the TCO, which is what attaches the charging point types the LCA reads
        # off the Areas and Stations. A failure here is logged and swallowed: the LCA is
        # a secondary result, and losing a finished simulation over it would be a poor
        # trade. The result page renders the section only when there is something in it.
        logger.info(f"S.ID:{db_scenario.id}:Running eFLIPS LCA {datetime.now()}")
        progress.status = _("Berechne Ökobilanz")
        progress.save()
        try:
            db_scenario.lca_result = calculate_lca(db_scenario)
            db_scenario.save(update_fields=["lca_result"])
        except Exception:
            logger.error(f"S.ID:{db_scenario.id}:LCA failed\n{traceback.format_exc()}")

        check_event_soc_consistency(db_scenario)
        db_scenario.refresh_from_db()
        db_scenario.finished = timezone.now()
        db_scenario.save()
    except Exception as e:
        logger.error(traceback.format_exc())
        # Let all progress no of the failed simulation
        Notification.objects.create(
            scenario=db_scenario.parent or db_scenario,
            sender="eflips-depot",
            level=EnumNotificationLevels.ERROR,
            notification_type=EnumNotificationType.UNEXPECTED_ERROR,
            message=_("Ein unerwarteter Fehler ist aufgetreten! "),
        )
        set_scenario_progress_failed(db_scenario, e)
        raise


def set_scenario_progress_failed(scenario: Scenario, exception: Exception | None = None):
    mutation_progress = Progress.objects.filter(
        scenario=scenario.parent, progress_type=EnumProgress.RUNNING_SIMULATION
    ).first()
    if mutation_progress:
        mutation_progress.errors.append(str(exception))
        mutation_progress.set_failed()
    # Also set simulation specific scenario progress to failed
    simulation_scenario_progress = Progress.objects.filter(
        scenario=scenario, progress_type=EnumProgress.RUNNING_SIMULATION
    ).first()
    if simulation_scenario_progress:
        simulation_scenario_progress.errors.append(str(exception))
        simulation_scenario_progress.set_failed()


def check_event_soc_consistency(db_scenario: Scenario):
    """Give warning if scenario events are not consistent.

    Consistency in this case is that soc_end values are identical to the next events soc_start of the same vehicle.
    """
    logger.info(f"S.ID:{db_scenario.id}:" + 50 * "#" + "\nChecking event consistency")
    consistent = True
    for vehicle in Vehicle.objects.filter(scenario=db_scenario):
        events = list(Event.objects.filter(vehicle=vehicle).order_by("time_start"))
        for i in range(len(events) - 2):
            event = events[i]
            next_event = events[i + 1]
            if not event.soc_end == next_event.soc_start:
                delta = event.soc_end - next_event.soc_start
                logger.warning(
                    f"S.ID:{db_scenario.id}:SOC does not align between events for {vehicle=} for "
                    # f"events {events[i]} and {events[i+1]}"
                    f"\n DELTA = {delta}\n"
                    f"\n{event.id} {event.event_type} and {next_event.id} {next_event.event_type}"
                )
                consistent = False

            if not events[i].time_end == events[i + 1].time_start:
                logger.warning(
                    f"S.ID:{db_scenario.id}:Times do not align for Events {events[i].id} and {events[i+1].id} "
                )
                consistent = False
    if consistent:
        logger.info(f"S.ID:{db_scenario.id}:" + 50 * "#" + "\nEvents did not show inconsistencies")


def electrify_depot_station_w_default(db_scenario):
    configs = {
        x.station.id: x for x in list(DepotConfigurationWish.objects.filter(scenario=db_scenario))
    }
    max_vehicles = Rotation.objects.filter(scenario=db_scenario).count()
    for depot in Depot.objects.filter(scenario=db_scenario):
        logger.warning(
            f"S.ID:{db_scenario.id}:Overwriting Depot Station data. This data should be provided by eflips"
        )
        station = depot.station
        config: DepotConfigurationWish = configs[station.id]
        if config.auto_generate:
            charging_power = config.default_power
        else:
            charging_power = AreaInformation.objects.filter(
                depot_configuration_wish=config
            ).aggregate(Max("power"))["power__max"]
        # TODO: get defaults from somewhere
        station.is_electrified = True
        station.power_total = station.power_total or (max_vehicles + 1) * charging_power
        station.amount_charging_places = station.amount_charging_places or (max_vehicles + 1)
        station.power_per_charger = station.power_per_charger or (charging_power)
        station.charge_type = EnumChargeType.DEPOT.value
        station.voltage_level = station.voltage_level or EnumVoltageLevel.VOLTAGE_MV.value
        station.save()
        logger.info(station)


def get_assigned_vehicles(task_id: str) -> List[dict]:
    """
    Retrieves assigned vehicles for a given task ID, considering previous events.

    Args:
        task_id (str): The ID of the task associated with the scenario.

    Returns:
        List[dict]: A list of dictionaries containing assigned vehicle information, including
        rotation name, vehicle ID, and state of charge (SOC) at the end of the previous event.

    Raises:
        Scenario.DoesNotExist: If the scenario with the specified task ID does not exist.

    Note:
        This function retrieves assigned vehicles based on the given task ID and considers previous
        events to determine the state of charge (SOC) at the end of the previous event.
    """

    scenario = Scenario.objects.get(task_id=task_id)
    used_vehicles = Vehicle.objects.filter(rotation__scenario=scenario).distinct()
    # Delete the old vehicles which are not used anymore
    Vehicle.objects.filter(scenario=scenario).exclude(id__in=used_vehicles).delete()
    events = Event.objects.filter(scenario=scenario)

    all_rotations = Rotation.objects.filter(scenario=scenario)
    vehicle_assigns = []
    vehicle_counter_dict = {
        v.vehicle_type.id: {EnumChargeType.OPPORTUNITY: 0, EnumChargeType.DEPOT: 0}
        for v in used_vehicles
    }
    counted_vehicles = set()
    for rot in all_rotations:
        first_trip = Trip.objects.filter(rotation=rot).order_by("departure_time").first()
        assert first_trip is not None, f"Rotation {rot.id} / {rot.name} has no trips"
        vehicle = rot.vehicle
        assert vehicle is not None, f"Rotation {rot.id} / {rot.name} has no vehicle"
        if vehicle not in counted_vehicles:
            vt = vehicle.vehicle_type
            if vt.opportunity_charging_capable:
                ct = EnumChargeType.OPPORTUNITY.value
            else:
                ct = EnumChargeType.DEPOT.value

            vehicle_counter_dict[vt.id][ct] += 1
            counted_vehicles.add(vehicle)

        prev_event = (
            events.filter(time_end__lte=first_trip.departure_time, vehicle=vehicle)
            .order_by("time_end")
            .last()
        )
        if prev_event is None:
            logger.error(
                f"{rot.id=} has no event with {vehicle=} before {first_trip.departure_time.isoformat()}"
            )
            start_soc = 1
        else:
            start_soc = prev_event.soc_end

        vehicle_assigns.append({"rot": rot.id, "v_id": vehicle.to_simba_name(), "soc": start_soc})
    return vehicle_assigns


def create_optimizer_config(db_scenario: Scenario) -> simba.station_optimization.config:
    """Create a config for the SimBA optimizer.

    Currently the standard charging power and exclusion stations are passed.
    """
    conf = simba.optimizer_util.OptimizerConfig()
    exclusion_stations = {
        stat.to_simba_name()
        for stat in Station.objects.filter(scenario=db_scenario, is_electrifiable=False)
    }
    standard_charging_power = db_scenario.simba_options["cs_power_opps"]
    standard_opp_station = {
        "type": "opps",
        "n_charging_stations": None,
        "cs_power_opps": standard_charging_power,
    }
    conf.exclusion_stations = exclusion_stations
    conf.standard_opp_station = standard_opp_station
    conf.solver = "quick"
    return conf


def run_mode(
    mode: str,
    schedule: SimbaSchedule,
    scenario: "None | SimbaScenario",
    args: Namespace,
    db_scenario: Scenario,
) -> tuple[SimbaSchedule, "SimbaScenario"]:
    """Run an implemented mode.

    SimBA modes are not used to allow access to the optimizer config
    without the need of reading from a file.
    """
    assert mode in IMPLEMENTED_MODES
    if mode == "sim":
        new_scenario: "SimbaScenario" = schedule.run(args, mode="greedy")
        return schedule, new_scenario
    conf = create_optimizer_config(db_scenario)
    conf.remove_impossible_rotations = True
    if mode == "station_optimization_single_step":
        conf.early_return = True

    # For now the optimizer needs a directory, and also expects an
    # arg which is only set in this function args.results_directory
    simba.simulate.create_results_directory(args, 0)
    return simba.station_optimization.run_optimization(
        conf, sched=schedule, scen=scenario, args=args
    )


def run_simba(
    schedule: SimbaSchedule,
    args: Namespace,
    db_scenario: Scenario,
    mode: str,
    scenario: "None | SimbaScenario" = None,
) -> tuple[SimbaSchedule, "SimbaScenario"]:
    assert mode in IMPLEMENTED_MODES, f"{mode} is not implemented in simba"

    logger.info(f"S.ID:{db_scenario.id}:Running Simba {datetime.now()} with mode {mode}")
    # TODO don't overwrite output on multiple function calls
    args.output_directory = Path(settings.UPLOAD_PATH) / str(db_scenario.task_id)
    args.attach_vehicle_soc = True

    try:
        schedule, scenario = run_mode(mode, schedule, scenario, args, db_scenario)
    except AssertionError as e:
        logger.info(traceback.format_exception(e))
        # TODO: Let SimBA throw custom exceptions to be caught
        if any(
            ("Schedule cannot be optimized, since rotations cannot be electrified.") in x
            for x in e.args
        ):
            raise StationOpimizationImpossible("StationOptimization was impossible")
        logger.info("Assertion not found")
        raise
    # Apply changes to database depending on mode
    match mode:
        case "sim":
            pass
        case w if w in ["station_optimization", "station_optimization_single_step"]:
            update_electrified_stations_db(schedule.stations, db_scenario)
        case _:
            raise NotImplementedError

    logger.info(f"S.ID:{db_scenario.id}:Creating SimBA Events {datetime.now()}")
    create_event_output(scenario, db_scenario)
    logger.info(f"S.ID:{db_scenario.id}:SimBA Events Created {datetime.now()}")
    return schedule, scenario


def opportunity_rotation_to_eflips_input(
    db_rotation, db_scenario, input_for_eflips, rot_id, rotation, scenario, schedule
):
    input_for_eflips = copy(input_for_eflips)
    v_soc, start, end = simba.optimizer_util.get_rotation_soc(
        rot_id=rot_id, schedule=schedule, scenario=scenario
    )
    # Start is the first index during the rotation, with a decreased soc already, therefore
    # use the index before
    start_idx = max(start - 1, 0)
    rot_soc = v_soc[start_idx:end]
    vehicle_type_db = VehicleType.objects.get(
        scenario=db_scenario,
        name_short=rotation.vehicle_type,
        opportunity_charging_capable=True,
    )
    input_for_eflips[db_rotation.id] = dict(
        departure_soc=rot_soc[0],
        arrival_soc=rot_soc[-1],
        minimal_soc=min(rot_soc),
        charging_type=rotation.charging_type,
        vehicle_type=vehicle_type_db.id,
    )
    return input_for_eflips


def depot_rotation_to_eflips_input(db_rotation, db_scenario, input_for_eflips, rotation, schedule):
    input_for_eflips = copy(input_for_eflips)
    input_for_eflips[db_rotation.id].update(
        departure_soc=schedule.min_recharge_deps_depb,
        charging_type="depb",
    )
    vehicle_type_db = VehicleType.objects.get(
        scenario=db_scenario,
        name_short=rotation.vehicle_type,
        opportunity_charging_capable=(rotation.charging_type == "oppb"),
    )
    input_for_eflips[db_rotation.id]["vehicle_type"].append(vehicle_type_db.id)
    vehicle = schedule.vehicle_types[rotation.vehicle_type][rotation.charging_type]
    input_for_eflips[db_rotation.id]["delta_soc"].append(rotation.consumption / vehicle["capacity"])
    return input_for_eflips


def run_eflips(scenario, delete_existing_depot, progress) -> None:
    logger.info(f"S.ID:{scenario.id}:Running eFLIPS {datetime.now()}")
    # Constructing the database URL manually
    db_url = create_db_url()
    depot_configs = DepotConfigurationWish.objects.filter(scenario=scenario).prefetch_related(
        "areainformation_set"
    )
    eflips_configs = []
    for config in depot_configs:
        eflips_configs.append(config.to_dataclass())

    if not Depot.objects.filter(scenario=scenario).exists():
        progress.status = _("Optimiere das Depot Layout")
        progress.save()
        logger.info(f"S.ID:{scenario.id}:Eflips starts generating an optimal depot layout")
        generate_optimal_depot_layout(
            depot_config_wishes=eflips_configs,
            scenario=scenario,
            database_url=db_url,
            delete_existing_depot=delete_existing_depot,
        )
    else:
        logger.info(f"S.ID:{scenario.id}:Eflips is reusing the existing depot layout")
        #

    progress.status = _("Simuliere das Depot")
    progress.save()
    simulate_scenario(
        scenario,
        database_url=db_url,
        ignore_unstable_simulation=False,
        ignore_delayed_trips=False,
    )


def eflips_calculate_tco(scenario: Scenario) -> dict:
    """Complete the fleet topology of a simulated scenario and calculate its TCO.

    The fleet rows and their parameters were created before the simulation and
    carried onto this scenario by ``apply_tco_mutation``; ``ensure_fleet_topology``
    is only a fallback for scenarios that never went through the costs page. The
    charging point types can only be attached now, because the Areas and the
    opportunity-charging Events they are attached to are products of the simulation.

    :param scenario: A simulated scenario, after ``apply_depot_strategy``.
    :returns: ``{cost_category: EUR per revenue-km}`` for ``Scenario.tco_result``.
    """
    ensure_fleet_topology(scenario)
    ensure_lca_parameters(scenario)
    attach_charging_point_types(scenario)
    return calculate_tco(scenario)


def create_db_url():
    db_dict = settings.DATABASES["default"]
    engine = db_dict["ENGINE"].split(".")[-1]
    # sqlalchemy needs a translation of the engine
    if engine in ["postgres", "postgis"]:
        engine = "postgresql"
    db_url = (
        f"{engine}://{db_dict['USER']}:{db_dict['PASSWORD']}@{db_dict['HOST']}/{db_dict['NAME']}"
    )
    return db_url


def get_timestep(simba_scenario: "SimbaScenario", timestamp: datetime) -> int:
    """Returns time steps into the scenario for a given scenario and datetime"""
    # calculate the corresponding time step
    timedelta_into_scenario = timestamp - simba_scenario.start_time
    minutes_into_scenario = timedelta_into_scenario.total_seconds() / 60
    return round(minutes_into_scenario * (simba_scenario.stepsPerHour / 60))


def get_datetime(simba_scenario: "SimbaScenario", timestep: int) -> datetime:
    """Returns datetime for a given scenario and time steps into the scenario"""
    # calculate the corresponding datetime
    minutes = timestep * (60 / simba_scenario.stepsPerHour)
    return simba_scenario.start_time + timedelta(minutes=minutes)


def get_middlepoint(scenario: Scenario) -> tuple[float, float] | None:
    """
    Get the geometric middlepoint of a scenario or None if the scenario has no geo data.
    :param scenario: Scenario
    :return: lon, lat
    """
    try:
        middlepoint = (
            Station.objects.filter(scenario=scenario)
            .aggregate(center=Collect("geom"))["center"]
            .centroid
        )
    except AttributeError:
        return None
    return middlepoint


def is_consistent_rotation(rotation: Rotation) -> bool:
    trips = list(
        Trip.objects.filter(rotation=rotation).select_related("route").order_by("departure_time")
    )
    for trip in trips:
        if trip.arrival_time < trip.departure_time:
            logger.error(f"A trip must have a duration. {trip=}")
            return False

        if trip.route.distance is None or trip.route.distance < 0:
            logger.error(f"A route must have a postive distance. {trip=}")
            return False

    if trips[-1].route.arrival_station.charge_type != EnumChargeType.DEPOT:
        logger.error(f"A rotation ends at station which is not a depot. {rotation=}, {trips[-1]=}")
        return False

    if trips[0].route.departure_station != trips[-1].route.arrival_station:
        logger.error(
            f"A rotation does not end at its starting location. {rotation=}. {trips[0]=}, {trips[-1]=}"
        )
        return False

    if len(trips) < 2:
        return True
    trip = trips[0]
    for next_trip in trips[1:]:
        if trip.arrival_time > next_trip.departure_time:
            logger.error(
                f"A trip arrives after the departure of the next trip. {rotation=}, {trip=}, {next_trip=}"
            )
            return False
        trip = next_trip
    return True


def make_consistent(scenario: Scenario) -> None:
    for rotation in Rotation.objects.filter(scenario=scenario):
        message = (
            "Der Umlauf '{}' wurde gelöscht da er nicht die Anforderungen an Konsistenz erfüllt. "
            "Die Anfoderungen an Konsistenz sind in der Hilfe erläutert."
        )
        if not is_consistent_rotation(rotation):
            Notification.objects.create(
                scenario=scenario,
                level=EnumNotificationLevels.WARNING,
                notification_type=EnumNotificationType.DELETED_INCONSISTENT_ROTATION,
                message=(
                    message.format(
                        escape(rotation.name),
                    )
                )[:999],
            )
            rotation.delete()

    if VehicleType.objects.filter(scenario=scenario, consumption=None).count() > 0:
        q = Trip.objects.filter(scenario=scenario, loaded_mass=None)
        if q.count() > 0:
            q.update(loaded_mass=0)


def is_consistent(scenario: Scenario) -> bool:
    for rotation in Rotation.objects.filter(scenario=scenario):
        if not is_consistent_rotation(rotation):
            return False

    if Vehicle.objects.filter(scenario=scenario).exists():
        for rotation in Rotation.objects.filter(scenario=scenario).select_related(
            "vehicle_type", "vehicle__vehicle_type"
        ):
            if rotation.vehicle is not None:
                if not rotation.vehicle.vehicle_type == rotation.vehicle_type:
                    logger.error(f"Rotation has a vehicle of the wrong vehicle type. {rotation=}")
                    return False

    if VehicleType.objects.filter(scenario=scenario, consumption=None).count() > 0:
        if Trip.objects.filter(scenario=scenario, loaded_mass=None).count() > 0:
            logger.error("Scenario has trips without a loaded mass.")
            return False

    if VehicleType.objects.filter(scenario=scenario, consumption=None).count() > 0:
        if not Temperatures.objects.filter(scenario=scenario).count() == 1:
            logger.warning(
                "VehicleTypes have no constant consumption.\n"
                "This makes adding 'Temperatures' to the scenario mandatory.\n "
                "Use temperatures_to_db('ebustoolbox/static/ebustoolbox/"
                "examples/temperature_time_series.csv',django_scenario, True) "
                "to add a default temperature series. Default Temperature of "
                f"{DEFAULT_TEMPERATURE}°C will be used."
            )

    for vt in VehicleType.objects.filter(scenario=scenario):
        if vt.charging_curve is None:
            return False
        if vt.charging_curve[0][0] != 0:
            logger.error("Charging curve should start at SoC=0")
            return False
        if vt.charging_curve[-1][0] != 1:
            logger.error("Charging curve should ent at SoC=1")
            return False
    return True


def example_single_step_optimization(scenario: Scenario):
    """

    :param scenario: Scenario to be optimized
    :type scenario: ebustoolbox.models.Scenario
    :return: None
    """
    # Check that the scenario is consistent.
    assert is_consistent(scenario)
    schedule, simbascenario = run_simba_scenario(scenario, assign_vehicles=True)

    schedule, simbascenario = run_simba_scenario(
        scenario, simba_scenario=simbascenario, mode="station_optimization_single_step"
    )


def example_electrification_optimization(scenario: Scenario):
    """

    :param scenario: Scenario to be optimized
    :type scenario: ebustoolbox.models.Scenario
    :return: None
    """
    # Check that the scenario is consistent.
    assert is_consistent(scenario)
    schedule, simbascenario = run_simba_scenario(scenario, assign_vehicles=True)

    schedule, simbascenario = run_simba_scenario(
        scenario, simba_scenario=simbascenario, mode="station_optimization"
    )


def create_event_output(simba_scenario: "SimbaScenario", db_scenario) -> list[Event]:  # noqa: C901
    # collect data from DB
    # Delete old SimBA events

    (
        # Query the Event model for entries that meet the following criteria:
        Event.objects.filter(
            # The `scenario` field matches the provided `db_scenario` variable.
            scenario=db_scenario,
            # The `event_type` field matches one of the specified types:
            # - CHARGING_OPPORTUNITY
            # - DRIVING
            # - STANDBY_DEPARTURE
            # These are created by SimBA
            event_type__in=[
                EventType.CHARGING_OPPORTUNITY,
                EventType.DRIVING,
                EventType.STANDBY_DEPARTURE,
            ],
            # The `area` field must be NULL (or not set) because SimBA does not create events with area.
            area__isnull=True,
        )
        # SimBA STANDBY_DEPARTURE events have a Station.
        # Events without a station could come from eFLIPS and are not excluded
        .exclude(
            # The `event_type` is STANDBY_DEPARTURE AND the `station` field is NULL.
            event_type=EventType.STANDBY_DEPARTURE,
            station__isnull=True,
        )
        # Delete the remaining events that match the filter and exclude criteria.
        .delete()
    )

    vehicle_dict = Vehicle.objects.filter(scenario=db_scenario)
    vehicle_dict = {vehicle.to_simba_name(): vehicle for vehicle in vehicle_dict}
    vehicle_type_dict = VehicleType.objects.filter(scenario=db_scenario)
    vehicle_type_dict = {str(vehicle_type.id): vehicle_type for vehicle_type in vehicle_type_dict}

    vehicle_events = [e for e in simba_scenario.events.vehicle_events]

    # Departures and arrivals with the same vehicle_id and start time are ordered
    # "arrival" -> "departure".
    # this assumes there are no 0-duration trips but 0 duration stops
    vehicle_events = sorted(
        vehicle_events,
        key=lambda e: (
            e.vehicle_id,
            e.start_time,
            ["arrival", "departure"].index(e.event_type),
        ),
    )

    last_id = None
    counter = 0

    for i, e in enumerate(vehicle_events):
        if last_id != e.vehicle_id:
            counter = 0
            last_id = e.vehicle_id
        if not e.event_type == ["departure", "arrival"][counter % 2]:
            raise AssertionError(str(i), str(counter))
        counter += 1

    vehicle_trips_dict = dict()
    current_rotation = None
    events = []
    last_arrival_time = None
    current_vehicle = None
    last_aware = None
    for counter, vehicle_event in enumerate(vehicle_events):
        start_timestep = get_timestep(simba_scenario, vehicle_event.start_time)

        event_time = vehicle_event.start_time
        aware_start_time = make_aware(event_time) if not is_aware(event_time) else event_time

        try:
            if vehicle_events[counter + 1].vehicle_id == vehicle_event.vehicle_id:
                end_time = vehicle_events[counter + 1].start_time
            else:
                end_time = simba_scenario.stop_time
        except IndexError:
            end_time = simba_scenario.stop_time

        vehicle = vehicle_dict[vehicle_event.vehicle_id]
        if current_vehicle is not None:
            # current vehicle is set to None if a new rotation is reached.
            # during a rotation the vehicle must be the same
            assert (
                vehicle == current_vehicle
            ), f"{counter} {vehicle} , {last_aware}, {aware_start_time}"
        if vehicle_trips_dict.get(vehicle, None) is None:
            vehicle_trips_dict[vehicle] = Trip.objects.filter(
                rotation__vehicle=vehicle
            ).select_related("route__arrival_station", "rotation", "route__departure_station")
            vehicle_trips_arr = {
                t.arrival_time: (t, t.route.arrival_station, t.rotation)
                for t in vehicle_trips_dict[vehicle]
            }
            vehicle_trips_dep = {
                t.departure_time: (t, t.route.departure_station, t.rotation)
                for t in vehicle_trips_dict[vehicle]
            }

        # trips are sorted by time. all trips before the current rotation end time belong to the
        # same rotation
        if current_rotation is None:
            # first event must be a departure
            assert vehicle_event.event_type == "departure"
            current_rotation = vehicle_trips_dep.get(aware_start_time)[2]
            current_vehicle = vehicle
            last_arrival_time = None
            for arrival_time, value in vehicle_trips_arr.items():
                trip, arrival_station, rotation = value
                if rotation != current_rotation:
                    continue
                if last_arrival_time is None or arrival_time > last_arrival_time:
                    last_arrival_time = arrival_time

        if aware_start_time >= last_arrival_time:
            current_rotation = None
            current_vehicle = None
            # Do not save events passed their rotation time. This is done by eFLIPS
            continue
        else:
            last_aware = aware_start_time

        # Skip events with no duration
        if vehicle_event.start_time == end_time:
            continue

        end_timestep = min(get_timestep(simba_scenario, end_time), simba_scenario.step_i - 1)
        simba_vehicle_type = vehicle_event.vehicle_id.split("_")[0]
        vehicle_type = vehicle_type_dict[simba_vehicle_type]

        # figure out the location of the event
        station = None
        trip = None
        if not len(vehicle_trips_arr):
            raise RuntimeError(
                f"No trip assigned to vehicle {vehicle.to_simba_name()}/ID:{vehicle.id} found in database."
            )

        if vehicle_event.event_type == "arrival":
            station = vehicle_trips_arr.get(aware_start_time)[1]
            is_charging = vehicle_event.update["connected_charging_station"] is not None
            event_type = (
                EventType.CHARGING_OPPORTUNITY if is_charging else EventType.STANDBY_DEPARTURE
            )
        elif vehicle_event.event_type == "departure":
            trip = vehicle_trips_dep.get(aware_start_time)[0]
            event_type = EventType.DRIVING
        else:
            raise NotImplementedError("Unknown vehicle event type")
        timezone = aware_start_time.tzinfo
        timestamp_list = [
            get_datetime(simba_scenario, t).astimezone(timezone).isoformat()
            for t in range(start_timestep, end_timestep + 1, int(60 / simba_scenario.stepsPerHour))
        ]
        timeseries = {
            "time": timestamp_list,
            "soc": simba_scenario.vehicle_socs[vehicle.to_simba_name()][
                start_timestep : end_timestep + 1
            ],
        }
        if None in timeseries["soc"]:
            logger.warning(f"S.ID:{db_scenario.id}:None Values found in timeseries")
            forward_fill_last_value(timeseries["soc"])
        # grab current vehicle SoC at timestep
        soc_start = timeseries["soc"][0]
        soc_end = timeseries["soc"][-1]
        if None in timeseries["soc"]:
            raise Exception(
                f"{vehicle.to_simba_name()}/{vehicle.id} has None values in between socs"
            )
        event = Event(
            scenario=db_scenario,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            station=station,
            trip=trip,
            soc_start=soc_start,
            soc_end=soc_end,
            time_start=vehicle_event.start_time.astimezone(timezone),
            time_end=end_time.astimezone(timezone),
            timeseries=timeseries,
            event_type=event_type,
        )
        events.append(event)
    Event.objects.bulk_create(events)
    return events


def forward_fill_last_value(list_with_nones):
    """Forward fill the last non None value

    :param list_with_nones: List containing nones at the end
    :return: list without None values at the end
    """
    for idx in range(len(list_with_nones) - 1, -1, -1):
        last_soc = list_with_nones[idx]
        last_idx = idx
        if last_soc is not None:
            break
    else:
        raise Exception("Timeseries has only None values as soc")
    list_with_nones[last_idx:] = [last_soc for _ in range(last_idx, len(list_with_nones))]


def electrify_db_stations(scenario: Scenario, station_id_list, unelectrify=True):
    """Set given stations in scenario to be electrified."""
    all_stations = Station.objects.filter(scenario=scenario)
    stations = all_stations.filter(pk__in=station_id_list).exclude(charge_type=EnumChargeType.DEPOT)
    for station in stations:
        station.is_electrified = True
        # TODO get these values from somewhere?
        station.charge_type = EnumChargeType.OPPORTUNITY
        station.voltage_level = EnumVoltageLevel.VOLTAGE_MV
        station.amount_charging_places = scenario.simba_options["amount_charging_places"]
    Station.objects.bulk_update(
        stations,
        ["is_electrified", "charge_type", "voltage_level", "amount_charging_places"],
    )
    if unelectrify:
        revert_stations = (
            all_stations.exclude(pk__in=station_id_list)
            .filter(is_electrified=True)
            .exclude(charge_type=EnumChargeType.DEPOT)
        )
        for station in revert_stations:
            station.is_electrified = False
            station.charge_type = None
            station.voltage_level = None
            station.amount_charging_places = None
        Station.objects.bulk_update(
            revert_stations,
            [
                "is_electrified",
                "charge_type",
                "voltage_level",
                "amount_charging_places",
            ],
        )


def unelectrify_station(station: Station) -> Station:
    """Return a station with all attributes of electrification turned of / set to None"""
    station.is_electrified = False
    station.charge_type = None
    station.voltage_level = None
    station.amount_charging_places = None
    station.power_per_charger = None
    station.power_total = None
    return station


@atomic()
def update_stations_and_exclusion(
    station_forms: List[forms.StationForm], default_charge_power_per_station: float
):
    stations = []
    for form in station_forms:
        if not form.cleaned_data["is_electrified"]:
            station: Station = form.instance
            station = unelectrify_station(station)
        else:
            # Electrification needs further attributes
            station = form.save(commit=False)
            station.power_per_charger = (
                station.power_per_charger or default_charge_power_per_station
            )
            # TODO: Do we want more logic or user interaction with this?
            if station.amount_charging_places is not None:
                station.power_total = station.power_per_charger * station.amount_charging_places
            else:
                # If the station might have unlimited charging places,
                # we dont want the grid_connection/ power_total to restrict power
                # TODO: Discuss
                station.power_total = float("inf")
            station.charge_type = EnumChargeType.OPPORTUNITY
            station.voltage_level = EnumVoltageLevel.VOLTAGE_MV
            station.is_valid()
        stations.append(station)
    Station.objects.bulk_update(
        stations,
        fields=forms.StationForm._meta.fields
        + ["charge_type", "voltage_level", "power_per_charger", "power_total"],
    )


def update_vehicle_types_with_defaults(vehicle_type_pairs, task_id, vt_adjustments):
    """Update info of a VehicleType with a paired VehicleType from DefaultScenario"""
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_types_db = VehicleType.objects.filter(scenario=scenario)
    default_scenario = DefaultScenario.objects.first().scenario
    vehicle_types_default = VehicleType.objects.filter(scenario=default_scenario)
    for vehicle_type_pair in vehicle_type_pairs:
        vt = vehicle_types_db.get(pk=vehicle_type_pair[0])
        vt_default = vehicle_types_default.get(pk=vehicle_type_pair[1])
        vt_default.scenario = scenario
        if vt_adjustments[vt_default.id].get("battery_capacity"):
            vt_default.battery_capacity = vt_adjustments[vt_default.id]["battery_capacity"]
        vt_default.pk = vehicle_type_pair[0]
        # Do not overwrite this, since both capabilties might be needed
        assert vt_default.opportunity_charging_capable == vt.opportunity_charging_capable
        vt_default.name = vt.name
        vt_default.name_short = vt.name_short
        vt_default.save()


def get_distinct_arrival_station(trips: QuerySet[Trip]) -> QuerySet[Station]:
    station_ids = trips.values_list("route__arrival_station_id", flat=True).distinct()
    station_query = Station.objects.filter(id__in=station_ids)
    return station_query


def annotate_trips_with_standing_time(parent_trips: QuerySet[Trip]) -> QuerySet[Trip]:
    # Annotate trips with their nextr trip departure time to calculate break duration
    # Lead gives the next item of the ordered list by departure time, eg. next trip
    # but only for trips which share the same rotation / vehicle
    parent_trips = parent_trips.annotate(
        next_trip_departure=Window(
            expression=Lead("departure_time"),
            partition_by=[F("rotation")],
            order_by=F("departure_time").asc(),
        )
    )
    parent_trips = parent_trips.annotate(standing_time=F("next_trip_departure") - F("arrival_time"))
    return parent_trips


def annotate_stations_with_lines(station_query):
    annotated_query = station_query.annotate(
        lines_departure=ArrayAgg("route_departure_set__line__name", distinct=True)
    ).annotate(lines_arrival=ArrayAgg("route_arrival_set__line__name", distinct=True))
    return annotated_query


def annotate_vehicletypes_with_lines(vt_query):
    annotated_query = vt_query.annotate(
        lines=ArrayAgg("rotation__trip__route__line__name", distinct=True)
    )
    return annotated_query


def find_and_make_depots(scenario):
    depot_stations = set()
    for r in Rotation.objects.filter(scenario=scenario).prefetch_related("trip_set"):
        trips = r.trip_set.order_by("departure_time")
        depot_stations.add(trips.first().route.departure_station)
        arrival_sorted = sorted(trips, key=lambda x: x.arrival_time)
        depot_stations.add(arrival_sorted[-1].route.arrival_station)

    logger.info(f"S.ID:{scenario.id}:{len(depot_stations)} Depot Stations found")

    for station in depot_stations:
        station.is_electrified = True
        station.charge_type = EnumChargeType.DEPOT.value
        station.voltage_level = EnumVoltageLevel.VOLTAGE_MV.value
        station.save()


def trim_depots(scenario, depot_ids: list[int]):
    rot_before_count = Rotation.objects.filter(scenario=scenario).count()
    trip_before_count = Trip.objects.filter(scenario=scenario).count()
    route_before_count = Route.objects.filter(scenario=scenario).count()
    station_before_count = Station.objects.filter(scenario=scenario).count()
    vehicle_before_count = Vehicle.objects.filter(scenario=scenario).count()
    for dep_id in depot_ids:
        station = Station.objects.filter(
            id=dep_id, scenario=scenario, charge_type=EnumChargeType.DEPOT
        )
        if station.exists():
            station = station.first()
            logger.info(f"S.ID:{scenario.id}:Deleting station {station.name}")
            Rotation.objects.filter(
                scenario=scenario, trip__route__arrival_station=station
            ).delete()
            Rotation.objects.filter(
                scenario=scenario, trip__route__departure_station=station
            ).delete()
            station.delete()
        else:
            logger.info(f"S.ID:{scenario.id}:Station with id {dep_id} not found in scenario")
    (
        Station.objects.filter(scenario=scenario)
        .annotate(departure_count=Count("route_departure_set__trip"))
        .annotate(arrival_count=Count("route_arrival_set__trip"))
        .filter(departure_count=0, arrival_count=0)
        .delete()
    )
    (Route.objects.filter(scenario=scenario).annotate(count=Count("trip")).filter(count=0).delete())
    Line.objects.filter(scenario=scenario, route__isnull=True).delete()
    VehicleType.objects.filter(scenario=scenario, rotation__isnull=True).delete()
    logger.info(
        f"S.ID:{scenario.id}:Before -> After trimming\n"
        f"rotations:{rot_before_count} -> {Rotation.objects.filter(scenario=scenario).count()}\n"
        f"trips: {trip_before_count} ->{Trip.objects.filter(scenario=scenario).count()}\n"
        f"routes: {route_before_count} ->{Route.objects.filter(scenario=scenario).count()}\n"
        f"stations: {station_before_count} ->{Station.objects.filter(scenario=scenario).count()}\n"
        f"vehicles: {vehicle_before_count} ->{Vehicle.objects.filter(scenario=scenario).count()}\n"
    )


class ScheduleStationMerger:
    @staticmethod
    def get_problematic_routes(scenario) -> QuerySet[Route]:
        # Routes with less than 0 distance
        route_ids = Route.objects.filter(scenario=scenario, distance__lte=0).values_list(
            "id", flat=True
        )
        # Trips with less than zero duration
        min_duration = timedelta(minutes=0)
        trip_route_ids = (
            Trip.objects.filter(scenario=scenario)
            .annotate(duration=F("arrival_time") - F("departure_time"))
            .filter(duration__lte=min_duration)
            .select_related("route")
            .values_list("route_id", flat=True)
        )

        routes_to_change = set(route_ids).union(set(trip_route_ids))
        routes = (
            Route.objects.filter(id__in=routes_to_change)
            .prefetch_related("trip_set")
            .select_related("departure_station", "arrival_station")
        )
        return routes

    @staticmethod
    def get_rotations_trips(rotation: Rotation, rotation_trip_dict):
        # get a dictionary of the next and prev trip for all trips of a rotation.
        # The first key is the trip.id
        trip_dict = rotation_trip_dict.get(rotation)
        if trip_dict is None:
            trips = list(
                Trip.objects.filter(rotation=rotation)
                .order_by("departure_time")
                .select_related("route")
            )
            assert len(trips) > 1, "A rotation must have at least two trips"
            prev_trip = trips[0]
            trip_dict = {prev_trip.id: {"prev": None}}
            for _trip in trips[1:]:
                trip_dict[prev_trip.id]["next"] = _trip
                trip_dict[_trip.id] = {"prev": prev_trip}
                prev_trip = _trip
        rotation_trip_dict[rotation] = trip_dict

    @classmethod
    def expand_next_trips(cls, next_trip, merge_stations, delete_trips, rotation_trips):
        distance = 0
        while cls.is_problematic(next_trip):
            distance += next_trip.route.distance
            merge_stations.union(
                [next_trip.route.arrival_station, next_trip.route.departure_station]
            )
            # Mark for deletion
            delete_trips.add(next_trip.id)
            next_trip = rotation_trips.get(next_trip.id, {}).get("next")
            assert (
                next_trip is not None
            ), "The last trip of a rotation cannot be a 0 distance/duration trip"
        return distance, next_trip

    @classmethod
    def expand_prev_trips(cls, prev_trip, merge_stations, delete_trips, rotation_trips):
        distance = 0
        while cls.is_problematic(prev_trip):
            distance += prev_trip.route.distance
            merge_stations.union(
                [prev_trip.route.arrival_station, prev_trip.route.departure_station]
            )
            # Mark for deletion
            delete_trips.add(prev_trip.id)
            prev_trip = rotation_trips.get(prev_trip.id, {}).get("prev")
            assert (
                prev_trip is not None
            ), "The first trip of a rotation cannot be a 0 distance/duration trip"
        return distance, prev_trip

    @staticmethod
    def is_problematic(trip: Trip) -> bool:
        if trip.arrival_time - trip.departure_time <= timedelta(minutes=0):
            return True
        if trip.route.distance == 0:
            return True
        return False

    @staticmethod
    def fix_next_trip(trip, station, route_id) -> None:
        route: Route = trip.route
        route.id = route_id
        route.departure_station = station
        route.name = f"Fixed zero duration/distance route {route.departure_station.name} - {route.arrival_station.name}"
        trip.route = route
        trip.route_id = route_id

    @classmethod
    @atomic()
    def transform_zero_duration_trips(cls, source_scenario: Scenario) -> None:
        """
        Merge routes and trips with zero duration or distance

        Trips need to have a duration and a distance. If this is not the case this function merges
        stations when this occurs. The routes and trips with no duration/distanced are rerouted to this
        station. The number of trips and routes will be reduced.
        Routes which are generated and use these new stations are not shared across trips.
        With bad data, cases my arise where stations are merged since trips/routes connect them
        with zero duration/distance, while at the same time other routes using these merged stations
        contain distance and duration. This is not handled specifically as edge case of already bad
        data.

        :param source_scenario: Source scenario
        :param child: Child scenario which is notified about changes
        """

        rotation_trip_dict = dict()
        route_id = ebustoolbox.util.get_next_id(Route)

        # Merge all routes. This is done by creating new routes. change stations and trips accordingly
        new_stations = dict()
        created_stations = set()
        changed_trips = []
        new_routes = []
        delete_trips: set[int] = set()

        routes = cls.get_problematic_routes(source_scenario)
        # Merge trips and routes with all successive zero duration/distance trips.
        # The emerging stations are used for all routes which arrive
        # or depart from one of these multi-stations.
        child = None
        if source_scenario.scenario_set.count() == 1:
            child = source_scenario.scenario_set.first()
        for route in routes:
            route: Route
            for trip in route.trip_set.all():
                trip: Trip
                # Trip is already marked to be deleted. Skip it
                if trip.id in delete_trips:
                    continue
                logger.info(
                    f"S.ID:{source_scenario.id}:Handling problematic trip "
                    f"{trip} in source scenario for child {child}"
                )
                delete_trips.add(trip.id)
                cls.get_rotations_trips(trip.rotation, rotation_trip_dict)
                rotation_trips = rotation_trip_dict[trip.rotation]
                assert cls.is_problematic(trip)
                merge_stations = set([trip.route.arrival_station, trip.route.departure_station])
                prev_trip: Trip = rotation_trips.get(trip.id, {}).get("prev")
                assert (
                    prev_trip is not None
                ), "The first trip of a rotation cannot be a 0 distance/duration trip"

                # This will be added to the new trip and route
                problematic_distance = trip.route.distance

                # expand the selection of problematic trips/routes
                # until an non problematic trip is found
                distance, prev_trip = cls.expand_prev_trips(
                    prev_trip, merge_stations, delete_trips, rotation_trips
                )

                # NOTE: When fetching trip data with select_related("route")
                # multiple trip objects may share the same in memory route.
                # To make sure only this trip specific route instance is mutated
                # a in memory copy is created
                prev_trip.route = copy_model_instance(prev_trip.route)
                problematic_distance += distance

                next_trip: Trip = rotation_trips.get(trip.id, {}).get("next")
                # Zero distance/duration trips are merged with the next trip.
                # Therefor the last trip must have distance and duration
                assert (
                    next_trip is not None
                ), "The last trip of a rotation cannot be a 0 distance/duration trip"
                distance, next_trip = cls.expand_next_trips(
                    next_trip, merge_stations, delete_trips, rotation_trips
                )

                # Same logic for copying as in the previous copy_model_instance call
                next_trip.route = copy_model_instance(next_trip.route)
                problematic_distance += distance
                # Create a station or find a station with a common station
                station = None
                for search_station in merge_stations:
                    if search_station in new_stations:
                        station = new_stations[search_station]
                        break
                else:
                    # Saving stations as single calls is not very performant,
                    # but it allows for directly accessing the id.
                    # Since only few stations should be created, performance shouldn't be a problem
                    station = Station(scenario=source_scenario, name="Zusammengelegte Station: ")
                    station.save()
                    created_stations.add(station)
                    # Make this station reusable for all other trips which connect with this station
                    for search_station in merge_stations:
                        new_stations[search_station] = station
                # at this point the next and previous trip should be trips with non zero distance
                # and duration
                new_route = prev_trip.route

                new_route.id = route_id
                route_id += 1
                # In case this is a trip without duration and a route with some distance
                new_route.distance += problematic_distance
                new_route.name = (
                    "Fixed zero duration/distance route "
                    f"{new_route.departure_station.name} - {new_route.arrival_station}"
                )
                # NOTE: the route also has an attribute called stations,
                # which describes the path of a route. This is column is skipped since its optional,
                # and this kind of faulty data is more likely to occur with SimBA schedule data,
                # which does not pass station data
                # the new route of the previous trip ends at the merged station
                new_route.arrival_station = station
                # Store the new route and changed trip to update it after the route is created
                new_routes.append(new_route)
                prev_trip.route_id = new_route.id
                # NOTE: The trip duration is NOT changed. Adding trips with zero driving duration
                # would not change the driving time. Driving durations for routes with 0 distance
                # are ignored. this means possible standing times of 0 duration/distance trips
                # occur right after the first previous trip with duration and distance.
                changed_trips.append(prev_trip)
                cls.fix_next_trip(next_trip, station, route_id)
                assert next_trip.route not in new_routes
                route_id += 1
                new_routes.append(next_trip.route)

                # Store this changed trip to update it after the route is created
                changed_trips.append(next_trip)

        # The algorithm created some stations which are shared across routes.
        # The name should reflect stations they were merged from
        for original_station, new_station in new_stations.items():
            new_station.name += f"{original_station.name} "
        logger.info(
            "Creating new merged stations "
            f"{Station.objects.bulk_update(created_stations, fields=['name'])}"
        )

        # Reverse the lookup
        reversed_station = dict()
        for original_station, new_station in new_stations.items():
            if reversed_station.get(new_station) is None:
                reversed_station[new_station] = set()
            reversed_station[new_station].add(original_station)

        message = (
            "Die Station '{}' wurde automatisch generiert. "
            "Grund hierfür ist, dass folgende Stationen über Fahrten ohne Fahrtzeit "
            "oder ohne Distanz verknüpft sind:{}."
        )

        for new_station, original_stations in reversed_station.items():
            Notification.objects.create(
                scenario=source_scenario,
                level=EnumNotificationLevels.WARNING,
                notification_type=EnumNotificationType.MERGED_STATIONS_FOR_INCONSISTENT_TRIPS,
                message=(
                    message.format(
                        escape(new_station.name),
                        escape(", ".join([s.name for s in original_stations])),
                    )
                )[:999],
            )

        # After the stations were created we can change the routes
        logger.info(f"Creating new {len(new_routes)} Routes with merged stations")
        new_routes_ids = [x.id for x in new_routes]
        new_routes = Route.objects.bulk_create(new_routes)
        route_lookup = {old_id: x.id for old_id, x in zip(new_routes_ids, new_routes)}
        for original_station, new_station in new_stations.items():
            # other routes hitting this station should use the merged station too
            routes = Route.objects.filter(arrival_station=original_station).update(
                arrival_station=new_station
            )
            routes = Route.objects.filter(departure_station=original_station).update(
                departure_station=new_station
            )
        # The trips had placeholder route ids. Replace them with the ids returned from the db
        for t in changed_trips:
            t.route_id = route_lookup[t.route_id]
        logger.info(
            f"Updating trips {(Trip.objects.bulk_update(changed_trips, fields=['route_id']))}"
        )
        logger.info(
            f"Deleting zero distance/duration trips {(Trip.objects.filter(id__in=delete_trips).delete())}"
        )

        # Filter for routes which do not have a trip anymore and delete them.
        # Scoped to source_scenario: a global query would race concurrent
        # imports of other scenarios, whose routes briefly have no trips
        # between bulk_create(Route) and bulk_create(Trip).
        logger.info(
            "Deleting orphaned routes without trips "
            f"{(Route.objects.filter(scenario=source_scenario, trip__isnull=True).delete())}"
        )

        deleted_stations = str(
            Station.objects.filter(scenario=source_scenario)
            .annotate(departure_count=Count("route_departure_set__trip"))
            .annotate(arrival_count=Count("route_arrival_set__trip"))
            .filter(departure_count=0, arrival_count=0)
            .delete()
        )

        logger.info(f"Deleting orphaned Stations without trips {deleted_stations}")

        if cls.get_problematic_routes(source_scenario).count() > 0:
            logger.error(
                "Removing zero duration or distance trips did not work for all trips/routes"
            )


@atomic()
def transform_depot_stations(source_scenario: Scenario) -> None:
    """
    Duplicate depot stations and transform them into opportunity stations where necessary;

    WeBus only supports Blocks which start and end at depot station without intermediate stops
    at depots.
    This function determines if there are intermediate stops at depot stations,
    creates an opportunity station and switches this station into the appropriate routes.
    :param source_scenario: Source scenario
    """
    depots = Station.objects.filter(scenario=source_scenario, charge_type=EnumChargeType.DEPOT)
    all_routes = Route.objects.filter(scenario=source_scenario)
    depot_arrival_routes = all_routes.filter(arrival_station__in=depots)
    depot_departure_routes = all_routes.filter(departure_station__in=depots)
    # Only the first and last trip of a block should departe/arrive in a depot station.
    # The other trips should refrence routes which go to a newly generated opportunity station,
    # instead of the depot station.tasks
    # This query expects a outer ref to a rotation and returns the ordered trips by arrival time
    # with the last arrival first
    last_trip_subquery = Trip.objects.filter(rotation=OuterRef("pk")).order_by("-arrival_time")

    # Get the ids of each rotations last trip
    last_trip_ids = list(
        Rotation.objects.filter(scenario=source_scenario)
        .annotate(last_trip_id=Subquery(last_trip_subquery.values("id")[:1]))
        .values_list("last_trip_id", flat=True)
    )

    first_trip_subquery = Trip.objects.filter(rotation=OuterRef("pk")).order_by("arrival_time")
    # Get the ids of each rotations first trip
    first_trip_ids = list(
        Rotation.objects.filter(scenario=source_scenario)
        .annotate(first_trip_id=Subquery(first_trip_subquery.values("id")[:1]))
        .values_list("first_trip_id", flat=True)
    )
    relevant_trips = Trip.objects.filter(scenario=source_scenario)
    trip_dict = {t.id: t for t in relevant_trips}
    new_stations = dict()
    changed_rotations = dict()

    # NOTE: We make use of the lazy nature of queries. depot_departure_routes is evaluated after
    # the arrival_routes were created

    for depot_routes, allowed_depot_trips, station_type in zip(
        [depot_arrival_routes, depot_departure_routes],
        [last_trip_ids, first_trip_ids],
        ["arrival_station", "departure_station"],
    ):

        # This is used to differentiate between existing routes and newly created ones
        max_route_id = ebustoolbox.util.get_next_id(Route)
        new_routes = []
        changed_routes = []
        changed_trips = []

        for route in depot_routes:
            trips_of_route = set(route.trip_set.values_list("id", flat=True))
            intermediate_trips = trips_of_route.difference(set(allowed_depot_trips))
            if not intermediate_trips:
                # No intermediate trips were found with this route.
                continue
            logger.debug(f"{intermediate_trips} were found which end in depots")
            # at least 1 trip was found which is not the last trip, which ends in a depot station

            # All trips of this route are intermediate trip.
            # This means no new route has to be created but instead the route can be changed
            if len(trips_of_route) == len(intermediate_trips):
                # new route has a pk in this case
                new_route = route
                changed_routes.append(new_route)
            else:
                # Some trips need to keep a reference to the route ending in a depot.
                # The intermediate trips need a new route
                # Copy the route
                # We dont set a id/pk.
                # This way the db will set it and there are no issues with concurreny
                route.id = None
                new_route = route
                new_routes.append(new_route)
            new_station = new_stations.get(getattr(route, station_type))
            if not new_station:
                old_station = getattr(route, station_type)
                # Create a new station which has electrification defaults
                new_station = Station.objects.create(
                    name=old_station.name,
                    name_short=old_station.name_short,
                    geom=old_station.geom,
                    scenario=old_station.scenario,
                )
                new_stations[old_station] = new_station
            setattr(new_route, station_type, new_station)
            for t_id in intermediate_trips:
                t = trip_dict[t_id]
                t: Trip
                if changed_rotations.get(t.rotation) is None:
                    changed_rotations[t.rotation] = set()
                changed_rotations[t.rotation].add(new_station)
                # in case of a new route without a pk we set a placeholder
                # this is replaced later using a lookup between placeholder and actual pks
                t.route_id = new_route.id or len(new_routes) - 1 + max_route_id
                changed_trips.append(t)
        if changed_trips or changed_routes or new_routes:
            logger.info(
                "Schedule was transformed to remove intermediate depot trips.\n"
                f"{changed_trips=}\n{changed_routes=}\n{new_routes=}"
            )

        Route.objects.bulk_update(changed_routes, fields=["arrival_station", "departure_station"])
        # the returned routes have pk
        new_routes = Route.objects.bulk_create(new_routes)

        # create a lookup for the pks to
        pk_lut = {i + max_route_id: new_route.pk for i, new_route in enumerate(new_routes)}
        for t in changed_trips:
            if t.route_id >= max_route_id:
                t.route_id = pk_lut[t.route_id]
        Trip.objects.bulk_update(changed_trips, fields=["route"])

    for rotation, stations in changed_rotations.items():
        Notification.objects.create(
            scenario=source_scenario,
            level=EnumNotificationLevels.WARNING,
            notification_type=EnumNotificationType.INTERMEDIATE_DEPOT_STOPS_TRANSFORMED,
            message=(
                f"Für den Umlauf {escape(rotation.name)} wurden Zwischenhaltestellen "
                f"an den Depots {[escape(s.name) for s in stations]} erzeugt. "
                "Mehr Informationen finden Sie in der Hilfe."
            )[:999],
        )
    if len(changed_rotations) > 0:
        logger.warning(
            f"{changed_rotations.keys()} were transformed so they dont have intermediate stops at depot stations"
        )


@atomic()
def consolidate_socs(scenario: Scenario) -> None:
    """Align socs of consecutive events for drive and depot events

    Iterate over depot events and remove gaps in between driving/simba and depot events
    Depot charge events keep their end soc, since this is used for simba vehicle initialization.
    Log warnings for unexpected gaps.
    Bad Event input can lead to negative socs after this consolidation.
    This is only possible if previous assumptions are not met.
    Assumptions:
    eflips dispositions vehicles to meet block consumption criteria
    and applies this consumption to depot events after this block.
    In this case the consolidation should only shift the soc by the added charge during opportunity
    charging due to higher charging powers at lower socs.
    (Assumption monotonic sinking charging curves over soc)
    This shift would be positive and would not create negative socs.
    """
    logger.info(50 * "#" + "\n Consolidation")
    EPS = 0.005
    events = list(Event.objects.filter(scenario=scenario).order_by("vehicle", "time_start"))

    # the first event type from eflips could be one of many
    depot_event_types = [
        EventType.CHARGING_DEPOT,
        EventType.STANDBY_DEPARTURE,
        EventType.PRECONDITIONING,
        EventType.SERVICE,
        EventType.STANDBY,
    ]
    # The last event type of simba is always a driving event
    driving_event_types = [EventType.DRIVING]
    if not events:
        logger.warning(
            f"Scenario {scenario.task_id} could not be consolidated, since it has no events"
        )
        return
    vehicle = None
    prev_event = None

    running_delta_soc = 0
    summed_difference = {}
    for i, event in enumerate(events):
        assert event.vehicle is not None, "Events must have a vehicle"
        # New vehicle detected. First event is used to initialize values
        if event.vehicle != vehicle:
            vehicle = event.vehicle
            pre_fix_end_soc = event.soc_end
            summed_difference[vehicle] = 0
            continue
        prev_event = events[i - 1]
        assert isinstance(prev_event, Event)
        assert prev_event.time_end == event.time_start

        # This is the delta which exists between the current and the previous event
        # Generally we expect this value to be negative or 0.
        # Negative values mean the charge was increased in previous events
        pre_fix_delta = event.soc_start - pre_fix_end_soc
        summed_difference[vehicle] += abs(pre_fix_delta)
        # This is the delta which has to be applied to the current event
        # The deltas differs since, the prev_event.soc might have been changed during consolidation
        running_delta_soc = event.soc_start - prev_event.soc_end

        pre_fix_end_soc = event.soc_end

        # The delta soc which has to be applied to the current event
        # This does not reflect the delta_soc between both events before consolidation
        running_delta_soc = event.soc_start - prev_event.soc_end

        if running_delta_soc == 0:
            continue

        next_event = (
            events[i + 1]
            if (i + 1 > len(events) and events[i + 1].vehicle == vehicle)
            else "No next Event"
        )
        create_consolidate_log(event, next_event, prev_event, running_delta_soc, pre_fix_delta)
        # NOTE: Small deltas of SOC might occur anywhere
        # Bigger delta_socs 'should' only happen at the interface DRIVING - DEPOT
        # or also DEPOT - DRIVING

        # Example: Simba calculated Socs during drive for two rotations
        # R1: 1, 0.4, 0.6, 0.5         R2: 1.0, 0.4, 0.6, 0.5            R3: 1.0, 0.9, 0.8, 0.7
        # eflips gives them the same vehicle and assumes "constant" energy curve
        # R1: 1, 0.4, 0.6, 0.5 D1 0.9  R2: 0.9, 0.3, 0.5, 0.4   D2 0.45  R3: .45, 0.35, 0.25, 0.15
        # In the following simba simulation the energy consumption but especially the added charge are
        # recalculated. These lead to added charge
        # R1: 1, 0.4, 0.6, 0.5 D1 0.9  R2: 0.9, 0.3, 0.58, 0.48 D2 0.45  R3: 0.48, 0.38, 0.28, 0.18
        # while previously the d2 event had a start soc of 0.4 it now diverges from the previous events
        # end soc which is now 0.48
        # in some cases this soc lift during the previous rotation can lead to another gap
        # in the following rotation R3 the soc from the first event diverges from the previous depot
        # event (0.45 end soc vs 0.48 end soc with an implied even higher start soc)
        # this is due to how simba/SpiceEV used the instructions from eflips.
        # internally in SpiceEV the rotations are simulated sequentially with the start socs of the rotations
        # being defined as the target socs of the previous charge. In this case the simulation would
        # have to reduce the soc from 0.48 -> entering the depot to 0.45 (eflips calculated start value)
        # SpiceEV handles this by simply not adding charge, but will not reduce it
        if abs(pre_fix_delta) > EPS:
            if not (
                (
                    prev_event.event_type in driving_event_types
                    and event.event_type in depot_event_types
                )
                or (
                    prev_event.event_type in depot_event_types
                    and event.event_type in driving_event_types
                )
            ):
                raise AssertionError(
                    f"Big SoC Jump not at interface of SimBA/eFlips {prev_event=}, {event=}"
                )

        # NOTE: Charging depot events are only aligned at their start value.
        # This makes the timeseries not usable, therefor they are deleted
        # The timeseries can be recreated by SimBAs depot strategy
        if event.event_type == EventType.CHARGING_DEPOT:
            event.soc_start = prev_event.soc_end
            if event.timeseries and event.timeseries.get("soc"):
                event.timeseries["soc"] = None
            continue

        event.soc_start = prev_event.soc_end
        event.soc_end -= running_delta_soc
        if event.timeseries and event.timeseries["soc"]:
            ts = event.timeseries["soc"]
            shifted_ts = [v - running_delta_soc for v in ts]
            min_shifted_ts = min(shifted_ts)
            if min_shifted_ts < 0 and min(ts) >= 0:
                logger.warning(
                    f"Consolidation lead to negative SOCs ({min_shifted_ts:.3e}) which did not exist before. {event=}"
                )
            max_shifted_ts = max(shifted_ts)
            if max_shifted_ts > 1:
                log = logger.warning if max_shifted_ts > 1 + EPS else logger.debug
                log(
                    f"Consolidation lead to SOCs above 1 ({max_shifted_ts:.3e}). This should never happen. {event=}"
                )
            event.timeseries["soc"] = shifted_ts

        if not math.isclose(event.soc_start, prev_event.soc_end):
            raise AssertionError(f"Events dont align after consolidation {event=} , {prev_event=}")

    logger.info(
        50 * "#" + "\nDuring consolidation summed abs(soc_shift) per vehicle did not exceed "
        f"{max(summed_difference.values()):.3e}"
    )
    logger.debug(summed_difference)
    Event.objects.fast_update(events, fields=["soc_end", "soc_start", "timeseries"])


def create_consolidate_log(
    event: Event,
    next_event: Event | str,
    prev_event: Event,
    running_delta_soc: float,
    pre_fix_delta: float,
) -> None:
    """Create a log depending on severity of delta soc"""

    if abs(pre_fix_delta) >= 0.1:
        logger.warning(
            f"Unexpected high soc delta {pre_fix_delta:.2e} during consolidation.\n"
            f"{event=}\n{prev_event=}\n{next_event=}."
        )
    elif abs(pre_fix_delta) >= 0.01:
        logger.info(
            f"Socs differed by: {pre_fix_delta=:.2e}. {running_delta_soc=:.2e} is applied"
            f"\n{prev_event.id} and {event.id}"
        )
    elif pre_fix_delta != 0:
        logger.debug(
            f"Socs differed by: {pre_fix_delta=:.2e}. {running_delta_soc=:.2e} is applied"
            f"\n{prev_event.id} and {event.id}"
        )

    # NOTE: A following event gets its soc reduced.
    # This can happen if the previous depot charge diverged from eflips/simba
    # e.g. the first simba run started with soc 1 since each rotation gets its own vehicle
    # eflips assigned a vehicle which was previously in use and charged from .8 to 0.95 and started the rotation
    # SimBA/SpiceEv will use the same assignment and try to reach 0.95 in the depot. if it does not succeed
    # all following socs will drop by this amount
    # this is not ideal since these difference can add up over time.
    if pre_fix_delta > EPS:
        logger.warning(
            f"Unexpected soc drop {running_delta_soc=:.2e} and {pre_fix_delta=:.2e} due to consolidation.\n"
            f"{prev_event=}\n{event=}\n{next_event=}."
        )
    elif pre_fix_delta > 0:
        logger.debug(
            f"Unexpected soc drop {running_delta_soc=:.2e} due to consolidation.\n"
            f"{prev_event=}\n{event=}\n{next_event=}."
        )


def create_negative_block_notifications(scenario: Scenario) -> None:
    events = Event.objects.filter(
        scenario=scenario, event_type=EventType.DRIVING, soc_end__lt=0
    ).select_related("trip__rotation")
    if not events.exists():
        return
    low_soc_blocks = {str(event.trip.rotation.name) for event in events}
    Notification.objects.create(
        scenario=scenario,
        sender="SimBA-Optimizier from tasks.py",
        level=EnumNotificationLevels.INFO,
        notification_type=EnumNotificationType.LOW_SOC_BLOCKS,
        message=_(
            (
                "Die Stationsoptimierung konnte nicht alle Umläufe elektrifzieren. "
                f"Folgende {len(low_soc_blocks)} Umläufe haben auch nach der Optimierung einen SOC unter 0%: "
                + ", ".join(low_soc_blocks)
            )[:999]
        ),
    )


def delete_scenario(scenario: Scenario):
    """Delete the scenario and appropriate relatives

    In case of a SimulationScenario only this scenario is deleted.
    In case of a MutationScenario the parent is deleted if the parent has no other children.
    This is done since the user does not see source scenarios inside his management view.
    """
    logger.info(f"Deleting Scenario with {scenario.id=} and {scenario.manager}")
    # Delete from db. This does not affect the in memory scenario
    logger.info(scenario.delete())
    if scenario.scenario_type == EnumScenarioType.SIMULATION:
        return
    if scenario.parent is None:
        return
    children = Scenario.objects.filter(parent=scenario.parent)
    if children.exists():
        return
    logger.info(scenario.parent.delete())


def migrate_legacy_tco_forward(legacy_params: dict) -> dict | None:
    """Returns a dict with the new tco_parameter setup"""
    legacy_keys = {
        "energy_cost",
        "maint_cost",
        "maint_cost_diesel",
        "maint_infr_cost",
        "pef_general",
        "pef_wages",
        "pef_energy",
        "pef_fuel",
        "pef_insurance",
    }
    if not legacy_keys.intersection(legacy_params):
        return None
    old_fuel = legacy_params.pop("fuel_cost", 1.5)
    new_params = dict(legacy_params)
    new_params["fuel_cost"] = {
        "electricity": legacy_params.pop("energy_cost", 0.18),
        "diesel": old_fuel if not isinstance(old_fuel, dict) else old_fuel.get("diesel", 1.5),
    }
    new_params["vehicle_maint_cost"] = {
        "electricity": legacy_params.pop("maint_cost", 0.07),
        "diesel": legacy_params.pop("maint_cost_diesel", 0.14),
    }
    new_params["infra_maint_cost"] = legacy_params.pop("maint_infr_cost", 1000.0)
    new_params["cost_escalation_rate"] = {
        "general": legacy_params.pop("pef_general", 0.02),
        "staff": legacy_params.pop("pef_wages", 0.02),
        "electricity": legacy_params.pop("pef_energy", 0.02),
        "diesel": legacy_params.pop("pef_fuel", 0.02),
        "insurance": legacy_params.pop("pef_insurance", 0.02),
    }
    for stale in (
        "energy_cost",
        "maint_cost",
        "maint_cost_diesel",
        "maint_infr_cost",
        "pef_general",
        "pef_wages",
        "pef_energy",
        "pef_fuel",
        "pef_insurance",
    ):
        new_params.pop(stale, None)
    return new_params


class StationOpimizationImpossible(Exception):
    pass
