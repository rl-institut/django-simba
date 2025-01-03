import csv
import random
import shutil
import traceback
import warnings
from argparse import Namespace
from copy import deepcopy, copy
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from pathlib import Path
from typing import TYPE_CHECKING, List

import environ
from celery import shared_task
import django.apps
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, Point
from django.db import connections
from django.db.models import Max, Count, Min, QuerySet
from django.db.transaction import atomic
from django.forms import model_to_dict
from django.http import HttpRequest
from django.utils import timezone
from django.utils.timezone import make_aware, is_aware
from eflips.depot.api import simulate_scenario, generate_depot_layout

import core.deepcopy
import ebustoolbox.util
import simba.optimizer_util
import simba.simulate
import simba.trip
import simba.util
from core.deepcopy import reset_postgres_auto_increments
from core.models import Progress
from simba.data_container import DataContainer
from simba.schedule import Schedule as SimbaSchedule
from . import schedule_readers
from .models import (
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
)
from .schedule_readers import ScheduleReader

if TYPE_CHECKING:
    from spice_ev.scenario import Scenario as SimbaScenario

logger = logging.getLogger("custom")

# ToDo: Any better solutions?
INTEGER_INF = 9999
MAX_AMOUNT_VEHICLES = 10000
DEFAULT_TEMPERATURE = 20


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


# ToDo Do somewhere else?
def filter_inconsistent_trips_and_rotations(simba_schedule):
    # Some filter functions to handle messy bvg input
    counter = 0
    del_rots = []
    for key, rotation in simba_schedule.rotations.items():
        depart_times = [t.departure_time for t in rotation.trips]
        arrival_times = [t.arrival_time for t in rotation.trips]
        start = 0
        while True:
            for i, _ in enumerate(rotation.trips[start:]):
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


def get_schedule_from_db(
    django_scenario: Scenario,
) -> tuple[simba.schedule.Schedule, Namespace]:
    """Takes a django Scenario and returns the simba Schedule and arguments

    Can be used to run a previously stored Django Scenario again straight from the database without
    using files, by returning schedule and args.

    :param django_scenario: Scenario
    :type django_scenario: .models.Scenario
    :return: (simba Schedule, args)
    :rtype: (simba.schedule.Schedule, Namespace)
    """
    data_container = DataContainer()

    # get SimBa station_data
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
        data_container.add_consumption_data(consumption.name, consumption.to_df())

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

    # Simba does not disallow opportunity charging for rotations.
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
    """Create simba rotations with trips from a database with a scenario as a key

    :param django_scenario: Django scenario
    :param station_data: dictionary with all stations and elevation
    :return: list of trip dictionaries
    :rtype: list
    """
    lines_dict = {line.id: line for line in Line.objects.filter(scenario=django_scenario)}
    simba_trips = list()
    temperatures = Temperatures.objects.filter(scenario=django_scenario)
    DEFAULT_LOADED_MASS = 0

    for rot in Rotation.objects.filter(scenario=django_scenario).select_related(
        "vehicle_type", "vehicle"
    ):
        vehicle_type = str(rot.vehicle_type.id)
        charging_type = (
            EnumChargeType.OPPORTUNITY.value
            if rot.vehicle_type.opportunity_charging_capable
            else EnumChargeType.DEPOT.value
        )

        # Use the id/pk instead of the name, since names might not be unique, when database is
        # filled with non simba ingesters
        simba_id = rot.id

        try:
            allowed_load = rot.vehicle_type.allowed_mass - rot.vehicle_type.empty_mass
        except TypeError:
            allowed_load = None
        vehicle_classes = VehicleClass.objects.filter(vehicle_types=vehicle_type)
        consumption_classes = vehicle_classes.exclude(consumption__isnull=True)
        assert len(consumption_classes) <= 1
        lut_consumption = False
        if len(consumption_classes) == 1:
            lut_consumption = True
        if lut_consumption and not temperatures.exists():
            logger.warning(
                f"Vehicle Type {rot.vehicle_type.id} uses a consumption LUT for "
                "consumption calculation but the scenario has no Temperature object for "
                "temperature lookup. Default value for temperature of "
                f"{DEFAULT_TEMPERATURE} °C is used."
            )

        if lut_consumption and allowed_load is None:
            allowed_load = 1000
            logger.warning(
                f"{rot.id=} is serviced by a vehicle_type with a consumption lut. "
                "The vehicle_type does not contain the allowed and empty mass. "
                f"The allowed load will be set to {allowed_load} kg."
            )
        # select related means later db access can be skipped
        query = (
            Trip.objects.filter(rotation=rot)
            .select_related("route__arrival_station", "route__departure_station", "route__line")
            .order_by("arrival_time")
        )

        for trip in query:
            loaded_mass = trip.loaded_mass
            level_of_loading = None
            if allowed_load is not None:
                if lut_consumption and loaded_mass is None:
                    loaded_mass = DEFAULT_LOADED_MASS
                    logger.warning(
                        f"{trip.id=} has no loaded mass but the vehicle_type which services this "
                        "trip needs a loaded mass for consumption look up and is set to "
                        f"{loaded_mass}."
                    )
                level_of_loading = loaded_mass / allowed_load
                if 1 < level_of_loading or 0 > level_of_loading:
                    logger.warning(f"Level of loading is out of [0,1] range for {trip.id=}")
            simba_trip_dict = {
                "rotation_id": simba_id,
                "departure_time": trip.departure_time,
                "departure_name": trip.route.departure_station.to_simba_name(),
                "arrival_time": trip.arrival_time,
                "arrival_name": trip.route.arrival_station.to_simba_name(),
                "vehicle_type": vehicle_type,
                "charging_type": charging_type,
                "distance": trip.route.distance,
                "line": lines_dict[trip.route.line.id].name,
                "height_diff": (
                    station_data[trip.route.arrival_station.to_simba_name()]["elevation"]
                    - station_data[trip.route.departure_station.to_simba_name()]["elevation"]
                ),
                "level_of_loading": level_of_loading,
                "mean_speed": trip.speed * 3.6,
                "temperature": DEFAULT_TEMPERATURE,
            }
            if temperatures.exists():
                assert (
                    len(temperatures) == 1
                ), "A scenario can only have a single linked Temperature object"
                temperature = temperatures.first()

                middle_time = trip.departure_time + 0.5 * (trip.arrival_time - trip.departure_time)
                # get pseudo mean temperature by using center and boundary temperatures
                temp = (
                    0.5 * temperature.get_interpolated_temperature(middle_time)
                    + 0.25 * temperature.get_interpolated_temperature(trip.arrival_time)
                    + 0.25 * temperature.get_interpolated_temperature(trip.departure_time)
                )
                simba_trip_dict["temperature"] = temp

            simba_trips.append(simba_trip_dict)
    return simba_trips


