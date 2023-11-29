import collections
import json
from copy import deepcopy, copy
from argparse import Namespace
from typing import List

from django.utils import timezone
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Max
from django.conf import settings
from django.utils.timezone import make_aware
from django.http import HttpRequest

import csv
import shutil
import tqdm
import traceback
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from celery import shared_task


from .models import (
    Vehicle,
    VehicleProperties,
    UploadedFile,
    Station,
    VehicleType,
    Rotation,
    Trip,
    Scenario,
    EnumChargeType,
    Line,
)

from simba.consumption import Consumption
import simba.optimizer_util
import simba.util
import simba.simulate
from django.db.transaction import atomic
from simba.rotation import Rotation as SimbaRotation
from simba.schedule import Schedule as SimbaSchedule

if settings.EFLIPS_USE:
    import eflips.depot.api.django_simba.input as eflips_api
    from eflips.depot.api.django_simba.input import VehicleType as EflipsVehicleType
    from eflips.depot.api import init_simulation, run_simulation
    from eflips.depot.api.django_simba.output import to_simba, InputForSimba

# ToDo: Any better solutions?
INTEGER_INF = 9999


@atomic()
def input_files_to_database(cleaned_data: dict, request: HttpRequest):
    """Fill the database with the inputs from the form

    :param cleaned_data: cleaned data
    :param request: Request with uploaded files
    :return:
    """
    django_scenario = scenario_to_db(cleaned_data, request)
    original_args = get_args(django_scenario)
    # Create the schedule from the args, and delete features which are not used in django
    simba_schedule, new_args = get_schedule_from_args(original_args)

    # Write the station geodata and electrified stations to DB
    stations_to_db(simba_schedule.station_data, simba_schedule.stations, django_scenario)

    # Write the vehicle types to DB
    vehicles_to_db(simba_schedule.vehicle_types, django_scenario)

    # Write the schedule including rotations and trips to the DB
    schedule_to_db(simba_schedule, django_scenario)

    return django_scenario, simba_schedule, original_args


def get_schedule_from_db(django_scenario: Scenario) -> tuple[simba.schedule.Schedule, Namespace]:
    """Takes a django Scenario and returns the simba Schedule and arguments

    Can be used to run a previously stored Django Scenario again straight from the database without
    using files, by returning schedule and args.

    :param django_scenario: Scenario
    :type django_scenario: .models.Scenario
    :return: (simba Schedule, args)
    :rtype: (simba.schedule.Schedule, Namespace)
    """
    # get SimBa station_data
    station_data = get_station_data_from_db(django_scenario)

    # get SimBA electrified stations from db
    stations_dict = get_electrified_stations_from_db(django_scenario)

    # get SimBA vehicle_types from db
    vehicle_types = get_vehicle_types_from_db(django_scenario)

    # ToDo this might need refactoring since binding consumption to Trip Class is not versatile
    # in case of parallel schedules / scenarios, since both access the same Consumption
    # setup consumption calculator that can be accessed by all trips
    simba.trip.Trip.consumption = Consumption(vehicle_types)

    options = copy(django_scenario.simba_options)

    del options["electrified_stations"]
    del options["vehicle_types"]

    schedule = SimbaSchedule(stations=stations_dict, vehicle_types=vehicle_types, **options)
    schedule.station_data = station_data

    # get SimBA rotations and trips from db
    rotations = get_rotations_and_trips_from_db(django_scenario, schedule, station_data)

    schedule.rotations = rotations
    schedule.original_rotations = deepcopy(rotations)

    args = get_args(django_scenario=django_scenario)
    # filter rotations
    schedule.rotation_filter(args)

    # calculate consumption of all trips
    schedule.calculate_consumption()

    # Create soc dispatcher
    schedule.init_soc_dispatcher(args)

    return schedule, args


