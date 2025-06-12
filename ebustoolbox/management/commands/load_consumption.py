from django.core.management.base import BaseCommand
from ebustoolbox.default_scenario import get_default_scenario
from ebustoolbox.models import Consumption, VehicleType, VehicleClass, Scenario, DefaultScenario
import pandas as pd
from pathlib import Path


class Command(BaseCommand):
    help = "Load Consumption tables and connect them with default Vehicle Types"

    def handle(self, *args, **kwargs):
        scenario = get_default_scenario(DefaultScenario, Scenario).scenario
        consumption_paths = {
            7: "./ebustoolbox/static/ebustoolbox/examples/6m_consumption_sprinter_6m.csv",
            10: "./ebustoolbox/static/ebustoolbox/examples/10m_consumption_lle_99.csv",
            12: "./ebustoolbox/static/ebustoolbox/examples/12m_consumption_nor_bus.csv",
            18: "./ebustoolbox/static/ebustoolbox/examples/18m_consumption_solaris_18m.csv",
        }
        default_vts = VehicleType.objects.filter(scenario=scenario)
        for length, path in consumption_paths.items():
            vts = default_vts.filter(length=length)
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