def get_vehicle_types_from_db(django_scenario) -> dict:
    """Create simba rotations with trips from database with scenario as key

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
            mileage = Consumption.objects.get(vehicle_class=query[0]).name

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
    """Create simba electrified stations from database with scenario as key

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

    Creates arguments for SimBA by getting default arguments from simba, updating them with
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

    # Add default optimizer config
    p = Path(settings.STATIC_URL, __package__, "examples", "default_optimizer.cfg")
    # ToDo Use ebusdjango.util.get_static_file_path
    if settings.DEBUG:
        # use app static folder
        if p.is_absolute():
            # remove first slash
            p = Path(str(p)[1:])
        p = Path(settings.BASE_DIR, __package__, p)
    args.optimizer_config_path = str(p)
    if not p.is_file():
        logger.info("default_optimizer.cfg not found. Optimizer config will use default values")

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
    scenario = Scenario.objects.create(name=cleaned_data["title"])
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
        p = Path(settings.STATIC_URL, __package__, "examples", v)
        # ToDo Use ebusdjango.util.get_static_file_path
        if settings.DEBUG:
            # use app static folder
            if p.is_absolute():
                # remove first slash
                p = Path(str(p)[1:])
            p = Path(settings.BASE_DIR, __package__, p)
        if not p.exists():
            logger.warning(f"FILE ERROR: {k} COULD NOT BE SET ({str(p)})")
            continue
        args[k] = str(p)
    scenario.simba_options = args
    scenario.save()

    return scenario


