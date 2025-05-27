from django.core.management.base import BaseCommand
from django.apps import apps
from ebustoolbox.default_scenario import get_default_scenario, set_default_vehicle_types
from ebustoolbox.models import VehicleType, VehicleClass, Scenario, DefaultScenario


class Command(BaseCommand):
    help = "Delete old default vehicle types and Vehicle Classed and create new ones"

    def handle(self, *args, **kwargs):
        # Set the default vehicle types
        scenario = get_default_scenario(DefaultScenario, Scenario).scenario
        VehicleType.objects.filter(scenario=scenario).delete()
        VehicleClass.objects.filter(scenario=scenario).delete()
        set_default_vehicle_types(apps, scenario)
