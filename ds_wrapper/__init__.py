from django.db import connections
from environ import environ
import os

from ds_wrapper import settings


class DjangoSimbaWrapper:
    def __init__(self, database_url: str):
        # We need to replace a postgresql:// on the database URL with postgis:// for it to work over here
        database_url = database_url.replace("postgresql://", "postgis://")
        # Now, we put this string in the DEFAULT entry in settings.DATABASES

        # Allow unsafe async operations (for Juptyer Notebook)
        # https://stackoverflow.com/questions/61926359/django-synchronousonlyoperation-you-cannot-call-this-from-an-async-context-u

        os.environ["DJANGO_SECRET_KEY"] = "DJANGO_SECRET_KEY"
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

        os.environ["DATABASE_URL"] = database_url
        settings.DATABASES["default"] = environ.Env().db("DATABASE_URL")

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ds_wrapper.settings")
        import django

        django.setup()

    def run_simba_scenario(
        self,
        scenario_id: int,
        assign_vehicles=False,
    ):
        """Run a Scenario from the database with SimBA

        The provided scenario must contain all information including Temperatures, Vehicle_Types,
        station information and electrified_station information.
        :param scenario_id: Scenario which is simulated
        :param assign_vehicles: boolean if the vehicles should be added to rotations.
        Previous assignments will be deleted
        :return:
        """
        # needs Django setup
        from ebustoolbox.tasks import run_simba_scenario

        run_simba_scenario(django_scenario=scenario_id, assign_vehicles=assign_vehicles)
        for conn in connections.all():
            conn.close()

    def single_step_electrification(self, scenario_id: int) -> None:
        """Run single step electrification once for a scenario. One station will be electrified as long as there are
        rotations with negative SOC

        :param scenario_id: Scenario which is simulated
        :return: None
        """
        # needs Django setup
        from ebustoolbox.models import Scenario
        from ebustoolbox.tasks import is_consistent
        from ebustoolbox.tasks import run_simba_scenario

        # fetch scenario. Will fail if scenario_id is wrong
        django_scenario = Scenario.objects.get(id=scenario_id)
        assert is_consistent(django_scenario)

        schedule, simbascenario = run_simba_scenario(
            django_scenario=scenario_id, assign_vehicles=True
        )

        schedule, simbascenario = run_simba_scenario(
            django_scenario, simba_scenario=simbascenario, mode="station_optimization_single_step"
        )

        for conn in connections.all():
            conn.close()