def vehicles_to_db(vehicle_types: dict, scenario: Scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """

    # ToDo: Get real data
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
                # The milage can be a link/ str to a consumption_table.In this case link
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


def stations_to_db(simba_schedule: SimbaSchedule, scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    object_list = []
    station_translation = dict()

    try:
        last_id = Station.objects.aggregate(Max("id"))["id__max"]
        if last_id is None:
            last_id = -1
    except Exception:
        last_id = -1
    for key, station in simba_schedule.station_data.copy().items():
        last_id += 1
        try:
            long = float(station["long"])
            lat = float(station["lat"])
            elevation = float(station["elevation"])
            geom = GEOSGeometry(f"POINT({long} {lat} {elevation})")
            params = dict(id=last_id, scenario=scenario, geom=geom, name=str(key))
            new_station = Station(**params)
            object_list.append(new_station)
            # try renaming the station in the simba context, so it gets access to the database.
            # This is needed to guarantee uniqueness of station names which is not enforced by the
            # database
            station_translation[key] = new_station.to_simba_name()
            simba_schedule.station_data[new_station.to_simba_name()] = station
            del simba_schedule.station_data[key]
            try:
                simba_schedule.stations[new_station.to_simba_name()] = simba_schedule.stations[key]
                del simba_schedule.stations[key]
            except KeyError:
                pass
        except Exception:
            logger.error(traceback.format_exc())
            pass
    Station.objects.bulk_create(object_list)

    # Update db stations which are electrified with info from electrified_stations dictionary
    update_electrified_stations_db(simba_schedule.stations, scenario)

    # mutate the schedule, so trip names are identical with new database names
    for rot_key in simba_schedule.rotations.copy().keys():
        rot = simba_schedule.rotations[rot_key]
        rot.arrival_name = station_translation[rot.arrival_name]
        rot.departure_name = station_translation[rot.departure_name]
        for trip in rot.trips:
            trip.arrival_name = station_translation[trip.arrival_name]
            trip.departure_name = station_translation[trip.departure_name]


def update_electrified_stations_db(electrified_stations, scenario):
    """Update stations which are electrified with info from electrified_stations dictionary"""
    for name, ele_station in electrified_stations.items():
        # Todo loop over stations
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


@shared_task(bind=True)
def init_db_with_trips(self, scenario_id: int, reader_num: int, files: dict, cleaned_data):
    progress = Progress.objects.create(task_id=self.request.id, status="Gestartet")
    # files is a dict with values of (path, file_id)
    file_paths = {key: value[0] for key, value in files.items()}
    try:
        schedule_reader_factory = schedule_readers.get_schedule_reader_factory(reader_num)
        schedule_reader: ScheduleReader = schedule_reader_factory(**file_paths, **cleaned_data)
        schedule_reader.set_observer(progress)
        scenario = Scenario.objects.get(id=scenario_id)
        progress.scenario = scenario
        progress.save()
        # parent scenario has all the content. "normal scenario" has the mutation. Simulation
        # Scenario is parent scenario with mutation applied

        delete_old_scenario_data(scenario)
        # Read the file and write it to database
        progress.refresh_from_db()
        progress.success = schedule_reader.write_to_db(scenario.id)
        scenario.simba_options = vars(get_args(scenario))
        find_and_make_depots(scenario)
        scenario.save()
        progress.save()
    except Exception as e:
        logger.error(traceback.format_exc())
        progress.status = "Fehlgeschlagen"
        progress.errors.append(str(e))
    finally:
        try:
            progress.errors.extend(schedule_reader.get_errors())
        except:  # noqa
            pass
        progress.status = "Fertig"
        if not progress.success:
            progress.status = "Fehlgeschlagen"
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
    logging.info(f"Deleting {rotations_to_remove.count()} rotations out of sim range")
    rotations_to_remove.delete()
    pass


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


@shared_task(bind=True)
def run_and_merge_scenarios(self, parent_id: int, mutation_id: int, simulation_task_id):
    parent_scenario = Scenario.objects.get(id=parent_id)
    mutation_scenario = Scenario.objects.get(id=mutation_id)
    simulation_scenario = create_child_from_mutation(parent_scenario, mutation_scenario)
    simulation_scenario.name = "Results for " + simulation_scenario.name
    simulation_scenario.task_id = simulation_task_id
    simulation_scenario.save()
    if "ebus_map" in settings.INSTALLED_APPS:
        create_stations_for_map(simulation_scenario)
    run_toolchain_from_scenario(simulation_scenario, assign_vehicles=True)


def run_toolchain_from_scenario(django_scenario: Scenario, assign_vehicles=False):
    """Run a Scenario from the database with SimBA

    The provided scenario must contain all information including Temperatures, Vehicle_Types,
    station information and electrified_station information.
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
    mode=None,
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
            simba_schedule_db, args_db, django_scenario, mode=mode, scenario=simba_scenario
        )
    finally:
        # Always reset the database to default
        for model in django.apps.apps.app_configs["ebustoolbox"].models.values():
            model.objects = model.objects.using("default")
    return simba_schedule, scenario


