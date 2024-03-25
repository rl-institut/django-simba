"""File to create all default Scenario models"""
from django.db import models


def get_default_scenario(apps) -> models.Model:
    DefaultScenario = apps.get_model("ebustoolbox", "DefaultScenario")
    Scenario = apps.get_model("ebustoolbox", "Scenario")
    default_scenarios = DefaultScenario.objects.all()
    count = default_scenarios.count()
    if count == 1:
        default_scenario = default_scenarios.first()
    elif count == 0:
        scenario = Scenario.objects.create(name="DefaultScenario")
        default_scenario = DefaultScenario.objects.create(scenario=scenario)
    else:
        raise Exception
    return default_scenario


def set_default_scenario(apps, schema_editor):
    default_scenario = get_default_scenario(apps)
    Scenario = apps.get_model("ebustoolbox", "Scenario")
    # Delete previous Scenario
    old_scenario = default_scenario.scenario
    new_scenario = Scenario.objects.create(name="DefaultScenario")
    default_scenario.scenario = new_scenario
    old_scenario.delete()
    set_default_vehicle_types(apps=apps, scenario=new_scenario)
    default_scenario.save()


def set_default_vehicle_types(apps, scenario) -> None:
    battery_capacity = 150
    vehicle_type_args = {
        "name": "10m_bus",
        "scenario": scenario,
        "battery_capacity": battery_capacity,
        "charging_curve": [[0, battery_capacity], [1, battery_capacity]],
        "length": 10,
        "empty_mass": 13_000 * 10 / 12,
        "allowed_mass": 19_000 * 10 / 12,
    }
    VehicleType = apps.get_model("ebustoolbox", "VehicleType")
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=True)
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=False)

    battery_capacity = 200
    vehicle_type_args = {
        "name": "12m_bus",
        "scenario": scenario,
        "battery_capacity": battery_capacity,
        "charging_curve": [[0, battery_capacity], [1, battery_capacity]],
        "length": 12,
        "empty_mass": 13_000,
        "allowed_mass": 19_000,
    }
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=True)
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=False)

    battery_capacity = 100
    vehicle_type_args = {
        "name": "18m_bus",
        "scenario": scenario,
        "battery_capacity": battery_capacity,
        "charging_curve": [[0, battery_capacity], [1, battery_capacity]],
        "length": 18,
        "empty_mass": 13_000 * 1.5,
        "allowed_mass": 19_000 * 1.5,
    }
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=True)
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=False)
