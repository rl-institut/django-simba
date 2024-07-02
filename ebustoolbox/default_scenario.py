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
    vehicle_data_list = [
        ("Minibus", 120, 7, 0.9),
        ("Minibus_Zusatzheizung", 120, 7, 0.7),
        ("Midibus", 450, 10, 1.8),
        ("Midibus_Zusatzheizung", 325, 10, 1.3),
        ("Solobus", 500, 12, 2),
        ("Solobus_Zusatzheizung", 400, 12, 1.4),
        ("Gelenkbus", 650, 18, 2.6),
        ("Gelenkbus_Zusatzheizung", 500, 18, 1.8),
    ]
    for vehicle_data in vehicle_data_list:
        set_default_vehicle_type(apps, scenario, *vehicle_data)


def set_default_vehicle_type(apps, scenario, name, battery_capacity, length, consumption) -> None:
    vehicle_type_args = {
        "name": name,
        "scenario": scenario,
        "battery_capacity": battery_capacity,
        "charging_curve": [[0, 350], [0.8, 350], [1, 50]],
        "length": length,
        "empty_mass": 13_000 * length / 12,
        "allowed_mass": 19_000 * length / 12,
        "consumption": consumption,
    }
    VehicleType = apps.get_model("ebustoolbox", "VehicleType")
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=True)
    _ = VehicleType.objects.create(**vehicle_type_args, opportunity_charging_capable=False)
