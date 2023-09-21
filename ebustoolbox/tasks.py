import collections
import json
import warnings
from copy import deepcopy

from django.utils import timezone
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Max
from django.conf import settings
from django.utils.timezone import make_aware

import csv
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from celery import shared_task

from .models import Vehicle, VehicleProperties, UploadedFile, Station, VehicleType, VehicleClass, \
    Rotation, Trip
from .models import Scenario
from argparse import Namespace
import simba.optimizer_util
import simba.util
import simba.simulate
import simba.schedule
from django.db.transaction import atomic

# ToDo: Any better solutions?
INTEGER_INF = 9999


def fill_db_with_input_files(cleaned_data, request):
    django_scenario = scenario_to_db(cleaned_data, request)
    original_args = get_args(django_scenario)
    simba_schedule, new_args = get_schedule_from_args(original_args)

    stations_to_db(django_scenario.options["station_data_path"],
                   django_scenario.options["electrified_stations"],
                   django_scenario)
    vehicles_to_db(django_scenario.options["vehicle_types"], django_scenario)

    schedule_to_db(simba_schedule, django_scenario)

    add_classes_to_vehicle_types(django_scenario)

    return django_scenario, simba_schedule, original_args


@atomic()
def add_classes_to_vehicle_types(django_scenario):
    for c_class in VehicleClass.objects.filter(scenario=django_scenario):
        short_names = c_class.name.split(",")
        v_types = [v_type for name in short_names
                   for v_type in
                   VehicleType.objects.filter(scenario=django_scenario, name_short=name)]
        for v_type in v_types:
            v_type.vehicle_class.add(c_class)
            v_type.save()


def get_schedule_from_args(original_args):
    simba_schedule, new_args = simba.simulate.pre_simulation(original_args)
    return simba_schedule, new_args


def get_args(django_scenario):
    # Get parser from SimBA
    parser = simba.util.get_parser()
    # Read the parse values, in this case the default values
    args, _ = parser.parse_known_args()
    # Overwrite args with scenario specific data
    vars(args).update(vars(Namespace(**django_scenario.options)))
    # arguments relevant to SpiceEV, setting automatically to reduce clutter in config
    simba.util.mutate_args_for_spiceev(args)
    # If a config is provided, the config will overwrite previously parsed arguments
    simba.util.set_options_from_config(args, check=parser, verbose=False)
    return args


def scenario_to_db(cleaned_data, request):
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


