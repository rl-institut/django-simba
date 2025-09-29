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
from uuid import UUID as UUIDType
from celery import shared_task, uuid
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
from eflips.depot import UnstableSimulationException, DelayedTripException
from eflips.depot.api import (  # noqa
    simulate_scenario,
    generate_depot_optimal_size,
    generate_depot_layout,
)


import core.deepcopy
from ebusdjango.util import get_static_file_path
import ebustoolbox.util
import simba.optimizer_util
import simba.station_optimization
import simba.simulate
import simba.util
from core.deepcopy import reset_postgres_auto_increments
from core.models import EnumProgress, Progress
from simba.data_container import DataContainer
from simba.schedule import Schedule as SimbaSchedule
from . import schedule_readers, forms
from .models import (
    AreaInformation,
    DepotConfigurationWish,
    DepotMutation,
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
    SimulationRange,
    DepotSelection,
    ElectrificationOptions,
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

# NOTE: 1% is not a very small number, but the balanced strategy can have deltas of at least 0.6%
EPS = 1e-2  # a small number, used to allow for difference when comparing floats


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
    source_vehicle_type.save()
    # Copy vehicle_classes and consumption and add the new vehicle type to it
    for vehicle_class in vehicle_classes:
        # Cast consumptions to list to evaluate them early
        consumptions = list(vehicle_class.consumption_set.all())

        vehicle_class.id = ebustoolbox.util.get_next_id(VehicleClass)
        vehicle_class.scenario = target_vehicle_type.scenario
        vehicle_class.save()
        vehicle_class.vehicle_types.add(source_vehicle_type)
        if consumptions:
            assert len(consumptions) == 1
            c = consumptions[0]
            c.id = ebustoolbox.util.get_next_id(Consumption)
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

    schedule, args = get_schedule_from_db(django_scenario)

    return django_scenario, schedule, args


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


# TODO: Do somewhere else?
def filter_inconsistent_trips_and_rotations(simba_schedule):
    # Some filter functions to handle messy bvg input
    counter = 0
    del_rots = []
    for key, rotation in simba_schedule.rotations.items():
        depart_times = [t.departure_time for t in rotation.trips]
        arrival_times = [t.arrival_time for t in rotation.trips]
        start = 0
        while True:
            for i, __ in enumerate(rotation.trips[start:]):
                i = i + start
                if (
                    depart_times.count(rotation.trips[i].departure_time) > 1
                    or arrival_times.count(rotation.trips[i].arrival_time) > 1
                ):
                    break
            else:
                rotation.trips = list(sorted(rotation.trips, key=lambda x: x.departure_time))
                break
            counter += 1
            rotation.trips.pop(i)
            depart_times = [t.departure_time for t in rotation.trips]
            arrival_times = [t.arrival_time for t in rotation.trips]
            start = i

        if (
            rotation.trips[0].departure_name not in simba_schedule.stations
            or rotation.trips[-1].arrival_name not in simba_schedule.stations
        ):
            del_rots.append(key)
    logger.info(
        f"Deleting {len(del_rots)} rotations since they dont start or end at electrified station:{del_rots}"
    )
    for rot_id in del_rots:
        del simba_schedule.rotations[rot_id]
    if counter > 0:
        logger.info(f"{counter} trips deleted")


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
) -> dict[str, QuerySet[Notification]]:
    return {
        "error": notifications.filter(
            level=EnumNotificationLevels.ERROR,
        ),
        "warning": notifications.filter(
            level=EnumNotificationLevels.WARNING,
        ),
        "info": notifications.filter(
            level=EnumNotificationLevels.INFO,
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
            "Some rotations in the database contain vehicles, others do not. "
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
        # TODO: clean up conditionals

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
            simba_trip_dict = {
                "rotation_id": simba_id,
                "departure_time": trip.departure_time,
                "departure_name": trip.route.departure_station.to_simba_name(),
                "arrival_time": trip.arrival_time,
                "arrival_name": trip.route.arrival_station.to_simba_name(),
                "vehicle_type": str(vehicle_type),
                "charging_type": charging_type,
                "distance": trip.route.distance,
                "line": lines_dict[trip.route.line.id].name,
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
            logger.warning(text + "\n This message is only shown once as warning.")
        else:
            logger.debug(text)
    if 1 < level_of_loading or 0 > level_of_loading:
        text = f"Level of loading is out of [0,1] range for {trip.id=}"
        if warning_dict["level_of_loading_out_of_range"]:
            warning_dict["level_of_loading_out_of_range"] = False
            logger.warning(text + "\n This message is only shown once as warning.")
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
            logger.warning(text + "\n This message is only shown once as warning.")
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
            logger.warning(text + "\n This message is only shown once as warning.")
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
    logger.debug(f"Setting default arguments for scenario {django_scenario.id}")
    # Get parser from SimBA
    parser = simba.util.get_parser()
    # Read the parse values, in this case the default values
    args, _ = parser.parse_known_args()

    p = get_static_file_path(__package__, "examples/default_optimizer.cfg")
    args.optimizer_config_path = str(p)
    if not p.is_file():
        logger.info("default_optimizer.cfg not found. Optimizer config will use default values.")
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
    for name, ele_station in electrified_stations.items():
        # TODO: loop over stations
        station = Station.objects.get(id=Station.get_id_from_simba_name(name), scenario=scenario)
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
            logger.warning(f"Station {station.name} does not have a power per charger")
            if power_per_charger is None:
                assert station.power_per_charger is None

        station.power_per_charger = power_per_charger
        station.power_total = ele_station.get(
            "gc_power", scenario.simba_options.get("gc_power_" + charge_type)
        )
        if station.power_total is None:
            logger.warning(f"Station {station.name} does not have a power_total Value")
        station.save()


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
    parent.name = "Parent of " + parent.name
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
            ebustoolbox.util.validate_zip(zf.ZipFile(f), 100, max_uncompressed_size, 5)
            for f in file_paths.values()
            if Path(f).suffix == ".zip"
        ]
        schedule_reader_factory = schedule_readers.get_schedule_reader_factory(reader_num)
        schedule_reader: ScheduleReader = schedule_reader_factory(**file_paths, **cleaned_data)
        # The progress is linked to the child scenario.
        schedule_reader.set_observer(progress)
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
        scenario.scenario_type = EnumScenarioType.MUTATION
        parent.scenario_type = EnumScenarioType.SOURCE
        scenario.save()
        transform_depot_stations(parent, scenario)
        # Parent contains the trip data so check the consistency of the parent and not the mutation.
        if not (is_consistent(parent)):
            logger.error("Scenario does not seem to be consistent with assumptions")
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

        # Make sure postgres auto increment is up to date
        core.deepcopy.reset_postgres_auto_increments(["ebustoolbox"])


# for some reason, creating the atomic savepoint in an outer atomic transaction fails.
@atomic(savepoint=False)
def trim_scenario(scenario, time_delta, start_time=None):
    rotations = get_rotations_by_timespan(scenario, time_delta, start_time)
    rotations_to_remove = Rotation.objects.filter(scenario=scenario).exclude(id__in=rotations)
    logger.info(f"Deleting {rotations_to_remove.count()} rotations out of sim range")
    rotations_to_remove.delete()
    pass


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
    logger.info("Simulating scenario with average consumption first")
    default_simulation_scenario = merge_scenario(mutation_id, default_simulation_task_id)
    assign_new_vehicles_to_db(default_simulation_scenario)
    _ = _run_ebus_toolchain.apply(
        (str(default_simulation_task_id),),
        task_id=str(default_simulation_task_id),
    )
    logger.info("Simulating scenario with high consumption")
    sizing_scenario = merge_scenario(mutation_id, sizing_scenario_task_id)
    apply_sizing_parameters(mutation_id, sizing_scenario)
    assign_new_vehicles_to_db(sizing_scenario)
    _ = _run_ebus_toolchain.apply(
        (str(sizing_scenario_task_id),), task_id=str(sizing_scenario_task_id)
    )
    progress.set_success()


def apply_sizing_parameters(mutation_id, scenario: Scenario) -> None:
    """Increase all consumptions in some way"""

    vts = VehicleType.objects.filter(scenario=scenario)
    for vt in vts:
        if vt.consumption is not None:
            vt.consumption *= 2
        else:
            consumptions = Consumption.objects.filter(vehicle_class__vehicle_types=vt)
            assert consumptions.count() == 1
            vt.consumption = vt.max_consumption
        vt.save()
    sim_range = SimulationRange.objects.get(scenario_id=mutation_id)
    Temperatures.objects.filter(scenario=scenario).delete()
    # Create temperature instance
    Temperatures.create_constant_temperatures(scenario, sim_range.temperature_extreme)


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


class SimulationDepotsMissingException(Exception):
    pass


class SimulationDoubleArrivalException(Exception):
    pass


class SimulationDepartureFailException(Exception):
    pass


class SimulationExecutionFailException(Exception):
    pass


def create_spiceev_scenario_dict(scenario: Scenario) -> dict:  # noqa: C901
    events = scenario.event_set.filter(event_type=EventType.CHARGING_DEPOT)
    if not events.exists():
        raise SimulationEventsMissingException("SpiceEV scenario generation: no events found")

    args = get_args(scenario)
    start_simulation = events.order_by("time_start").first().time_start
    stop_simulation = events.order_by("time_end").last().time_end
    # simulate whole last timestep
    n_intervals = -int((start_simulation - stop_simulation) // timedelta(minutes=args.interval))
    # and one more timestep, since vehicle soc are taken at begin of each timestep
    n_intervals += 1

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
    spice_ev_events = get_spiceev_events_from_scenario(scenario, skip_oppb=True)
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
        if event["event_type"] == "arrival":
            if vehicle_to_cs.get(event["vehicle_id"]) is not None:
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
                        f"SpiceEV scenario generation: Station {station} "
                        f"exceeds maximum number of charging stations ({max_cs})."
                    )
                    # disable further warnings
                    max_cs_dict[station] = None
            # take note in lookup tables for future reference (departure)
            occupied_cs[station].add(cs_id)
            vehicle_to_cs[event["vehicle_id"]] = (station, cs_id)
            # update event station from Station (GC) name to charging station
            event["update"]["connected_charging_station"] = cs_id
        elif event["event_type"] == "departure":
            try:
                station, cs_id = vehicle_to_cs[event["vehicle_id"]]
            except KeyError:
                raise SimulationDepartureFailException(
                    f"SpiceEV scenario generation: departure without arrival {event}"
                )
            # clear occupied state
            occupied_cs[station].remove(cs_id)
            unoccupied_cs[station].add(cs_id)
            vehicle_to_cs[event["vehicle_id"]] = None

    # create needed charging stations
    charging_stations = dict()
    for station, station_info in grid_connectors.items():
        for cs_id in occupied_cs[station] | unoccupied_cs[station]:
            charging_stations[cs_id] = {
                "max_power": station_info["power_per_charger"],
                "parent": station,
            }

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


def get_spiceev_events_from_scenario(scenario, skip_oppb=False):
    # Create SpiceEV-like event dictionaries for a Scenario

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
    # iterate over events in-order, creating SpiceEV event-dicts for each charging event
    for event in charging_events:
        vid = event.vehicle.to_simba_name()
        # create arrival event
        event_list.append(
            {
                "signal_time": scenario_start_time.isoformat(),
                "start_time": event.time_start.isoformat(),
                "vehicle_id": vid,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": event.station.to_simba_name(),
                    "estimated_time_of_departure": event.time_end.isoformat(),
                    "soc_delta": event.soc_start - vehicle_soc[event.vehicle_id],
                    "desired_soc": event.soc_end,
                },
            }
        )

        # create departure event (end of charging, not necessarily leaving station)
        event_list.append(
            {
                "signal_time": scenario_start_time.isoformat(),
                "start_time": event.time_end.isoformat(),
                "vehicle_id": vid,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": None,
                },
            }
        )

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