def get_spiceev_events_from_scenario(scenario, skip_oppb=False):
    # Create SpiceEV-like event dictionaries for a Scenario

    events = scenario.event_set.order_by("time_start")
    event_list = list()
    if not events.exists():
        return event_list
    # all events known at scenario start
    scenario_start_time = events.first().time_start

    # get initial SoC of all vehicles
    vehicles = scenario.vehicle_set.all()
    vehicle_soc = dict()  # store current soc of vehicles
    for vehicle in vehicles:
        first_vehicle_event = events.filter(vehicle=vehicle).first()
        if first_vehicle_event is not None:
            vehicle_soc[vehicle.id] = first_vehicle_event.soc_start

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
        # create arrival event
        event_list.append(
            {
                "signal_time": scenario_start_time.isoformat(),
                "start_time": event.time_start.isoformat(),
                "vehicle_id": event.vehicle.name,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": event.station.name,
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
                "vehicle_id": event.vehicle.name,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": None,
                },
            }
        )

        # update SoC
        vehicle_soc[event.vehicle_id] = event.soc_end

    return event_list


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
        },
        max_depth=1,
    )
    return copied_instance, stack


@atomic()
def create_stations_for_map(django_scenario: Scenario):
    stations = ebustoolbox.models.Station.objects.filter(scenario=django_scenario)
    warned = False
    stations_with_geo = []
    for station in stations:
        if station.geom is None:
            if not warned:
                warnings.warn("At least one Station has no geometry and is placed randomly")
                warned = True
            station.geom = Point(x=13.0 + random.random(), y=52.0 + random.random(), z=0)
            stations_with_geo.append(station)
    Station.objects.bulk_update(stations_with_geo, ["geom"])


def create_empty_child_scenario(parent_scenario: Scenario, task_id):
    new_child_scenario = Scenario.objects.create(task_id=task_id)
    new_child_scenario.manager = parent_scenario.manager
    new_child_scenario.name = parent_scenario.name
    new_child_scenario.parent = parent_scenario
    new_child_scenario.save()
    return new_child_scenario