def schedule_to_db(schedule, django_scenario):
    model_rotations = []
    model_trips = []
    rot_id = 1 if Rotation.objects.last() is None else Rotation.objects.last().id + 1
    trip_id = 1 if Trip.objects.last() is None else Trip.objects.last().id + 1
    for key, rot in schedule.rotations.items():
        vehicle_class, _ = VehicleClass.objects.get_or_create(name=",".join(rot.vehicle_class),
                                                              scenario=django_scenario)
        r = Rotation(name=key, vehicle_class=vehicle_class, scenario=django_scenario)
        r.id = rot_id
        rot_id += 1
        model_rotations.append(r)
        for trip in rot.trips:
            t = Trip(
                rotation=r,
                departure_stop=Station.objects.get(scenario=django_scenario,
                                                   name=trip.departure_name),
                departure_time=make_aware(trip.departure_time),
                arrival_stop=Station.objects.get(scenario=django_scenario,
                                                 name=trip.arrival_name),
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


def vehicles_to_db(file_path, scenario):
    with open(file_path, 'r') as f:
        vehicle_types = simba.util.uncomment_json_file(f)

    for name, v_type in vehicle_types.items():
        for charge_name, charge_type in v_type.items():
            consumption = float(charge_type.get("mileage"))
            params = dict(name=charge_type.get("name", "unnamed bus"),
                          name_short=name,
                          scenario=scenario,
                          flex_charging=(charge_name == "oppb"),
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


def stations_to_db(stations_path, electrified_stations_path, scenario):
    object_list = []
    try:
        last_id = Station.objects.aggregate(Max('id'))['id__max']
        if last_id is None:
            last_id = -1
    except Exception:
        last_id = -1
    has_necessary_columns = True
    with open(stations_path, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        column_names = [name for name in reader.fieldnames]
        must_contains = ["Endhaltestelle", "long", "lat", "elevation"]
        for must_contain in must_contains:
            if must_contain not in column_names:
                warnings.warn(f"{stations_path} does not contain the right columns, but only "
                              f"{column_names}")
                has_necessary_columns = False
                break
        if has_necessary_columns:
            for row in reader:
                last_id += 1
                try:
                    long = float(row["long"])
                    lat = float(row["lat"])
                    elevation = float(row["elevation"])
                    geom = GEOSGeometry(f"POINT({long} {lat} {elevation})")
                    params = dict(id=last_id, scenario=scenario,
                                  geom=geom, name=str(row["Endhaltestelle"]))
                    object_list.append(Station(**params))
                except Exception:
                    print(traceback.format_exc())
                    pass
    Station.objects.bulk_create(object_list)

    with open(electrified_stations_path, 'r') as f:
        electrified_stations = simba.util.uncomment_json_file(f)

    for name, ele_station in electrified_stations.items():
        station = Station.objects.get(name=name, scenario=scenario)
        station.is_electrified = True
        station.type = ele_station.get("type")

        station.voltage_level = ele_station.get("voltage_level",
                                                scenario.options.get("default_voltage_level"))
        station.amount_charging_places = ele_station.get("n_charging_stations")
        # ToDo how do we handle differences in charging power depending on oppb or depb
        if station.type == "opps":
            power_per_charger = ele_station.get("cs_power_opps")
        else:
            power_per_charger = ele_station.get("cs_power_deps_oppb")
        station.power_per_charger = power_per_charger
        station.total_power = ele_station.get("gc_power", scenario.options.get(
            "gc_power_" + station.type))
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


def vary_depot_rotations(schedule) -> "collections.Iterable[simba.rotation.Rotation]":
    """Generator that creates schedules with varying vehicle types for """
    # Keep original rotations to restore them later and keep track of depot rotations
    orig_rotations = deepcopy(schedule.rotations)
    # depot rotations
    depot_rotations = {r_id: rotation for r_id, rotation in orig_rotations.items()
                       if rotation.charging_type == "depb"}
    for rot_id, rotation in depot_rotations.items():
        for vt in rotation.vehicle_class:
            for charging_type in ["depb", "oppb"]:
                # in case of a depot rotation, the vehicle type is adjusted and both
                # charging types are used, even the "oppb". This way calculate_consumption() also
                # calculates the "non-charging" consumption of a depot rotation which is run with
                # an opportunity bus.
                if orig_rotations[rot_id].charging_type == "depb":
                    # Charging type is mutated, since this is used to determine the exact vehicle
                    schedule.rotations[rot_id].charging_type = charging_type
                    schedule.rotations[rot_id].vehicle_type = vt
                    yield schedule.rotations[rot_id]
    # Restore rotations before leaving generator
    schedule.rotations = orig_rotations


def _run_ebus_toolbox(schedule: "simba.schedule.Schedule", args, task_id):
    args.output_directory = Path(settings.UPLOAD_PATH) / task_id
    args.attach_vehicle_soc = True

    db_scenario = Scenario.objects.get(task_id=task_id)

    # By setting charging power for depot buses to zero, we make sure every rotation will generate
    # a new bus
    args.cs_power_depbs_depb = 0
    args.cs_power_deps_oppb = 0
    for key, station in schedule.stations.items():
        schedule.stations[key]["cs_power_deps_depb"] = 0
        schedule.stations[key]["cs_power_deps_oppb"] = 0

    scenario = schedule.run(args)

    def dict_creator():
        return dict(departure_soc=None,
                    vehicle_type=[],
                    delta_soc=[],
                    arrival_soc=None,
                    minimal_soc=None,
                    charging_type=None,
                    )

    # initialize eflips input
    eflips_input = {Rotation.objects.get(scenario=db_scenario, name=rot_id).id: dict_creator()
                    for rot_id in schedule.rotations}

    # Analyze schedules which are generated using different depot vehicles. I.e. every depot
    # rotation is run with each vehicle to generate the consumption
    for rotation in vary_depot_rotations(schedule):
        rotation.calculate_consumption()
        db_rotation = Rotation.objects.get(scenario=db_scenario, name=rotation.id)
        eflips_input[db_rotation.id].update(departure_soc=schedule.min_recharge_deps_depb,
                                            charging_type="depb",
                                            )
        vehicle_type_db = VehicleType.objects.get(
            scenario=db_scenario,
            name_short=rotation.vehicle_type,
            flex_charging=False + (rotation.charging_type == "oppb"))
        eflips_input[db_rotation.id]["vehicle_type"].append(vehicle_type_db.id)
        vehicle = schedule.vehicle_types[rotation.vehicle_type][rotation.charging_type]
        eflips_input[db_rotation.id]["delta_soc"].append(
            rotation.consumption / vehicle["capacity"])

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

        vehicle_type_db = VehicleType.objects.get(scenario=db_scenario,
                                                  name_short=rotation.vehicle_type,
                                                  flex_charging=True,
                                                  )

        eflips_input[db_rotation.id] = dict(departure_soc=rot_soc[0],
                                            arrival_soc=rot_soc[-1],
                                            minimal_soc=min(rot_soc),
                                            charging_type=rotation.charging_type,
                                            vehicle_type=vehicle_type_db.id,
                                            )
    schedule, scenario = simba.simulate.modes_simulation(schedule, scenario, args)

    db_scenario.finished = timezone.now()
    db_scenario.save()
    with open(settings.BASE_DIR / args.output_directory / "report_1/eflips_input.json", "w") as f:
        json.dump(eflips_input, f, indent=4)

    file_path = settings.BASE_DIR / args.output_directory / "report_1/rotation_socs.csv"
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