def get_rotations_and_trips_from_db(django_scenario, schedule, station_data) -> dict:
    """Create simba rotations with trips from a database with a scenario as a key

    :param django_scenario: Django scenario
    :param schedule: SimBA Schedule
    :param station_data: dictionary with all stations and elevation
    :return: rotations
    :rtype: dict
    """
    rotations = {}
    lines_dict = {line.id: line for line in Line.objects.filter(scenario=django_scenario)}

    for rot in Rotation.objects.filter(scenario=django_scenario):
        vehicle_type = rot.vehicle.vehicle_type.name_short
        simba_rotation = SimbaRotation(id=rot.name, vehicle_type=vehicle_type, schedule=schedule)
        simba_rotation.charging_type = (
            EnumChargeType.OPPORTUNITY if rot.allow_opportunity_charging else EnumChargeType.DEPOT
        )

        rotations[rot.name] = simba_rotation
        for trip in Trip.objects.filter(rotation=rot):
            simba_trip_dict = {
                "departure_time": str(trip.departure_time),
                "departure_name": trip.departure_station.name,
                "arrival_time": str(trip.arrival_time),
                "arrival_name": trip.arrival_station.name,
                "distance": trip.distance,
                "line": lines_dict[trip.line.id].name,
                "temperature": trip.temperature,
                "height_diff": (
                    station_data[trip.arrival_station.name]["elevation"]
                    - station_data[trip.departure_station.name]["elevation"]
                ),
                "level_of_loading": trip.level_of_loading,
                "mean_speed": trip.speed * 3.6,
            }
            simba_rotation.add_trip(simba_trip_dict)
    return rotations


def get_vehicle_types_from_db(django_scenario) -> dict:
    """Create simba rotations with trips from database with scenario as key

    :param django_scenario: Django scenario
    :return: vehicle_types
    :rtype: dict
    """
    vehicle_types = dict()
    for vehicle_type in VehicleType.objects.filter(scenario=django_scenario):
        charge_type = (
            EnumChargeType.OPPORTUNITY
            if vehicle_type.opportunity_charging_capable
            else EnumChargeType.DEPOT
        )
        try:
            vehicle_types[vehicle_type.name_short]
        except KeyError:
            vehicle_types[vehicle_type.name_short] = dict()
        vehicle_types[vehicle_type.name_short][charge_type] = {
            "name": vehicle_type.name,
            "capacity": vehicle_type.battery_capacity,
            "charging_curve": vehicle_type.charging_curve,
            "min_charging_power": vehicle_type.minimum_charging_power,
            "v2g": (vehicle_type.v2g_curve is not None),
            # ToDo use vehicle to grid curve
            # vehicle_to_grid_curve ....
            "mileage": vehicle_type.consumption,
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
    for station in Station.objects.filter(scenario=django_scenario):
        if not station.is_electrified:
            continue
        stat_dict = {
            "type": station.charge_type.lower(),
            "n_charging_stations": station.amount_charging_places,
            "cs_power_deps_oppb": station.power_per_charger,
            "cs_power_deps_depb": station.power_per_charger,
            "cs_power_opps": station.power_per_charger,
            "gc_power": station.power_total,
            "voltage_level": station.voltage_level,
        }
        stations_dict[station.name] = stat_dict
    return stations_dict


def get_station_data_from_db(django_scenario) -> dict:
    """Create station_data from database with scenario as key

    :param django_scenario: Django scenario
    :return: station_data
    :rtype: dict
    """
    station_data = dict()
    for station in Station.objects.filter(scenario=django_scenario):
        station_data[station.name] = {
            "long": station.geom.x,
            "lat": station.geom.y,
            "elevation": station.geom.z,
        }

    return station_data


def get_schedule_from_args(original_args) -> tuple[simba.schedule.Schedule, Namespace]:
    """Create SimBA Schedule from arguments with existing files and restrict features.

    This creates a schedule object without making use of the database. Instead the passed arguments
    and files are used.
    :param django_scenario: Django scenario
    :return: simba schedule and args
    :rtype: simba.schedule.Schedule, Namespace
    """
    simba_schedule, new_args = simba.simulate.pre_simulation(original_args)
    # Remove simba features which are not used
    remove_station_attributes = ["battery", "energy_feed_in", "distance_to_grid", "external_load"]
    for attribute in remove_station_attributes:
        stations_copy = deepcopy(simba_schedule.stations)
        for key, station in stations_copy.items():
            if attribute in station:
                del simba_schedule.stations[key][attribute]

    # Mutate values according to models and make values explicit, i.e. dictionary contains all
    # values, even if defaults would be used anyway. This makes sure the database contains all
    # the needed information to recreate the exact scenario.
    # ToDo: Another way could be to use properties in the models which look up scenario provided
    # data if no specific data (e.g. station.cs_power) is provided.
    for station in simba_schedule.stations.values():
        if str(station["type"]).lower() != "opps":
            try:
                station["cs_power_deps_depb"] = station["cs_power_opps"] = station[
                    "cs_power_deps_oppb"
                ]
            except KeyError:
                station["cs_power_deps_depb"] = station["cs_power_opps"] = station[
                    "cs_power_deps_oppb"
                ] = original_args.cs_power_deps_oppb
        else:
            try:
                station["cs_power_opps"] = station["cs_power_opps"]
            except KeyError:
                station["cs_power_opps"] = original_args.cs_power_opps
                station["cs_power_deps_depb"] = station["cs_power_deps_oppb"] = station[
                    "cs_power_opps"
                ]

        try:
            station["voltage_level"] = station["voltage_level"]
        except KeyError:
            station["voltage_level"] = original_args.default_voltage_level

        try:
            station["gc_power"] = station["gc_power"]
        except KeyError:
            station["gc_power"] = vars(original_args).get(
                "gc_power_" + str(station["type"]).lower()
            )

    return simba_schedule, new_args


def get_args(django_scenario) -> Namespace:
    """Creates arguments from django Scenario

    Creates arguments for SimBA by getting default arguments from simba, updating them with
    the options from the django_scenario and

    :param django_scenario: Scenario in the django database
    :type django_scenario: models.Scenario
    :return:
    """
    # Get parser from SimBA
    parser = simba.util.get_parser()
    # Read the parse values, in this case the default values
    args, _ = parser.parse_known_args()
    # Overwrite args with scenario specific data
    vars(args).update(vars(Namespace(**django_scenario.simba_options)))
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
        "input_schedule": "trips_example.csv",
        "electrified_stations": "electrified_stations.json",
        "vehicle_types": "vehicle_types.json",
        "station_data_path": "all_stations.csv",
        "outside_temperature_over_day_path": "default_temp_summer.csv",
        "level_of_loading_over_day_path": "default_level_of_loading_over_day.csv",
        "cost_parameters_file": "cost_params.json",
    }.items():
        if args[k]:
            # uploaded file: store in upload folder
            f = UploadedFile.objects.create(scenario=scenario, file=request.FILES[k])
            args[k] = f.file.path
            continue
        p = Path(settings.STATIC_URL, __package__, "examples", v)
        if settings.DEBUG:
            # use app static folder
            if p.is_absolute():
                # remove first slash
                p = Path(str(p)[1:])
            p = Path(settings.BASE_DIR, __package__, p)
        if not p.exists():
            print(f"FILE ERROR: {k} COULD NOT BE SET ({str(p)})")
            continue
        args[k] = str(p)
    scenario.simba_options = args
    scenario.save()

    return scenario