def replace_event_timeseries(event: Event, soc_ts: list) -> None:
    # replace Event soc timeseries with arbitrary list
    # ### sanity checks ### #
    # start and end soc must remain the same
    if not (abs(soc_ts[0] - event.soc_start) < EPS):
        logger.info(
            f"Delta of {abs(soc_ts[0] - event.soc_start)} at {event}."
            f"{event.soc_start} Start Soc\n Timeseries:\n{soc_ts}"
        )
        raise AssertionError("Depot Charging Simulation diverged")
    if not (abs(soc_ts[-1] - event.soc_end) < EPS):
        logger.info(
            f"Delta of {abs(soc_ts[-1] - event.soc_end)} at {event}."
            f"{event.soc_end} END SOC\n Timeseries:\n{soc_ts}"
        )
        raise AssertionError("Depot Charging Simulation diverged")
    # event soc should always be defined / not null
    assert all([soc is not None for soc in soc_ts])
    # soc and time lists must have same length
    assert len(soc_ts) == len(event.timeseries["time"])
    # save to DB
    event.timeseries["soc"] = soc_ts


def apply_depot_strategy(scenario: Scenario, strategy: str) -> None:
    # simulate all depot charging in SpiceEV with new strategy, update timeseries
    spice_ev_scenario_dict = create_spiceev_scenario_dict(scenario)
    spice_ev_scenario = simulate_depot_strategy(spice_ev_scenario_dict, strategy)
    # attach vehicle soc to SpiceEV scenario
    spice_ev_report.generate_soc_timeseries(spice_ev_scenario)
    # update events with new soc timeseries
    events = scenario.event_set.filter(event_type=EventType.CHARGING_DEPOT)
    for event in events:
        vid = event.vehicle.to_simba_name()
        # find timeseries timestep range (indices of relevant timesteps)
        ts_start = -(
            (spice_ev_scenario.start_time - event.time_start) // spice_ev_scenario.interval
        )
        ts_end = -((spice_ev_scenario.start_time - event.time_end) // spice_ev_scenario.interval)
        # end timestep is inclusive in range
        time_range = range(ts_start, ts_end + 1)
        if event.timeseries is None:
            event.timeseries = {
                "time": [
                    (spice_ev_scenario.start_time + i * spice_ev_scenario.interval).isoformat()
                    for i in time_range
                ]
            }
        new_soc_ts = [spice_ev_scenario.vehicle_socs[vid][i] for i in time_range]
        replace_event_timeseries(event, new_soc_ts)
    Event.objects.bulk_update(events, ["timeseries"])
    logger.info(f"{events.count()} depot charging events updated")


def apply_depot_and_area_wishes(mutation: Scenario, child: Scenario, stack: dict) -> None:
    depot_configs = DepotConfigurationWish.objects.filter(scenario=mutation)
    # Assert uniqueness of the mutations
    new_depot_configs = []
    new_area_infos = []
    i = ebustoolbox.util.get_next_id(DepotConfigurationWish)
    ii = ebustoolbox.util.get_next_id(AreaInformation)
    for depot_config in depot_configs:
        depot_config: DepotConfigurationWish
        area_infos = AreaInformation.objects.filter(
            scenario=mutation, depot_configuration_wish=depot_config
        )
        search_station = StationMutation.objects.get(
            mutated_original_station=depot_config.station
        ).original_station
        depot_config.station_id = stack[Station][search_station.id]
        depot_config.scenario = child
        depot_config.id = i
        i += 1
        new_depot_configs.append(depot_config)

        for area_info in area_infos:
            area_info: AreaInformation
            area_info.scenario = child
            search_vt = VehicleTypeMutation.objects.get(
                mutated_vehicle_type=area_info.vehicle_type
            ).original_vehicle_type
            area_info.vehicle_type_id = stack[VehicleType][search_vt.id]
            area_info.depot_configuration_wish = depot_config
            area_info.id = ii
            ii += 1
            new_area_infos.append(area_info)

    DepotConfigurationWish.objects.bulk_create(new_depot_configs)
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
    vehicle_last_id = Vehicle.objects.aggregate(Max("id"))["id__max"] or 0
    for i, r in enumerate(Rotation.objects.using(db_name).filter(scenario=django_scenario)):
        vehicle_last_id += 1
        vt = r.vehicle_type
        v_name = "Vehicle_" + str(i)
        vehicle = Vehicle(
            id=vehicle_last_id, scenario=django_scenario, vehicle_type=vt, name=v_name
        )
        vehicles.append(vehicle)
        r.vehicle = vehicle
        rotations.append(r)
    Vehicle.objects.bulk_create(vehicles)
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
        exclude_models={Scenario, User, Event, Progress, UserGroup},
        exclude_fields={
            DepotSelection._meta.get_field("depots"),
            ElectrificationOptions._meta.get_field("electrified_stations"),
            VehicleTypeMutation._meta.get_field("original_vehicle_type"),
            DepotMutation._meta.get_field("original_depot"),
            StationMutation._meta.get_field("original_station"),
        },
        max_depth=1,
    )
    return copied_instance, stack


