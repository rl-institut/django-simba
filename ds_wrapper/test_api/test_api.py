#!/usr/bin/env python3

"""

This module tests the public API in order to make

- the consumption simulation
- the charging station placement (single and all-step)

callable from the outside.

Additionally, since the sample database that is used is a `eflips-model` database, this test validates (parts of)
the django-simba <-> efliPS-model database compatibility.

"""
import random
import warnings
from typing import Tuple
from urllib.parse import urlparse
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import eflips.model

from ds_wrapper import DjangoSimbaWrapper
from eflips.model import ConsistencyWarning

SCENARIO_ID = 1

# We resolve the "both a LUT and consumption value are set" before it gets used.
warnings.simplefilter("ignore", category=ConsistencyWarning)


def database_url_components(database_url: str) -> Tuple[str, str, str, str, str, str]:
    """
    Extracts the components of a database URL.
    :param database_url: The URL of the database.
    :return: A tuple with the components of the URL: protocol, user, password, host, port, database name.
    """
    o = urlparse(database_url)
    if o.scheme != "postgresql":
        raise ValueError("Only PostgreSQL databases are supported.")
    if o.port is None:
        port = "5432"
    else:
        port = str(o.port)
    return o.scheme, o.username, o.password, o.hostname, port, o.path[1:]


def clear_db():
    DATABASE_URL = os.environ["EFLIPS_DATABASE_URL"]

    path_to_this_file = os.path.dirname(os.path.abspath(__file__))
    path_to_clear_database_sql = os.path.join(path_to_this_file, "clear_database.sql")

    _, database_user, database_password, database_host, database_port, database_name = (
        database_url_components(DATABASE_URL)
    )

    # Clear the database
    query_str = f"psql {database_name} -f {path_to_clear_database_sql}"
    if database_host:
        query_str += f" -h {database_host}"
    if database_port:
        query_str += f" -p {database_port}"
    if database_user:
        query_str += f" -U {database_user}"
        os.environ["PGPASSWORD"] = database_password
    if os.system(query_str) != 0:
        raise ValueError("Failed to clear the database.")


def import_db():
    DATABASE_URL = os.environ["EFLIPS_DATABASE_URL"]

    path_to_this_file = os.path.dirname(os.path.abspath(__file__))
    path_to_import_eflips_model_sql = os.path.join(path_to_this_file, "eflips-model-sample-db.sql")

    _, database_user, database_password, database_host, database_port, database_name = (
        database_url_components(DATABASE_URL)
    )

    # Import the eflips-model database
    query_str = f"psql {database_name} -f {path_to_import_eflips_model_sql}"
    if database_host:
        query_str += f" -h {database_host}"
    if database_port:
        query_str += f" -p {database_port}"
    if database_user:
        query_str += f" -U {database_user}"
        os.environ["PGPASSWORD"] = database_password
    if os.system(query_str) != 0:
        raise ValueError("Failed to import the eflips-model database.")


def clear_and_import():
    """
    Uses psql system commands to clear the database and import the eflips-model database.
    """
    clear_db()
    import_db()


def test_simba_consumption_simulation():
    """
    Tests the consumption simulation from the public API.
    """
    engine = create_engine(os.environ["EFLIPS_DATABASE_URL"])
    session = Session(engine)
    # Make sure the fixed ocnsumption values are set to None
    session.query(eflips.model.VehicleType).update({eflips.model.VehicleType.consumption: None})

    # ---- BUG ----
    # django-simBA requires the `VehicleType` type to have a (unique within scenario?) short name
    # This is not enforced by the eflips-model database, so we need to set it manually.
    # Tracked in https://github.com/rl-institut/django-simba/issues/145
    # ---- BUG ----
    #for vehicle_type in session.query(eflips.model.VehicleType).all():
    #    random_string = random.randbytes(16).hex()
    #    vehicle_type.name_short = random_string

    # --- BUG ---
    # Apparently, a rotation with allow_opportunity_charging=False cannot be driven by a vehicle of a
    # VehicleType with opportunity_cahrging_capable=True
    # This is not documented and should be fixed.
    # Tracked in https://github.com/rl-institut/django-simba/issues/146
    # --- BUG ---
    #for vehicle_type in session.query(eflips.model.VehicleType).all():
    #    vehicle_type.opportunity_charging_capable = False

    # We need to set the loaded mass for each trip
    # If we're using the smart consumption model.
    # THis is not a bug
    session.query(eflips.model.Trip).update({eflips.model.Trip.loaded_mass: 1000})

    session.commit()
    ds_wrapper = DjangoSimbaWrapper(os.environ["EFLIPS_DATABASE_URL"])
    ds_wrapper.run_simba_scenario(SCENARIO_ID, assign_vehicles=True)
    del ds_wrapper
    # A second commit is needed to get the results of the consumption simulation.
    # Since it used a separate session, we need to commit our own session again
    # to make the results visible.
    session.commit()
    session.expire_all()  # Just for safety, to make sure that we don't use stale data.

    # Now, we use the session to check that there is a driving event for each trip.
    all_trips = (
        session.query(eflips.model.Trip).filter(eflips.model.Trip.scenario_id == SCENARIO_ID).all()
    )
    for trip in all_trips:
        driving_event = (
            session.query(eflips.model.Event).filter(eflips.model.Event.trip_id == trip.id).one()
        )
        assert driving_event is not None
        driving_event: eflips.model.Event
        assert driving_event.event_type == eflips.model.EventType.DRIVING
        assert driving_event.time_start is not None
        assert driving_event.time_end is not None
        assert driving_event.soc_start is not None
        assert driving_event.soc_end is not None
        assert driving_event.soc_start > driving_event.soc_end


if __name__ == "__main__":
    # This script must be run with the project root directory as the working directory.
    # Specifically, the "django_mapengine" folder must be in the working directory.
    # TODO: Make this more robust. Like this, it is impossible to use the consumption simulation
    # --- BUG ---
    # The django-mapengine folder seems to be required to be in the working directory.
    # This is not documented and should be fixed.
    # Tracked in https://github.com/rl-institut/django-simba/issues/144
    # --- BUG ---

    # Check if the EFLIPS_DATABASE_URL is set
    if "EFLIPS_DATABASE_URL" not in os.environ or os.environ["EFLIPS_DATABASE_URL"] == "":
        raise ValueError("EFLIPS_DATABASE_URL not set")

    if not os.path.exists("django_mapengine"):
        # outside of the Django project
        raise ValueError(
            "This script must be run with the project root directory as the working directory."
        )

    clear_and_import()

    test_simba_consumption_simulation()
