from django.core.management.base import BaseCommand
from ebustoolbox.default_scenario import get_default_scenario
from ebustoolbox.models import Consumption, VehicleType, VehicleClass, Scenario, DefaultScenario
import pandas as pd
from pathlib import Path


class Command(BaseCommand):
    help = "Load Consumption tables and connect them with default Vehicle Types"

    def handle(self, *args, **kwargs):
        scenario = get_default_scenario(DefaultScenario, Scenario).scenario
        root = "./ebustoolbox/static/ebustoolbox/examples/"
        consumption_paths = [
            (10, root + "consumption_ebus2030_no_diesel_10m.csv"),
            (12, root + "consumption_ebus2030_no_diesel_12m.csv"),
            (14, root + "consumption_ebus2030_no_diesel_14m.csv"),
            (18, root + "consumption_ebus2030_no_diesel_18m.csv"),
            (7, root + "consumption_ebus2030_no_diesel_7m.csv"),
            (10, root + "consumption_ebus2030_w_diesel_10m.csv"),
            (12, root + "consumption_ebus2030_w_diesel_12m.csv"),
            (14, root + "consumption_ebus2030_w_diesel_14m.csv"),
            (18, root + "consumption_ebus2030_w_diesel_18m.csv"),
            (7, root + "consumption_ebus2030_w_diesel_7m.csv"),
        ]

        default_vts = VehicleType.objects.filter(scenario=scenario)
        for length, path in consumption_paths:
            vts = default_vts.filter(length=length)
            if "no_diesel" in path:
                vts = vts.exclude(name__icontains="zusatzheizung")
            else:
                vts = vts.filter(name__icontains="zusatzheizung")
            dataframe = pd.read_csv(Path(path))
            for vt in vts:
                vehicle_class = VehicleClass.objects.filter(
                    vehicle_types=vt,
                )
                if vehicle_class.exists():
                    assert vehicle_class.count() == 1
                    vehicle_class = vehicle_class.first()
                else:
                    vehicle_class = VehicleClass(
                        scenario=scenario,
                        name=f"Consumption Vehicle Class for default vehicle {vt.name} {vt.id}",
                    )
                    vehicle_class.save()
                    vehicle_class.vehicle_types.add(vt)
                # Delete old consumptions which might point to the default vehicles
                Consumption.objects.filter(vehicle_class=vehicle_class).delete()
                consumption = Consumption.from_df(
                    dataframe,
                    name=f"Default Consumption {length}m for default vehicle {vt.name} with id {vt.id}",
                )
                consumption.scenario = scenario
                consumption.vehicle_class = vehicle_class
                consumption.save()
