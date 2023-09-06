import collections
import json
from copy import deepcopy, copy
from argparse import Namespace

from django.utils import timezone
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Max
from django.conf import settings
from django.utils.timezone import make_aware
from django.http import HttpRequest

import csv
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from celery import shared_task

from simba.consumption import Consumption
from .models import Vehicle, VehicleProperties, UploadedFile, Station, VehicleType, VehicleClass, \
    Rotation, Trip
from .models import Scenario

import simba.optimizer_util
import simba.util
import simba.simulate
import simba.schedule
from simba.rotation import Rotation as SimbaRotation
from simba.schedule import Schedule as SimbaSchedule

# ToDo: Any better solutions?
INTEGER_INF = 9999


def fill_db_with_input_files(cleaned_data: dict, request: HttpRequest):
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
    stations_to_db(simba_schedule.station_data,
                   simba_schedule.stations,
                   django_scenario)

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

    options = copy(django_scenario.options)

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
    """Create simba rotations with trips from database with scenario as key

    :param django_scenario: Django scenario
    :param schedule: SimBA Schedule
    :param station_data: dictionary with all stations and elevation
    :return: rotations
    :rtype: dict
    """
    rotations = {}
    for rot in Rotation.objects.filter(scenario=django_scenario):
        vehicle_type = rot.vehicle_class.name.split("_")[0]
        charging_type = rot.vehicle_class.name.split("_")[1]
        simba_rotation = SimbaRotation(rot.name, vehicle_type, schedule)
        simba_rotation.charging_type = charging_type
        rotations[rot.name] = simba_rotation
        for trip in Trip.objects.filter(rotation=rot):
            simba_trip_dict = {
                "departure_time": str(trip.departure_time),
                "departure_name": trip.departure_stop.name,
                "arrival_time": str(trip.arrival_time),
                "arrival_name": trip.arrival_stop.name,
                "distance": trip.distance,
                "line": trip.line,
                "temperature": trip.temperature,
                "height_diff": (station_data[trip.arrival_stop.name]["elevation"] -
                                station_data[trip.departure_stop.name]["elevation"]),
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
        charge_type = "oppb" if vehicle_type.flex_charging else "depb"
        try:
            vehicle_types[vehicle_type.name_short]
        except KeyError:
            vehicle_types[vehicle_type.name_short] = dict()
        vehicle_types[vehicle_type.name_short][charge_type] = {
            "name": vehicle_type.name,
            "capacity": vehicle_type.battery_capacity,
            "charging_curve": vehicle_type.charging_curve,
            "min_charging_power": vehicle_type.minimum_charging_power,
            "v2g": vehicle_type.v2g,
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
        stat_dict = {"type": station.charge_type.lower(),
                     "n_charging_stations": station.amount_charging_places,
                     "cs_power_deps_oppb": station.power_per_charger,
                     "cs_power_deps_depb": station.power_per_charger,
                     "cs_power_opps": station.power_per_charger,
                     "gc_power": station.total_power,
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
                    "cs_power_deps_oppb"]
            except KeyError:
                station["cs_power_deps_depb"] = station["cs_power_opps"] = station[
                    "cs_power_deps_oppb"] = original_args.cs_power_deps_oppb
        else:
            try:
                station["cs_power_opps"] = station["cs_power_opps"]
            except KeyError:
                station["cs_power_opps"] = original_args.cs_power_opps
                station["cs_power_deps_depb"] = station["cs_power_deps_oppb"] = station[
                    "cs_power_opps"]

        try:
            station["voltage_level"] = station["voltage_level"]
        except KeyError:
            station["voltage_level"] = original_args.default_voltage_level

        try:
            station["gc_power"] = station["gc_power"]
        except KeyError:
            station["gc_power"] = vars(original_args).get(
                "gc_power_" + str(station["type"]).lower())

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
    vars(args).update(vars(Namespace(**django_scenario.options)))
    # arguments relevant to SpiceEV, setting automatically to reduce clutter in config
    simba.util.mutate_args_for_spiceev(args)

    return args


def scenario_to_db(cleaned_data, request) -> Scenario:
    scenario = Scenario.objects.create(name=cleaned_data["title"])
    args = dict(cleaned_data)
    args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(',')))
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
    scenario.options = args
    scenario.save()

    return scenario