def schedule_to_db(schedule: simba.schedule.Schedule, django_scenario: Scenario) -> None:
    """Takes a simba Schedule and writes it into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    model_rotations = []
    model_trips = []
    model_lines = []
    rot_id = 1 if Rotation.objects.last() is None else Rotation.objects.last().id + 1
    trip_id = 1 if Trip.objects.last() is None else Trip.objects.last().id + 1

    station_dict = Station.objects.filter(scenario=django_scenario)
    station_dict = {station.name: station for station in station_dict}
    line_dict = {}
    for key, rot in tqdm.tqdm(schedule.rotations.items(), total=len(schedule.rotations)):
        opportunity_charging_capable = rot.charging_type == "oppb"
        vehicletype = VehicleType.objects.get(
            scenario=django_scenario,
            name_short=rot.vehicle_type,
            opportunity_charging_capable=opportunity_charging_capable,
        )
        # ToDo Replace dummy vehicles with properly generated vehicles from SimBA or eFlips
        vehicle = Vehicle.objects.create(
            vehicle_type=vehicletype, scenario=django_scenario, name="Placeholder Vehicle"
        )
        r = Rotation(
            name=key,
            scenario=django_scenario,
            allow_opportunity_charging=opportunity_charging_capable,
            vehicle=vehicle,
        )
        r.id = rot_id
        rot_id += 1
        model_rotations.append(r)
        for trip in rot.trips:
            # Get the proper Line
            if trip.line in line_dict:
                line = line_dict[trip.line]
            else:
                line = Line(scenario=django_scenario, name=trip.line)
                line_dict[trip.line] = line
                model_lines.append(line)
            t = Trip(
                rotation=r,
                scenario=django_scenario,
                departure_station=station_dict[trip.departure_name],
                departure_time=make_aware(trip.departure_time),
                arrival_station=station_dict[trip.arrival_name],
                arrival_time=make_aware(trip.arrival_time),
                distance=trip.distance,
                line=line,
                temperature=trip.temperature,
                level_of_loading=trip.level_of_loading,
            )
            t.id = trip_id
            model_trips.append(t)
            trip_id += 1
    Line.objects.bulk_create(model_lines)
    Rotation.objects.bulk_create(model_rotations)
    Trip.objects.bulk_create(model_trips)


def vehicles_to_db(vehicle_types: dict, scenario: Scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    for name, v_type in vehicle_types.items():
        for charge_name, charge_type in v_type.items():
            consumption = float(charge_type.get("mileage"))
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
                length_m=float(charge_type.get("length", 0)),
            )
            VehicleType.objects.create(**params)


def stations_to_db(station_data, electrified_stations, scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    object_list = []
    try:
        last_id = Station.objects.aggregate(Max("id"))["id__max"]
        if last_id is None:
            last_id = -1
    except Exception:
        last_id = -1
    for key, station in station_data.items():
        last_id += 1
        try:
            long = float(station["long"])
            lat = float(station["lat"])
            elevation = float(station["elevation"])
            geom = GEOSGeometry(f"POINT({long} {lat} {elevation})")
            params = dict(id=last_id, scenario=scenario, geom=geom, name=str(key))
            object_list.append(Station(**params))
        except Exception:
            print(traceback.format_exc())
            pass
    Station.objects.bulk_create(object_list)

    for name, ele_station in electrified_stations.items():
        station = Station.objects.get(name=name, scenario=scenario)
        station.is_electrified = True
        station.charge_type = ele_station.get("type")

        station.voltage_level = ele_station.get(
            "voltage_level", scenario.simba_options.get("default_voltage_level")
        )
        station.amount_charging_places = ele_station.get("n_charging_stations")
        # ToDo how do we handle differences in charging power depending on oppb or depb
        if station.charge_type == "opps":
            power_per_charger = ele_station.get("cs_power_opps")
        else:
            power_per_charger = ele_station.get("cs_power_deps_oppb")
        station.power_per_charger = power_per_charger
        station.power_total = ele_station.get(
            "gc_power", scenario.simba_options.get("gc_power_" + station.charge_type)
        )
        station.save()


def generate_zipped_scenario(task_id):
    if settings.CELERY_USE:
        print("Using Celery")
        _celery_generate_zipped_scenario.apply_async((str(task_id),), task_id=task_id)
    else:
        _generate_zipped_scenario(task_id)


def _generate_zipped_scenario(task_id: str):
    folder_path = Path(settings.UPLOAD_PATH, task_id)
    output_path = settings.MEDIA_ROOT / (task_id + ".zip")
    if not folder_path.exists():
        print("input folder for zipping not found")
        return
    if output_path.is_file():
        print("Zip already exists")
        return
    shutil.make_archive(output_path.with_suffix(""), "zip", folder_path)


@shared_task(bind=True)
def _celery_generate_zipped_scenario(self, task_id: str):
    _generate_zipped_scenario(task_id)


def run_ebus_toolchain(schedule: simba.schedule.Schedule, args, task_id):
    if settings.CELERY_USE:
        print("Using Celery")
        args_dict = vars(args)
        _ = _celery_run_ebus_toolchain.apply_async((args_dict, str(task_id)), task_id=task_id)
    else:
        _run_ebus_toolchain(schedule, args, task_id)


@shared_task(bind=True)
def _celery_run_ebus_toolchain(self, args, task_id):
    args = Namespace(**args)
    schedule, args = get_schedule_from_args(args)
    _run_ebus_toolchain(schedule, args, task_id)


def vary_depot_rotations(schedule) -> "collections.Iterable[SimbaRotation]":
    """Generator that creates schedules with varying vehicle types for"""
    # Keep original rotations to restore them later and keep track of depot rotations
    orig_rotations = deepcopy(schedule.rotations)
    # depot rotations
    depot_rotations = {
        r_id: rotation
        for r_id, rotation in orig_rotations.items()
        if rotation.charging_type == "depb"
    }
    for rot_id, rotation in depot_rotations.items():
        vt = rotation.vehicle_type
        # Iterate over both charging types of this vehicle type, e.g., depot and opp bus.
        for charging_type in EnumChargeType.values:
            # Skip rotation with a vehicle type / charging type combination, if it does not exist
            try:
                schedule.vehicle_types[vt][charging_type]
            except KeyError:
                continue
            # in case of a depot rotation, the vehicle type is adjusted and both
            # charging types are used, even the "oppb". This way calculate_consumption() also
            # calculates the "non-charging" consumption of a depot rotation which is run with
            # an opportunity bus.
            if orig_rotations[rot_id].charging_type == EnumChargeType.DEPOT:
                # Charging type is mutated, since this is used to determine the exact vehicle
                schedule.rotations[rot_id].charging_type = charging_type
                schedule.rotations[rot_id].vehicle_type = vt
                yield schedule.rotations[rot_id]
    # Restore rotations before leaving generator
    schedule.rotations = orig_rotations


def _run_ebus_toolchain(schedule: "simba.schedule.Schedule", args, task_id):
    """Run the tool chain"""
    # set report dir for first iteration
    args.output_directory = Path(settings.UPLOAD_PATH) / task_id
    report_dir = Path(settings.BASE_DIR, args.output_directory, "report_1")
    # call simba and eflips
    run_simba(schedule, args, task_id, report_dir=report_dir)

    if settings.EFLIPS_USE:
        eflips_dataclass_list: List[InputForSimba] = run_eflips(report_dir, task_id)

        # set report dir for second iteration/final results
        # report_dir = Path(settings.BASE_DIR, args.output_directory, "report_2")
        # TODO: currently report_directory is set in simba internally and is always report_1 for current purposes
        # (number changes by the amount of reports in the same fun of SimBA)
        # call simba with eflips results
        run_simba(
            schedule, args, task_id, report_dir=report_dir, eflips_input=eflips_dataclass_list
        )


def run_simba(
    schedule: "SimbaSchedule",
    args,
    task_id,
    report_dir=Path(".", "report"),
    eflips_input: List[InputForSimba] | None = None,
):
    # TODO don't overwrite output on multiple function calls
    args.attach_vehicle_soc = True

    db_scenario = Scenario.objects.get(task_id=task_id)

    # By setting charging power for depot buses to zero, we make sure every rotation will generate
    # a new bus
    args.cs_power_depbs_depb = 0
    args.cs_power_deps_oppb = 0
    for key, station in schedule.stations.items():
        schedule.stations[key]["cs_power_deps_depb"] = 0
        schedule.stations[key]["cs_power_deps_oppb"] = 0

    if eflips_input is not None:
        # TODO same for vehicle types (use short name)
        for obj in eflips_input:
            # SimBA doesn't work with the DB IDs, instead it needs the object names
            rotation = Rotation.objects.get(id=obj.rotation_id)
            obj.rotation_id = rotation.name
            vehicle_type = VehicleType.objects.get(id=obj.vehicle_type_id)
            obj.vehicle_type_id = vehicle_type.name_short
            obj.vehicle_type_id += "_oppb" if vehicle_type.opportunity_charging_capable else "_depb"
            v_id = obj.vehicle_id.split(" ")[1]
            obj.vehicle_id = f"{obj.vehicle_type_id}_{v_id}"

        schedule.assign_vehicles_for_django(eflips_input)

    scenario = schedule.run(args)

    def dict_creator():
        return dict(
            departure_soc=None,
            vehicle_type=[],
            delta_soc=[],
            arrival_soc=None,
            minimal_soc=None,
            charging_type=None,
        )

    # initialize eflips input
    input_for_eflips = {
        Rotation.objects.get(scenario=db_scenario, name=rot_id).id: dict_creator()
        for rot_id in schedule.rotations
    }

    # Analyze schedules which are generated using different depot vehicles. I.e. every depot
    # rotation is run with each vehicle to generate the consumption
    for rotation in vary_depot_rotations(schedule):
        rotation.calculate_consumption()
        db_rotation = Rotation.objects.get(scenario=db_scenario, name=rotation.id)
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
        input_for_eflips[db_rotation.id]["delta_soc"].append(
            rotation.consumption / vehicle["capacity"]
        )

    for rot_id, rotation in schedule.rotations.items():
        if rotation.charging_type != "oppb":
            continue
        db_rotation = Rotation.objects.get(scenario=db_scenario, name=rot_id)
        v_soc, start, end = simba.optimizer_util.get_rotation_soc_util(
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
    schedule, scenario = simba.simulate.modes_simulation(schedule, scenario, args)

    db_scenario.finished = timezone.now()
    db_scenario.save()
    # Create the file for eflips. This could be passed directly to eFlips by returning eflips_input
    with open(Path(report_dir, "eflips_input.json"), "w") as f:
        json.dump(input_for_eflips, f, indent=4)

    file_path = Path(report_dir, "vehicle_socs.csv")
    save_vehicle_properties_from_file(file_path, db_scenario)


def run_eflips(report_dir, task_id):
    eflips_input_path = Path(report_dir, "eflips_input.json")
    db_scenario = Scenario.objects.get(task_id=task_id)
    # START eFLIPS API CALL
    vehicle_schedule_list = eflips_api.VehicleSchedule.from_rotations(eflips_input_path)

    # Get the Vehicle Types
    vehicle_types = []
    for djangosimba_vehicle_type in VehicleType.objects.filter(scenario=db_scenario):
        vehicle_type = EflipsVehicleType(djangosimba_vehicle_type)
        vehicle_types.append(vehicle_type)

    # Initialize the simulation
    simulation_host = init_simulation(vehicle_types, vehicle_schedule_list)

    # Run the simulation the first time to find exact vehicle counts
    depot_evaluation = run_simulation(simulation_host)

    # Run the simulation the second time to get the results
    vehicle_counts = depot_evaluation.nvehicles_used_calculation()
    simulation_host = init_simulation(vehicle_types, vehicle_schedule_list, vehicle_counts)
    depot_evaluation = run_simulation(simulation_host)

    # Save a plot to the report_dir
    depot_evaluation.path_results = report_dir

    depot_evaluation.vehicle_periods(
        periods={
            "depot general": "darkgray",
            "park": "lightgray",
            "Arrival Cleaning": "steelblue",
            "Charging": "forestgreen",
            "Standby Pre-departure": "darkblue",
            "precondition": "black",
            "trip": "wheat",
        },
        save=True,
        show=False,
        formats=("png",),
        show_total_power=True,
        show_annotates=True,
    )

    # Save the results to a folder
    output_for_simba = to_simba(depot_evaluation)
    return output_for_simba
    # with open(eflips_input_path.parent / "output_for_simba.json", "w") as f:
    #     json.dump([dataclasses.asdict(o) for o in output_for_simba], f, indent=4)


def save_vehicle_properties_from_file(file_path, scenario):
    """Placeholder functionality to save data for plotting"""

    object_list = []
    try:
        last_id = VehicleProperties.objects.last().id
    except Exception:
        last_id = -1
    with open(file_path, "r") as csvfile:
        reader = csv.DictReader(csvfile)
        vehicle_names = [name for name in reader.fieldnames]
        vehicle_names.remove("time")
        vehicle_names.remove("timestep")
        vehicle_dict = dict()

        # ToDo: Vehicles are not properly assigned. Since this is only a "dummy" plotting feature
        # that is ok.
        vehicles = Vehicle.objects.filter(vehicle_type__scenario=scenario)

        for i, vehicle_name in enumerate(vehicle_names):
            vehicle = vehicles[i]
            vehicle.name = vehicle_name
            vehicle.save()
            vehicle_dict[vehicle_name] = vehicle

        for row in reader:
            dt = datetime.fromisoformat(row["time"])
            aware_datetime = make_aware(dt)
            for vehicle_name in vehicle_names:
                try:
                    soc = float(row[vehicle_name])
                except (KeyError, ValueError):
                    soc = None
                object_list.append(
                    VehicleProperties(
                        date=aware_datetime,
                        soc=soc,
                        vehicle=vehicle_dict[vehicle_name],
                        scenario=scenario,
                        id=last_id + 1,
                    )
                )
                last_id += 1
    VehicleProperties.objects.bulk_create(object_list)