def create_empty_child_scenario(parent_scenario: Scenario, task_id):
    parent_id = parent_scenario.id
    # Decouple memory of parent and child
    new_child_scenario = Scenario.objects.get(id=parent_scenario.id)
    new_child_scenario.id = ebustoolbox.util.get_next_id(Scenario)
    new_child_scenario.task_id = task_id
    new_child_scenario.parent_id = parent_id
    new_child_scenario.save()
    return new_child_scenario


@atomic()
def create_scenario_copy_for_user(mutation_scenario: Scenario):
    assert isinstance(mutation_scenario, Scenario)
    assert mutation_scenario.parent is not None
    assert mutation_scenario.parent.parent is None
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
    if parent_scenario.simba_options:
        child.simba_options = parent_scenario.simba_options
    else:
        child.simba_options = vars(get_args(child))
    child.save()

    # Mutate child according to parent
    # Remove rotations from the timespan
    sim_range = SimulationRange.objects.get(scenario=mutation)
    time_delta = sim_range.end - sim_range.start
    trim_scenario(child, time_delta, sim_range.start)
    # # Used for clearing up depots without rotations
    trim_depots(child, [])

    depot_selections = DepotSelection.objects.filter(scenario=mutation)
    assert depot_selections.count() <= 1, "Only a single depot selection is allowed per scenario"
    depot_selection = depot_selections.first()
    if depot_selection is not None:
        # These depots were selected to remain
        original_depot_ids = depot_selection.depots.all().values_list("id", flat=True)
        copied_depot_ids = [stack[Station][org_id] for org_id in original_depot_ids]
        all_depots = Station.objects.filter(scenario=child, charge_type=EnumChargeType.DEPOT)
        depots_to_remove = all_depots.exclude(id__in=copied_depot_ids)
        trim_depots(child, depots_to_remove)

    # Copy Temperatures
    temperatures_query = Temperatures.objects.filter(scenario=mutation)

    if temperatures_query.exists():
        assert temperatures_query.count() == 1
        temperature = temperatures_query.first()
        temperature.id = ebustoolbox.util.get_next_id(Temperatures)
        temperature.scenario = child
        temperature.save()

    # child.simba_options.update(ele_dict)
    all_stations = Station.objects.filter(scenario=mutation)
    electrified_stations = Station.objects.filter(scenario=mutation, is_electrified=True)
    excluded_stations = Station.objects.filter(scenario=mutation, is_electrifiable=False)
    # Some stations are not electrified or excluded -->possible need for optimization
    if all_stations.count() > electrified_stations.count() + excluded_stations.count():
        logger.info("Mode is set to optimization.")
        child.simba_options["modes"] = "sim,station_optimization,report"
    else:
        logger.info("Mode is set to NO optimization.")
        child.simba_options["modes"] = "sim,report"

    # org_ele_station_ids = ele_option.electrified_stations.all().values_list("id", flat=True)
    # copied_ele_station_ids = [stack[Station][org_id] for org_id in org_ele_station_ids]
    # electrify_db_stations(child, copied_ele_station_ids)
    # for station in Station.objects.filter(scenario=mutation).exclude(id__in=copied_ele_station_ids):
    #     station.is_electrified = False
    #     station.save()

    apply_vehicle_mutation(mutation, child, stack)
    apply_station_mutation(mutation, child, stack)
    apply_depot_and_area_wishes(mutation, child, stack)

    child.save()
    return child