@atomic()
def create_scenario_copy_for_user(mutation_scenario: Scenario):
    assert isinstance(mutation_scenario, Scenario)
    assert mutation_scenario.parent is not None
    assert mutation_scenario.parent.parent is None
    mutation_scenario.task_id = ebustoolbox.util.get_unique_task_id()
    copied_scenario, stack = deepcopy_scenario(mutation_scenario)
    vehicle_type_selections = VehicleTypeSelection.objects.filter(
        vehicle_type__scenario=mutation_scenario
    )
    for vts in vehicle_type_selections:
        new_vt_id = stack[VehicleType][vts.vehicle_type.id]
        vts.id = None
        vts.vehicle_type = VehicleType.objects.get(id=new_vt_id)
        vts.save()

    vehicle_type_mutation = VehicleTypeMutation.objects.filter(
        mutated_vehicle_type__scenario=mutation_scenario
    )
    for vtm in vehicle_type_mutation:
        new_vt_id = stack[VehicleType][vtm.mutated_vehicle_type.id]
        vtm.id = None
        vtm.mutated_vehicle_type = VehicleType.objects.get(id=new_vt_id)
        vtm.save()

    # ToDo Expand with Depot and Station Mutation if such settings will be introduced
    #
    # class DepotMutation(models.Model):
    #     original_depot = models.ForeignKey(
    #         Depot, related_name="originaldepot", null=True, on_delete=models.CASCADE
    #     )
    #     mutated_original_depot = models.ForeignKey(
    #         Depot, related_name="mutateddepot", null=True, on_delete=models.CASCADE
    #     )
    #
    #
    # class StationMutation(models.Model):
    #     original_station = models.ForeignKey(
    #         Station, related_name="originalstation", null=True, on_delete=models.CASCADE
    #     )
    #     mutated_original_station = models.ForeignKey(
    #         Station, related_name="mutatedstation", null=True, on_delete=models.CASCADE
    #     )

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

    depot_selection = DepotSelection.objects.get(scenario=mutation)
    # These depots were selected to remain
    original_depot_ids = depot_selection.depots.all().values_list("id", flat=True)
    copied_depot_ids = [stack[Station][org_id] for org_id in original_depot_ids]
    all_depots = Station.objects.filter(scenario=child, charge_type=EnumChargeType.DEPOT)
    depots_to_remove = all_depots.exclude(id__in=copied_depot_ids)
    trim_depots(child, depots_to_remove)

    ele_option = ElectrificationOptions.objects.get(scenario=mutation)
    ele_dict = model_to_dict(ele_option)
    del ele_dict["id"]
    del ele_dict["scenario"]
    del ele_dict["electrified_stations"]

    child.simba_options.update(ele_dict)
    if ele_option.station_optimization:
        child.simba_options["modes"] = "sim,station_optimization,report"
    else:
        child.simba_options["modes"] = "sim,report"

    org_ele_station_ids = ele_option.electrified_stations.all().values_list("id", flat=True)
    copied_ele_station_ids = [stack[Station][org_id] for org_id in org_ele_station_ids]
    electrify_db_stations(child, copied_ele_station_ids)
    for station in Station.objects.filter(scenario=mutation).exclude(id__in=copied_ele_station_ids):
        station.is_electrified = False
        station.save()

    vehicle_type_mutations = VehicleTypeMutation.objects.filter(
        original_vehicle_type__scenario=parent_scenario, mutated_vehicle_type__scenario=mutation
    )
    vt_mut_list = vehicle_type_mutations.values_list("original_vehicle_type", flat=True)
    assert len(vt_mut_list) == len({vt for vt in vt_mut_list})
    vt_mut_list = vehicle_type_mutations.values_list("mutated_vehicle_type", flat=True)
    assert len(vt_mut_list) == len({vt for vt in vt_mut_list})
    assert len(vt_mut_list) == VehicleType.objects.filter(scenario=mutation).count()

    for vt_mut in vehicle_type_mutations:
        org_vt = vt_mut.original_vehicle_type
        vt = vt_mut.mutated_vehicle_type
        copied_vt_id = stack[VehicleType][org_vt.id]
        vt.id = copied_vt_id
        vt.scenario = child
        vt.save()
    child.save()
    return child


@shared_task(bind=True)
def _run_ebus_toolchain(self, task_id):
    """Run the tool chain"""
    db_scenario = Scenario.objects.get(task_id=task_id)
    progress, _ = Progress.objects.get_or_create(task_id=self.request.id, scenario=db_scenario)
    progress.reset()

    try:
        logger.info(f"Getting schedule from db {datetime.now()}")
        schedule, args = get_schedule_from_db(db_scenario)

        # in the first run Depots can stay un electrified
        # ToDo keep that?
        for depot in Depot.objects.filter(scenario=db_scenario):
            try:
                del schedule.stations[depot.station.to_simba_name()]
            except KeyError:
                pass
        progress.total_work = 100
        progress.current_work = 0
        progress.save()

        # call simba and eflips
        try:
            wanted_modes = args.modes.split(",")
        except AttributeError:
            wanted_modes = args.mode
        assert wanted_modes[-1] == "report"
        simba_scenario = None
        # Chain of modes with mode->eflips -> sim. Last mode is "report" and can be outside of loop
        for mode in wanted_modes[:-1]:
            # Delete old events
            Event.objects.filter(scenario=db_scenario).delete()

            schedule, simba_scenario = run_simba(
                schedule, args, db_scenario, mode=mode, scenario=simba_scenario
            )

            # Event.objects.filter(scenario=db_scenario).order_by("soc_end").first().soc_end
            run_eflips(task_id)
            eflips_assignment = get_assigned_vehicles(task_id)
            schedule.assign_vehicles_custom(eflips_assignment)
            # ToDo: Keep that?
            electrify_depot_station_w_default(db_scenario)
            #
            # get electrified stations from db, e.g. depot station from eflips with
            # power
            stations_dict = get_electrified_stations_from_db(db_scenario)
            schedule.stations = stations_dict.copy()
            schedule, simba_scenario = run_simba(schedule, args, db_scenario, mode="sim")

            progress.current_work += 90 // (len(wanted_modes) - 1)
            progress.save()

        db_scenario.refresh_from_db()
        db_scenario.finished = timezone.now()
        db_scenario.save()
        progress.set_success()
    except Exception as e:
        logger.error(traceback.format_exc())
        progress.refresh_from_db()
        try:
            progress.errors.append(str(e))
        except Exception:
            logger.error(traceback.format_exc())
        progress.set_failed()


