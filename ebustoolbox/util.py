
from pathlib import Path
import shutil

from django.conf import settings
from django.utils import timezone
from .tasks import save_vehicle_properties_from_file, bus_stops_from_file
from .models import Scenario
from ebus_toolbox import simulate as ebus_toolbox
from celery import uuid
# make matplotlib not use Tk, run on main thread
import matplotlib
matplotlib.use('Agg')

def get_unique_task_id():
    task_id_not_unique = True
    task_id = None
    # Create unique ids for as long as needed, so no duplicate ids exist
    while task_id_not_unique:
        try:
            task_id = uuid()
            Scenario.objects.get(task_id=task_id)
        except Scenario.DoesNotExist:
            task_id_not_unique = False
    return task_id


def generate_zipped_scenario(task_id:str):
    scenario = Scenario.objects.get(task_id=task_id)
    folder_path = Path(settings.UPLOAD_PATH, task_id)
    output_path =  settings.MEDIA_ROOT / (task_id + ".zip")
    if not folder_path.exists():
        print("input folder for zipping not found")
        return
    if output_path.is_file():
        print("Zip already exists")
        return
    shutil.make_archive(output_path.with_suffix(''), 'zip', folder_path)
