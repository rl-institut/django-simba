import csv
import traceback
from datetime import datetime
from pathlib import Path
from django.utils import timezone
import ebus_toolbox
from celery import shared_task
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Max
from django.conf import settings
import ebusdjango.settings
from ebustoolbox.models import Scenario, Vehicle, VehicleProperties, BusStop
from .models import Scenario, BusStop
from django.utils.timezone import make_aware
from ebus_toolbox import simulate as ebus_toolbox
from ebus_toolbox.util import get_args
import time
import sys
import zipfile
import os
from argparse import Namespace

def run_ebus_toolbox(model_args_as_dict, task_id):
    if settings.CELERY_BROKER_URL:
        print("Using Celery")
        _ = celery_run_ebus_toolbox_.apply_async((model_args_as_dict, str(task_id)), task_id=task_id)
    else:
        run_ebus_toolbox_(model_args_as_dict, task_id)


@shared_task(bind=True)
def celery_run_ebus_toolbox_(self, model_args_as_dict, task_id):
    run_ebus_toolbox_(model_args_as_dict, task_id)

def run_ebus_toolbox_(model_args_as_dict, task_id):
    args = Namespace(**model_args_as_dict)
    args.output_directory = Path(settings.UPLOAD_PATH) / task_id
    # unmutable options, not read from form
    args.margin = 1
    args.ALLOW_NEGATIVE_SOC = True
    args.PRICE_THRESHOLD = -100
    args.rotation_filter_variable = None
    args.show_plots = False

    ebus_toolbox.simulate(args)

    # print(time.time() - start, " since start, after task")
    scenarios = Scenario.objects.filter(task_id=task_id)
    assert len(scenarios)==1
    scenarios.update(finished=timezone.now())
    file_path = settings.BASE_DIR / args.output_directory / "sim/rotation_socs.csv"
    save_vehicle_properties_from_file(file_path, scenarios[0])
    station_file_path = args.station_data_path
    bus_stops_from_file(station_file_path, scenarios[0])



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
                except:
                    soc = None
                object_list.append(VehicleProperties(date=aware_datetime, soc=soc, vehicle=vehicle_dict[vehicle_name],
                                                     scenario=scenario, id=last_id + 1))
                last_id += 1
    VehicleProperties.objects.bulk_create(object_list)

def bus_stops_from_file(file_path, scenario):
    object_list = []
    try:
        last_id = BusStop.objects.aggregate(Max('id'))['id__max']
        if last_id == None:
            last_id = -1
    except Exception:
        print(Exception)
        last_id = -1
    print(last_id)
    with open(file_path, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        column_names = [name for name in reader.fieldnames]
        for row in reader:
            last_id += 1
            if ("Endhaltestelle" not in column_names or
                    "long" not in column_names or
                    "lat" not in column_names):
                print("not the right columns")
                print(column_names)
                break
            try:
                name = str(row["Endhaltestelle"])
                long = float(row["long"])
                lat = float(row["lat"])
                geom = GEOSGeometry(f"POINT({long} {lat})")
                print(name)
                print(long)
                print(lat)
                print(last_id)
                print(scenario)
                print(geom)

                params= dict(id=last_id, scenario=scenario,
                                         geom=geom, name=name)
                print(params)
                obj = BusStop(**params)
                object_list.append(obj)
                print("bus stop created")
            except Exception:
                print(traceback.format_exc())
                pass

    BusStop.objects.bulk_create(object_list)