@atomic()
def create_station_mutations(scenario):
    Station.objects.filter(scenario=scenario).delete()
    next_id = ebustoolbox.util.get_next_id(Station)
    stations = []
    mutations = {}
    # Create a station for each station in the parent scenario
    for station in Station.objects.filter(scenario=scenario.parent):
        mutations[station.id] = next_id
        station.id = next_id
        next_id += 1
        station.scenario = scenario
        stations.append(station)
    Station.objects.bulk_create(stations)

    # Create a station mutation which link the original and mutation
    next_id = ebustoolbox.util.get_next_id(StationMutation)
    station_mutations = []
    for original, mutation in mutations.items():
        sm = StationMutation(
            id=next_id,
            scenario=scenario,
            original_station_id=original,
            mutated_original_station_id=mutation,
        )
        next_id += 1
        station_mutations.append(sm)
    StationMutation.objects.bulk_create(station_mutations)


@shared_task(bind=True)
def _run_ebus_toolchain(self, task_id):
    """Run the tool chain"""
    db_scenario = Scenario.objects.get(task_id=task_id)
    assert is_consistent(db_scenario)
    # With multiple simulations the progress is linked through the parent to its child scenarios
    progress = Progress.objects.filter(
        scenario=db_scenario.parent, progress_type=EnumProgress.RUNNING_SIMULATION
    ).first()
    if not progress:
        logger.warning(
            "The toolchain did not find a progress belonging to the parent of the scenario. "
            "Creating a Progress bound to the simulation scenario instead"
        )
        progress = Progress.objects.create(
            scenario=db_scenario, progress_type=EnumProgress.RUNNING_SIMULATION, task_id=task_id
        )

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
        logger.info(f"Getting schedule from db {datetime.now()}")
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

        schedule, simba_scenario = run_simba(schedule, args, db_scenario, mode="sim", scenario=None)

        progress.current_work += 1
        progress.save()
        schedule, simba_scenario = run_simba(
            schedule, args, db_scenario, mode="station_optimization", scenario=simba_scenario
        )

        progress.current_work += 1
        progress.save()
        notifications = []

        try:
            run_eflips(task_id)
        except UnstableSimulationException as e:
            # TODO: handle it and pass information to user
            logger.error("The simulation is unstable")
            logger.error(traceback.format_exception(e))
            notification = Notification(
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
            logger.error("There are delays in the Simulation")
            logger.error(traceback.format_exception(e))
            # TODO: @TU what notification should the user receive
            notification = Notification(
                sender="eflips-depot",
                level=EnumNotificationLevels.WARNING,
                notification_type=EnumNotificationType.DELAYED_TRIP_WARNING,
                message=_("Manche Fahrzeuge können nur verspätet abfahren"),
            )
        except Exception as e:
            logger.error("Eflips raised an unexpected Exception")
            logger.error(traceback.format_exception(e))
            notification = Notification(
                sender="eflips-depot",
                level=EnumNotificationLevels.ERROR,
                notification_type=EnumNotificationType.UNEXPECTED_ERROR,
                message=_("Ein unerwarteter Fehler ist aufgetreten! "),
            )
            notifications.append(notification)
            progress.refresh_from_db()
            progress.errors.append(str(e))
            progress.set_failed()
            raise
        finally:
            for scenario in [db_scenario, db_scenario.parent]:
                # parent might not exist
                if scenario is None:
                    continue
                for notification in notifications:
                    notification.scenario = scenario
                    notification.save()

        progress.current_work += 1
        progress.save()
        eflips_assignment = get_assigned_vehicles(task_id)
        schedule.assign_vehicles_custom(eflips_assignment)

        # Simba Run to add back the deleted events of eflips.
        # TODO: Does eflips need to delete the events? then we could skip this step
        simba_scenario = run_simba(schedule, args, db_scenario, mode="sim", scenario=None)
        # TODO: Keep that? / Set Depot values for final SimBA simulation?
        electrify_depot_station_w_default(db_scenario)
        #
        # get electrified stations from db, e.g. depot station from eFLIPS with
        # power
        stations_dict = get_electrified_stations_from_db(db_scenario)
        schedule.stations = stations_dict.copy()

        # NOTE: Consolidate results with a given strategy. EPS of 1% needed.
        # Balanced strategy or expose from simba_options? TODO: Discuss
        # TODO: Consolidate with depot electrification above
        apply_depot_strategy(db_scenario, "balanced")

        progress.current_work += 1
        progress.save()

        check_event_soc_consistency(db_scenario)
        db_scenario.refresh_from_db()
        db_scenario.finished = timezone.now()
        db_scenario.save()
    except Exception as e:
        logger.error(traceback.format_exc())
        progress.refresh_from_db()
        progress.errors.append(str(e))
        progress.set_failed()
        raise


def check_event_soc_consistency(db_scenario: Scenario):
    """Give warning if scenario events are not consistent.

    Consistency in this case is that soc_end values are identical to the next events soc_start of the same vehicle.
    """
    for vehicle in Vehicle.objects.filter(scenario=db_scenario):
        events = list(Event.objects.filter(vehicle=vehicle).order_by("time_start"))
        for i in range(len(events) - 2):
            if not events[i].soc_end == events[i + 1].soc_start:
                logger.warning(
                    f"SOC does not align between events for {vehicle=} for "
                    f"events {events[i]} and {events[i+1]}"
                    f"\n DELTA = {events[i].soc_end - events[i + 1].soc_start}"
                )

            if not events[i].time_end == events[i + 1].time_start:
                logger.warning(
                    f"Times do not align for Events {events[i].id} and {events[i+1].id} "
                )


def electrify_depot_station_w_default(db_scenario):
    for depot in Depot.objects.filter(scenario=db_scenario):
        logger.warning("Overwriting Depot Station data. This data should be provided by eflips")
        station = depot.station
        # TODO: get defaults from somewhere
        station.is_electrified = True
        station.power_total = station.power_total or 1000_000
        station.amount_charging_places = station.amount_charging_places or 1000
        station.power_per_charger = station.power_per_charger or 300
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

        vehicle_assigns.append(
            {"rot": rot.id, "v_id": vehicle.to_simba_name(), "soc": prev_event.soc_end}
        )
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

    logger.info(f"Running Simba {datetime.now()} with mode {mode}")
    # TODO don't overwrite output on multiple function calls
    args.output_directory = Path(settings.UPLOAD_PATH) / str(db_scenario.task_id)
    args.attach_vehicle_soc = True

    schedule, scenario = run_mode(mode, schedule, scenario, args, db_scenario)

    # Apply changes to database depending on mode
    match mode:
        case "sim":
            pass
        case w if w in ["station_optimization", "station_optimization_single_step"]:
            update_electrified_stations_db(schedule.stations, db_scenario)
        case _:
            raise NotImplementedError

    logger.info(f"Creating SimBA Events {datetime.now()}")
    create_event_output(scenario, db_scenario)
    logger.info(f"SimBA Events Created {datetime.now()}")
    reset_postgres_auto_increments(apps=[Event._meta.app_label])
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


def run_eflips(task_id) -> None:
    logger.info(f"Running eFLIPS {datetime.now()}")
    db_scenario = Scenario.objects.get(task_id=task_id)

    # calculate total scenario time for eFLIPS repetition period
    last_trip_time = Trip.objects.filter(scenario=db_scenario).aggregate(Max("arrival_time"))
    first_trip_time = Trip.objects.filter(scenario=db_scenario).aggregate(Min("departure_time"))
    period = last_trip_time["arrival_time__max"] - first_trip_time["departure_time__min"]

    # Constructing the database URL manually
    db_url = create_db_url()

    generate_depot_layout(
        db_scenario, database_url=db_url, charging_power=90, delete_existing_depot=True
    )
    # generate_depot(
    #     db_scenario,
    #     database_url=db_url,
    #     charging_power=90,
    #     delete_existing_depot=True,
    #     use_consumption_lut=True,
    #     repetition_period=period,
    # )
    #
    simulate_scenario(
        db_scenario,
        database_url=db_url,
        repetition_period=period,
        ignore_unstable_simulation=False,
        ignore_delayed_trips=False,
    )


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
    trips = list(Trip.objects.filter(rotation=rotation).order_by("departure_time"))
    for trip in trips:
        if trip.arrival_time <= trip.departure_time:
            logger.error(f"A trip must have a duration. {trip=}")
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
    event_id = ebustoolbox.util.get_next_id(Event)
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
            logger.warning("None Values found in timeseries")
            forward_fill_last_value(timeseries["soc"])
        # grab current vehicle SoC at timestep
        soc_start = timeseries["soc"][0]
        soc_end = timeseries["soc"][-1]
        if None in timeseries["soc"]:
            raise Exception(
                f"{vehicle.to_simba_name()}/{vehicle.id} has None values in between socs"
            )
        event = Event(
            id=event_id,
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
        event_id += 1
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
        depot_stations.add(trips.last().route.arrival_station)

    logger.info(f"{len(depot_stations)} Depot Stations found")

    for station in depot_stations:
        station.is_electrified = True
        station.charge_type = EnumChargeType.DEPOT.value
        station.voltage_level = EnumVoltageLevel.VOLTAGE_MV.value
        station.save()


@atomic(savepoint=False)
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
            logger.info(f"Deleting station {station.name}")
            Rotation.objects.filter(
                scenario=scenario, trip__route__arrival_station=station
            ).delete()
            Rotation.objects.filter(
                scenario=scenario, trip__route__departure_station=station
            ).delete()
            station.delete()
        else:
            logger.info(f"Station with id {dep_id} not found in scenario")
    (
        Station.objects.filter(scenario=scenario)
        .annotate(departure_count=Count("route_departure_set__trip"))
        .annotate(arrival_count=Count("route_arrival_set__trip"))
        .filter(departure_count=0, arrival_count=0)
        .delete()
    )
    (Route.objects.filter(scenario=scenario).annotate(count=Count("trip")).filter(count=0).delete())
    logger.info(
        f"Before -> After trimming\n"
        f"rotations:{rot_before_count} -> {Rotation.objects.filter(scenario=scenario).count()}\n"
        f"trips: {trip_before_count} ->{Trip.objects.filter(scenario=scenario).count()}\n"
        f"routes: {route_before_count} ->{Route.objects.filter(scenario=scenario).count()}\n"
        f"stations: {station_before_count} ->{Station.objects.filter(scenario=scenario).count()}\n"
        f"vehicles: {vehicle_before_count} ->{Vehicle.objects.filter(scenario=scenario).count()}\n"
    )


@atomic()
def transform_depot_stations(parent: Scenario, child: Scenario) -> None:
    """
    Duplicate depot stations and transform them into opportunity stations where necessary;

    WeBus only supports Blocks which start and end at depot station without intermediate stops
    at depots.
    This function determines if there are intermediate stops at depot stations,
    creates an opportunity station and switches this station into the appropriate routes.
    :param parent: Source scenario
    :param child: Child scenario which is notified about changes
    """

    depots = Station.objects.filter(scenario=parent, charge_type=EnumChargeType.DEPOT)
    all_routes = Route.objects.filter(scenario=parent)
    depot_arrival_routes = all_routes.filter(arrival_station__in=depots)
    depot_departure_routes = all_routes.filter(departure_station__in=depots)
    # Only the first and last trip of a block should departe/arrive in a depot station.
    # The other trips should refrence routes which go to a newly generated opportunity station,
    # instead of the depot station.
    # This query expects a outer ref to a rotation and returns the ordered trips by arrival time
    # with the last arrival first
    last_trip_subquery = Trip.objects.filter(rotation=OuterRef("pk")).order_by("-arrival_time")

    # Get the ids of each rotations last trip
    last_trip_ids = list(
        Rotation.objects.filter(scenario=parent)
        .annotate(last_trip_id=Subquery(last_trip_subquery.values("id")[:1]))
        .values_list("last_trip_id", flat=True)
    )

    first_trip_subquery = Trip.objects.filter(rotation=OuterRef("pk")).order_by("arrival_time")
    # Get the ids of each rotations first trip
    first_trip_ids = list(
        Rotation.objects.filter(scenario=parent)
        .annotate(first_trip_id=Subquery(first_trip_subquery.values("id")[:1]))
        .values_list("first_trip_id", flat=True)
    )
    relevant_trips = Trip.objects.filter(scenario=parent)
    trip_dict = {t.id: t for t in relevant_trips}
    new_stations = dict()
    changed_rotations = dict()

    route_id = ebustoolbox.util.get_next_id(Route)
    station_id = ebustoolbox.util.get_next_id(Station)
    # NOTE: We make use of the lazy nature of queries. depot_departure_routes is evaluated after
    # the arrival_routes were created

    for depot_routes, allowed_depot_trips, station_type in zip(
        [depot_arrival_routes, depot_departure_routes],
        [last_trip_ids, first_trip_ids],
        ["arrival_station", "departure_station"],
    ):
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
                new_route = route
                changed_routes.append(new_route)
            else:
                # Some trips need to keep a reference to the route ending in a depot.
                # The intermediate trips need a new route
                # Copy the route
                route.id = route_id
                route_id += 1
                new_route = route
                new_routes.append(new_route)
            new_station = new_stations.get(getattr(route, station_type))
            if not new_station:
                old_station = getattr(route, station_type)
                # Create a new station which has electrification defaults
                new_station = Station.objects.create(
                    id=station_id,
                    name=old_station.name,
                    name_short=old_station.name_short,
                    geom=old_station.geom,
                    scenario=old_station.scenario,
                )
                station_id += 1
                new_stations[old_station] = new_station
            setattr(new_route, station_type, new_station)
            for t_id in intermediate_trips:
                t = trip_dict[t_id]
                t: Trip
                if changed_rotations.get(t.rotation) is None:
                    changed_rotations[t.rotation] = set()
                changed_rotations[t.rotation].add(new_station)
                t.route = new_route
                changed_trips.append(t)
        if changed_trips or changed_routes or new_routes:
            logger.info(
                "Schedule was transformed to remove intermediate depot trips.\n"
                f"{changed_trips=}\n{changed_routes=}\n{new_routes=}"
            )

        Trip.objects.bulk_update(changed_trips, fields=["route"])
        Route.objects.bulk_update(changed_routes, fields=["arrival_station", "departure_station"])
        Route.objects.bulk_create(new_routes)

    for scenario in [parent, child]:
        for rotation, stations in changed_rotations.items():
            Notification.objects.create(
                scenario=scenario,
                level=EnumNotificationLevels.WARNING,
                notification_type=EnumNotificationType.INTERMEDIATE_DEPOT_STOPS_TRANSFORMED,
                message=(
                    f"Für den Umlauf {escape(rotation.name)} wurden Zwischenhaltestellen "
                    f"an den Depots {[escape(s.name) for s in stations]} erzeugt. "
                    "Mehr Informationen finden Sie in der Hilfe."
                ),
            )
    if len(changed_rotations) > 0:
        logger.warning(
            f"{changed_rotations.keys()} were transformed so they dont have intermediate stops at depot stations"
        )
