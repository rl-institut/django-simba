from environ import environ


class DjangoSimbaWrapper:
    def __init__(self, database_url: str):
        # We need to replace a postgresql:// on the database URL with postgis:// for it to work over here
        database_url = database_url.replace("postgresql://", "postgis://")
        # Now, we put this string in the DEFAULT entry in settings.DATABASES
        from ds_wrapper import settings

        import os
        # Allow unsafe async operations (for Juptyer Notebook)
        # https://stackoverflow.com/questions/61926359/django-synchronousonlyoperation-you-cannot-call-this-from-an-async-context-u

        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

        os.environ["DJANGO_SIMBA_DATABASE_URL"] = database_url
        settings.DATABASES["default"] = environ.Env().db("DJANGO_SIMBA_DATABASE_URL")


        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ds_wrapper.settings")
        # You may also want to set the DATABASE_URL env variable if it's not set from the outside.
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
        from ebustoolbox.tasks import run_simba_scenario

        run_simba_scenario(django_scenario=scenario_id, assign_vehicles=assign_vehicles)

        from django.db import connections
        for conn in connections.all():
            conn.close()
