import csv
import traceback
from datetime import datetime
from pathlib import Path

from celery import shared_task
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Max
from django.conf import settings
import ebusdjango.settings
from ebustoolbox.models import Scenario, Vehicle, VehicleProperties, TaskRun, EbusToolbox, BusStop
from django.utils.timezone import make_aware
from ebus_toolbox.simulate import simulate
from ebus_toolbox.util import get_args
import time
import sys
import zipfile
import os
@shared_task(bind=True)
def generate_zipped_scenario(self, _task_id:str):
    task, _ = TaskRun.objects.get_or_create(task_id=_task_id)
    task.finished = False

    # Example usage
    folder_path = settings.BASE_DIR / 'ebustoolbox/static/data/sim_outputs' / _task_id  # Replace with the actual folder path
    output_path =  settings.BASE_DIR / 'media' / (_task_id + ".zip")  # Replace with the desired output ZIP file path
    my_file = Path(output_path)
    if not Path(folder_path).exists():
        print("input folder for zipping not found")
        return
    if my_file.is_file():
        print("Zip already exists")
        return
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, arcname=os.path.relpath(file_path, folder_path))
    task.finished = True






@shared_task(bind=True)
def run_ebus_toolbox(self, model_args_as_dict, form_id):
    # change system arguments so get_args does not crash
    sys.argv = [sys.argv[0], "--input-schedule", "foo/bar.csv", "--electrified-stations", "foo/bar.json"]
    args = get_args()
    for key in args.__dict__:
        if key in model_args_as_dict:
            setattr(args, key, model_args_as_dict[key])

    scenario_id = self.request.id
    task, _ = TaskRun.objects.get_or_create(task_id=scenario_id, finished=False)

    # The model which is used as input gets a reference to the scenario/ ouput
    # which is created
    form_model = EbusToolbox.objects.get(id=form_id)
    form_model.task_id = scenario_id
    form_model.save()
    args.output_directory = Path(args.output_directory) / scenario_id
    print("starting sim")
    simulate(args)
    print("sim finished")
    # put the results into a model
    # data
    scenario, _ = Scenario.objects.get_or_create(id=scenario_id, name=scenario_id)

    file_path = ebusdjango.settings.BASE_DIR / args.output_directory / "sim/rotation_socs.csv"
    start = time.time()

    save_vehicle_properties_from_file(file_path, scenario)
    toolbox_obj = EbusToolbox.objects.get(task_id=scenario_id)
    print(Path(toolbox_obj.station_data_path.path))
    file_path = toolbox_obj.station_data_path.path
    bus_stops_from_file(file_path, scenario)
    print(time.time() - start, " since start, after task")
    task.finished = True
    task.save()


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