def schedule_to_db(schedule: simba.schedule.Schedule, scenario: Scenario) -> None:
    """Takes a simba Schedule and writes it into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    model_rotations = []
    model_trips = []
    rot_id = 1 if Rotation.objects.last() is None else Rotation.objects.last().id + 1
    trip_id = 1 if Trip.objects.last() is None else Trip.objects.last().id + 1
    for key, rot in schedule.rotations.items():
        vehicle_class, _ = VehicleClass.objects.get_or_create(
            name=f"{rot.vehicle_type}_{rot.charging_type}")
        r = Rotation(name=key, vehicle_class=vehicle_class, scenario=scenario)
        r.id = rot_id
        rot_id += 1
        model_rotations.append(r)
        for trip in rot.trips:
            t = Trip(
                rotation=r,
                departure_stop=Station.objects.get(scenario=scenario, name=trip.departure_name),
                departure_time=make_aware(trip.departure_time),
                arrival_stop=Station.objects.get(scenario=scenario, name=trip.arrival_name),
                arrival_time=make_aware(trip.arrival_time),
                distance=trip.distance,
                line=trip.line,
                temperature=trip.temperature,
                level_of_loading=trip.level_of_loading)
            t.id = trip_id
            model_trips.append(t)
            trip_id += 1
    Rotation.objects.bulk_create(model_rotations)
    Trip.objects.bulk_create(model_trips)


def vehicles_to_db(vehicle_types: dict, scenario: Scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    for name, v_type in vehicle_types.items():
        vehicle_class, _ = VehicleClass.objects.get_or_create(name=name)
        for charge_name, charge_type in v_type.items():
            consumption = float(charge_type.get("mileage"))
            params = dict(name=charge_type.get("name", "unnamed bus"),
                          name_short=name,
                          vehicle_class=vehicle_class,
                          scenario=scenario,
                          flex_charging=(charge_name.lower() == "oppb"),
                          battery_capacity=charge_type["capacity"],
                          charging_efficiency=charge_type.get("battery_efficiency", 0.95),
                          minimum_charging_power=charge_type.get("min_charging_power"),
                          charging_curve=charge_type["charging_curve"],
                          v2g_curve=charge_type.get("v2g_curve", None),
                          v2g=charge_type.get("v2g", False),
                          consumption=consumption,
                          length=float(charge_type.get("length", 0)),
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
        last_id = Station.objects.aggregate(Max('id'))['id__max']
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
            params = dict(id=last_id, scenario=scenario,
                          geom=geom, name=str(key))
            object_list.append(Station(**params))
        except Exception:
            print(traceback.format_exc())
            pass
    Station.objects.bulk_create(object_list)

    for name, ele_station in electrified_stations.items():
        station = Station.objects.get(name=name, scenario=scenario)
        station.is_electrified = True
        station.charge_type = ele_station.get("type")

        station.voltage_level = ele_station.get("voltage_level",
                                                scenario.options.get("default_voltage_level"))
        station.amount_charging_places = ele_station.get("n_charging_stations")
        # ToDo how do we handle differences in charging power depending on oppb or depb
        if station.charge_type == "opps":
            power_per_charger = ele_station.get("cs_power_opps")
        else:
            power_per_charger = ele_station.get("cs_power_deps_oppb")
        station.power_per_charger = power_per_charger
        station.total_power = ele_station.get("gc_power", scenario.options.get(
            "gc_power_" + station.charge_type))
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
    shutil.make_archive(output_path.with_suffix(''), 'zip', folder_path)


@shared_task(bind=True)
def _celery_generate_zipped_scenario(self, task_id: str):
    _generate_zipped_scenario(task_id)


def run_ebus_toolbox(schedule: simba.schedule.Schedule, args, task_id):
    if settings.CELERY_USE:
        print("Using Celery")
        args_dict = vars(args)
        _ = _celery_run_ebus_toolbox.apply_async((args_dict, str(task_id)),
                                                 task_id=task_id)
    else:
        _run_ebus_toolbox(schedule, args, task_id)


@shared_task(bind=True)
def _celery_run_ebus_toolbox(self, args, task_id):
    args = Namespace(**args)
    schedule, args = get_schedule_from_args(args)
    _run_ebus_toolbox(schedule, args, task_id)


def vary_schedule(schedule) -> "collections.Iterable[simba.schedule.Schedule]":
    depot_vehicle_types = {key: vehicle_type["depb"] for key, vehicle_type in
                           schedule.vehicle_types.items()
                           if "depb" in vehicle_type}
    for key, vt in depot_vehicle_types.items():
        for rot_id, rotation in schedule.rotations.items():
            if rotation.charging_type == "depb":
                rotation.vehicle_type = key
        yield schedule, key


def _run_ebus_toolbox(schedule: "simba.schedule.Schedule", args, task_id):
    args.output_directory = Path(settings.UPLOAD_PATH) / task_id
    eflips_input = dict()
    args.attach_vehicle_soc = True

    db_scenario = Scenario.objects.get(task_id=task_id)

    initial_dict = dict(departure_soc=None,
                        vehicle_type=[],
                        delta_soc=[],
                        arrival_soc=None,
                        minimal_soc=None,
                        charging_type=None,
                        )

    # initialize eflips input
    eflips_input = {Rotation.objects.get(scenario=db_scenario, name=rot_id).id: initial_dict
                    for rot_id in schedule.rotations}

    # Iterate over every depot rotation and calculate the consumption for different depot charger
    # vehicles
    for schedule, key in vary_schedule(schedule):
        schedule.calculate_consumption()
        for rot_id, rotation in schedule.rotations.items():
            if rotation.charging_type != "depb":
                continue
            db_rotation = Rotation.objects.get(scenario=db_scenario, name=rot_id)
            eflips_input[db_rotation.id].update(departure_soc=schedule.min_recharge_deps_depb,
                                                charging_type=rotation.charging_type,
                                                )
            eflips_input[db_rotation.id]["vehicle_type"].append(rotation.vehicle_type)
            vehicle = schedule.vehicle_types[rotation.vehicle_type]["depb"]
            eflips_input[db_rotation.id]["delta_soc"].append(
                rotation.consumption / vehicle["capacity"])

    # For the opportunity chargers a simulation needs to be run. But first a new vehicle is assigned
    # for each rotation
    vehicle_counts = {f"{vt}_{ct}": 0 for vt, value in schedule.vehicle_types.items() for ct in
                      value}
    for rot_id, rot in schedule.rotations.items():
        vt = rot.vehicle_type
        ct = rot.charging_type
        key = f"{vt}_{ct}"
        vehicle_counts[key] += 1
        rot.vehicle_id = f"{vt}_{ct}_{vehicle_counts[key]}"
    schedule.vehicle_type_counts = vehicle_counts
    scenario = schedule._run(args)

    for rot_id, rotation in schedule.rotations.items():
        if rotation.charging_type != "oppb":
            continue
        db_rotation = Rotation.objects.get(scenario=db_scenario, name=rot_id)
        v_soc, start, end = simba.optimizer_util.get_rotation_soc_util(rot_id=rot_id,
                                                                       schedule=schedule,
                                                                       scenario=scenario)
        # Start is the first index during the rotation, with a decreased soc already, therefore
        # use the index before
        start_idx = max(start - 1, 0)
        rot_soc = v_soc[start_idx:end]
        eflips_input[db_rotation.id] = dict(departure_soc=rot_soc[0],
                                            arrival_soc=rot_soc[-1],
                                            minimal_soc=min(rot_soc),
                                            charging_type=rotation.charging_type,
                                            vehicle_type=rotation.vehicle_type,
                                            )
    # Simulate and create the output files
    _, _ = simba.simulate.modes_simulation(schedule, scenario, args)

    db_scenario.finished = timezone.now()
    db_scenario.save()

    # Create the file for eflips. This could be passed directly to eFlips
    with open(settings.BASE_DIR / args.output_directory / "report_1/eflips_input.json", "w") as f:
        json.dump(eflips_input, f, indent=4)

    # Write some of the output in the database
    # ToDo: Write in the database from the simba schedule and scenario object. Reading files
    # is not needed anymore
    file_path = settings.BASE_DIR / args.output_directory / "report_1/vehicle_socs.csv"
    save_vehicle_properties_from_file(file_path, db_scenario)


def save_vehicle_properties_from_file(file_path, scenario):
    object_list = []
    try:
        last_id = VehicleProperties.objects.last().id
    except Exception:
        last_id = -1
    with open(file_path, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        vehicle_names = [name for name in reader.fieldnames]
        vehicle_names.remove("time")
        vehicle_names.remove("timestep")
        vehicle_dict = dict()

        for vehicle_name in vehicle_names:
            vehicle, _ = Vehicle.objects.get_or_create(name=vehicle_name, scenario=scenario)
            vehicle_dict[vehicle_name] = vehicle

        for row in reader:
            dt = datetime.fromisoformat(row["time"])
            aware_datetime = make_aware(dt)
            for vehicle_name in vehicle_names:
                try:
                    soc = float(row[vehicle_name])
                except (KeyError, ValueError):
                    soc = None
                object_list.append(VehicleProperties(date=aware_datetime, soc=soc,
                                                     vehicle=vehicle_dict[vehicle_name],
                                                     scenario=scenario, id=last_id + 1))
                last_id += 1
    VehicleProperties.objects.bulk_create(object_list)