def electrify_depot_station_w_default(db_scenario):
    for depot in Depot.objects.filter(scenario=db_scenario):
        station = depot.station
        if station.is_electrified:
            continue
        # ToDo get defaults from somewhere
        station.is_electrified = True
        station.power_total = station.power_total or 1000_000
        station.amount_charging_places = station.amount_charging_places or 1000
        station.power_per_charger = station.power_per_charger or 150
        station.charge_type = EnumChargeType.DEPOT.value
        station.voltage_level = station.voltage_level or EnumVoltageLevel.VOLTAGE_MV.value
        station.save()


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
        vehicle = rot.vehicle
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


def run_simba(
    schedule: SimbaSchedule, args, db_scenario, mode=None, scenario=None
) -> (SimbaSchedule, "SimbaScenario"):
    logger.info(f"Running Simba {datetime.now()} with mode {mode}")
    # TODO don't overwrite output on multiple function calls
    task_id = db_scenario.task_id
    args.output_directory = Path(settings.UPLOAD_PATH) / str(task_id)
    args.attach_vehicle_soc = True

    # Default mode is greedy simulation
    if mode is None or mode == "sim":
        mode = "sim_greedy"

    func = getattr(simba.simulate.Mode, mode)
    # Run this mode. Iteration number is not changed right now since only the last report is
    # used from the generated simba files
    schedule, scenario = func(schedule, scenario, args, 1)
    match mode:
        case "sim_greedy" | "report":
            pass
        case w if w in ["station_optimization", "station_optimization_single_step"]:
            update_electrified_stations_db(schedule.stations, db_scenario)
        case _:
            raise NotImplementedError

    logger.info(f"Creating Simba Events {datetime.now()}")
    create_event_output(scenario, db_scenario)
    logger.info(f"Simba Events Created {datetime.now()}")
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
    # ToDo Replace with logger
    logger.info(f"Running eflips {datetime.now()}")
    db_scenario = Scenario.objects.get(task_id=task_id)

    # Constructing the database URL manually
    db_url = create_db_url()
    generate_depot_layout(
        db_scenario, database_url=db_url, charging_power=90, delete_existing_depot=True
    )

    # calculate total scenario time for eflips repetition period
    last_trip_time = Trip.objects.filter(scenario=db_scenario).aggregate(Max("arrival_time"))
    first_trip_time = Trip.objects.filter(scenario=db_scenario).aggregate(Min("departure_time"))
    period = last_trip_time["arrival_time__max"] - first_trip_time["departure_time__min"]
    simulate_scenario(db_scenario, database_url=db_url, repetition_period=period)


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


def is_consistent_rotation(rotation: Rotation) -> bool:
    trips = list(Trip.objects.filter(rotation=rotation).order_by("departure_time"))
    for trip in trips:
        if trip.arrival_time <= trip.departure_time:
            logger.error("A trip must have a duration.")
            return False

    if len(trips) < 2:
        return True
    trip = trips[0]
    for next_trip in trips[1:]:
        if trip.arrival_time > next_trip.departure_time:
            logger.error("A trip arrives after the departure of the next trip.")
            return False
        trip = next_trip

    assert trips[0].route.departure_station.charge_type == EnumChargeType.DEPOT
    assert trips[-1].route.arrival_station.charge_type == EnumChargeType.DEPOT

    return True


def is_consistent(scenario: Scenario) -> bool:
    for rotation in Rotation.objects.filter(scenario=scenario):
        is_consistent_rotation(rotation)

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


def create_event_output(simba_scenario: "SimbaScenario", db_scenario):  # noqa: C901
    # collect data from DB
    # Delete old simba events
    Event.objects.filter(
        scenario=db_scenario,
        event_type__in=[
            EventType.CHARGING_OPPORTUNITY,
            EventType.DRIVING,
            EventType.STANDBY_DEPARTURE,
        ],
    ).delete()

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
        key=lambda e: (e.vehicle_id, e.start_time, ["arrival", "departure"].index(e.event_type)),
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
            # Do not save events passed their rotation time. This is done by eflips
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
            logger.warn("None Values found in timeseries")
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
        stations, ["is_electrified", "charge_type", "voltage_level", "amount_charging_places"]
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
            ["is_electrified", "charge_type", "voltage_level", "amount_charging_places"],
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
