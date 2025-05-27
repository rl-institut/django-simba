import json
import logging
from django.core.management.base import BaseCommand
from matplotlib import pyplot as plt
from ebustoolbox.default_scenario import get_default_scenario
from ebustoolbox.models import Consumption, VehicleType, VehicleClass, Scenario, DefaultScenario
import pandas as pd
from pathlib import Path
from ebustoolbox.util import generate_consumption_lut_plot

logger = logging.getLogger("custom")


class Command(BaseCommand):
    help = "Load Consumption tables and connect them with default Vehicle Types"

    def handle(self, *args, **kwargs):
        # Set the default vehicle types
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

        scenario = get_default_scenario(DefaultScenario, Scenario).scenario
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
                figure = generate_consumption_lut_plot(consumption)
                figure.savefig("consumption_" + vt.name + ".pdf")
                plt.close()
        df = pd.read_csv(root + "tu_bvg_lut.csv")
        # Read TU Berlin bvg data
        logger.warning("Emperical data is incomplete. GN Data is used for [EN,DD,GN] vehicle types")
        for name in ["EN", "DD", "GN"]:
            vts = default_vts.filter(name=name)
            for _, row in df.iterrows():
                # TODO: only GN has complete datapoints. other data_points for other vehicle types
                # dont end syntactically correct, e.g. with "...{10,10" without closing brackets

                # The commented code is what should be done if we had complete data
                # if name.lower() not in row["name"].lower():
                # instead
                if "gn" not in row["name"].lower():
                    continue
                columns = json.loads(row["columns"].replace("{", "[").replace("}", "]"))
                data_points = json.loads(row["data_points"].replace("{", "[").replace("}", "]"))
                values = json.loads(row["values"].replace("{", "[").replace("}", "]"))
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
                    consumption = Consumption(
                        name=f"Default Emperical Consumption for default vehicle {vt.name} with id {vt.id}",
                        vehicle_class=vehicle_class,
                        scenario=scenario,
                        columns=columns,
                        data_points=data_points,
                        values=values,
                    )
                    consumption.save()
                    figure = generate_consumption_lut_plot(consumption)
                    figure.savefig("consumption_" + vt.name + ".pdf")
                    plt.close()
                break
            else:
                print(name, " not found")
