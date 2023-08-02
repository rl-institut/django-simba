import json
import warnings

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

from .models import Vehicle, VehicleProperties, UploadedFile, Station, VehicleType, VehicleClass
from .models import Scenario
from ebus_toolbox import simulate as ebus_toolbox
from ebus_toolbox.util import uncomment_json_file

from argparse import Namespace

# ToDo: Any better solutions?
INTEGER_INF = 9999


def fill_db_with_input_files(cleaned_data, request):
    scenario = scenario_to_db(cleaned_data, request)

    stations_to_db(scenario.options["station_data_path"], scenario.options["electrified_stations"],
                   scenario)
    vehicles_to_db(scenario.options["vehicle_types"], scenario)

    schedule_to_db(scenario.options["input_schedule"])

    return scenario


def scenario_to_db(cleaned_data, request):
    scenario = Scenario.objects.create(name=cleaned_data["title"])
    args = dict(cleaned_data)
    args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(',')))
    # decimal -> float
    for k, v in args.items():
        if type(v) == Decimal:
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

    scenario.opps_charging_power = scenario.options["cs_power_opps"]
    scenario.deps_charging_power = scenario.options["cs_power_deps_depb"]
    scenario.save()
    return scenario


def schedule_to_db(file_path):
    pass


def vehicles_to_db(file_path, scenario):
    VehicleClass.objects.get_or_create(name="oppb")
    VehicleClass.objects.get_or_create(name="depb")

    with open(file_path, 'r') as f:
        vehicle_types = uncomment_json_file(f)

    for name, v_type in vehicle_types.items():
        for charge_name, charge_type in v_type.items():
            vehicle_class = VehicleClass.objects.get(name=charge_name)
            consumption=float(charge_type.get("mileage"))
            params=dict(name=charge_type.get("name", "unnamed bus"),
                        vehicle_class = vehicle_class,
                        scenario = scenario,
                        flex_charging = (vehicle_class == "oppb"),
                        battery_capacity = charge_type["capacity"],
                        charging_efficiency = charge_type.get("battery_efficiency", 0.95),
                        minimum_charging_power = charge_type.get("min_charging_power"),
                        charging_curve = charge_type["charging_curve"],
                        v2g_curve = charge_type.get("v2g_curve", None),
                        v2g = charge_type.get("v2g", False),
                        consumption = consumption,
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
        electrified_stations = uncomment_json_file(f)

    for name, ele_station in electrified_stations.items():
        a =1
        station = Station.objects.get(name=name, scenario=scenario)
        station.is_electrified = True
        station.type = ele_station.get("type")

        station.voltage_level = ele_station.get("voltage_level",
                                        scenario.options.get("default_voltage_level"))
        station.amount_charging_places = ele_station.get("n_charging_stations")
        # ToDo how do we handle differences in charging power depending on oppb or depb
        if station.type  == "opps":
            power_per_charger = ele_station.get("cs_power_opps")
        else:
            power_per_charger = ele_station.get("cs_power_deps_oppb")
        station.power_per_charger = power_per_charger
        station.total_power = ele_station.get("gc_power", scenario.options.get(
            "gc_power_" + station.type))
        station.save()


def generate_zipped_scenario(task_id):
    if settings.CELERY_BROKER_URL:
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


def run_ebus_toolbox(model_args_as_dict, task_id):
    if settings.CELERY_BROKER_URL:
        print("Using Celery")
        _ = _celery_run_ebus_toolbox.apply_async((model_args_as_dict, str(task_id)),
                                                 task_id=task_id)
    else:
        _run_ebus_toolbox(model_args_as_dict, task_id)


@shared_task(bind=True)
def _celery_run_ebus_toolbox(self, model_args_as_dict, task_id):
    _run_ebus_toolbox(model_args_as_dict, task_id)


def _run_ebus_toolbox(model_args_as_dict, task_id):
    args = Namespace(**model_args_as_dict)
    args.output_directory = Path(settings.UPLOAD_PATH) / task_id
    # immutable options, not read from form
    args.margin = 1
    args.ALLOW_NEGATIVE_SOC = True
    args.PRICE_THRESHOLD = -100
    args.rotation_filter_variable = None
    args.show_plots = False

    ebus_toolbox.simulate(args)

    # print(time.time() - start, " since start, after task")
    scenarios = Scenario.objects.filter(task_id=task_id)
    assert len(scenarios) == 1
    scenarios.update(finished=timezone.now())
    file_path = settings.BASE_DIR / args.output_directory / "sim/rotation_socs.csv"
    save_vehicle_properties_from_file(file_path, scenarios[0])
    # station_file_path = args.station_data_path
    # bus_stops_from_file(station_file_path, scenarios[0])


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

#
# def bus_stops_from_file(file_path, scenario):
#     object_list = []
#     try:
#         last_id = BusStop.objects.aggregate(Max('id'))['id__max']
#         if last_id is None:
#             last_id = -1
#     except Exception:
#         print(Exception)
#         last_id = -1
#     print(last_id)
#     with open(file_path, 'r') as csvfile:
#         reader = csv.DictReader(csvfile)
#         column_names = [name for name in reader.fieldnames]
#         for row in reader:
#             last_id += 1
#             if ("Endhaltestelle" not in column_names or
#                     "long" not in column_names or
#                     "lat" not in column_names):
#                 print("not the right columns")
#                 print(column_names)
#                 break
#             try:
#                 name = str(row["Endhaltestelle"])
#                 long = float(row["long"])
#                 lat = float(row["lat"])
#                 geom = GEOSGeometry(f"POINT({long} {lat})")
#                 params = dict(id=last_id, scenario=scenario,
#                               geom=geom, name=name)
#                 obj = BusStop(**params)
#                 object_list.append(obj)
#             except Exception:
#                 print(traceback.format_exc())
#                 pass
#     BusStop.objects.bulk_create(object_list)
