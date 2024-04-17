import collections
import csv
import shutil
import traceback
import warnings
from argparse import Namespace
from copy import deepcopy, copy
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, List

import tqdm
from celery import shared_task
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Max
from django.db.transaction import atomic
from django.http import HttpRequest
from django.utils import timezone
from django.utils.timezone import make_aware, is_aware
from eflips.depot.api import simulate_scenario, generate_depot_layout

import core.deepcopy
import simba.optimizer_util
import simba.simulate
import simba.trip
import simba.util
from core.deepcopy import reset_postgres_auto_increments
from core.models import Progress
from simba.rotation import Rotation as SimbaRotation
from simba.schedule import Schedule as SimbaSchedule
from simba.data_container import DataContainer
from . import schedule_readers
from .models import (
    Route,
    Consumption,
    Vehicle,
    UploadedFile,
    Station,
    VehicleType,
    Rotation,
    Trip,
    Scenario,
    EnumChargeType,
    Line,
    charge_type_from_simba_to_db,
    charge_type_from_db_to_station,
    Temperatures,
    Event,
    EventType,
    VehicleClass,
)
from .schedule_readers import ScheduleReader

if TYPE_CHECKING:
    from spice_ev.scenario import Scenario as SimbaScenario

# ToDo: Any better solutions?
INTEGER_INF = 9999
MAX_AMOUNT_VEHICLES = 10000


@atomic()
def input_files_to_database(cleaned_data: dict, request: HttpRequest):
    """Fill the database with the inputs from the form

    :param cleaned_data: cleaned data
    :param request: Request with uploaded files
    :return:
    """
    django_scenario = scenario_to_db(cleaned_data, request)
    original_args = get_args(django_scenario)

    # Write the Temperatures to the DB
    temperature_path = Path(django_scenario.simba_options["temperature_time_series_path"])
    use_only_time = django_scenario.simba_options["use_only_time"]
    temperatures_to_db(temperature_path, django_scenario, use_only_time=use_only_time)

    # Write the Consumption to the DB
    consumption_path = Path(django_scenario.simba_options["consumption_path"])
    consumption_file_to_db(consumption_path, django_scenario)

    # Create the schedule from the args, and delete features which are not used in django
    simba_schedule, new_args = get_schedule_from_args(
        original_args, django_scenario=django_scenario
    )

    # Write the station geodata and electrified stations to DB
    stations_to_db(simba_schedule.station_data, simba_schedule.stations, django_scenario)

    # Write the vehicle types to DB
    vehicles_to_db(simba_schedule.vehicle_types, django_scenario)

    # ToDo Consistency check is nice, but should be handled in a more generic way.
    # some cases are not handled like overlapping times, etc.
    # Remove trips which have non unique times for arrival or departure per rotation
    # Remove Rotations which dont start at the depot
    filter_inconsistent_trips_and_rotations(simba_schedule)

    # Write the schedule including rotations and trips to the DB
    schedule_to_db(simba_schedule, django_scenario)

    return django_scenario, simba_schedule, original_args


def consumption_file_to_db(consumption_path: Path, django_scenario: Scenario) -> None:
    """Writes the Consumption to the database and connects it with the scenario"""

    delim = simba.util.get_csv_delim(consumption_path)
    consumption_names = ["consumption", "consumption_kwh_per_km"]

    with open(consumption_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delim)
        columns = copy(reader.fieldnames)
        consumption_found = False
        cons = None
        for cons in consumption_names:
            if cons in columns:
                consumption_found = True
                break
        if not consumption_found:
            text = f"No column named {consumption_names} was found in {consumption_path.stem}"
            raise AssertionError(text)
        columns.remove(cons)
        datapoints = []
        values = []
        for i, row in enumerate(reader):
            data = []
            try:
                for field in columns:
                    data_point = row[field]
                    data.append(float(data_point))
                val = row[cons]
                val = float(val)
            except ValueError:
                if val == "" or data_point == "":
                    warnings.warn(
                        f"Row {i} in {consumption_path.stem} contains a missing value. "
                        f"This row and following rows will be ignored."
                    )
                    break
                else:
                    raise
            values.append(val)
            datapoints.append(data)

    # VehicleClass that will be linked with this Consumption
    vehicle_class, _ = VehicleClass.objects.get_or_create(
        scenario=django_scenario,
        name=consumption_path.name,
    )
    vehicle_class.save()
    Consumption.objects.create(
        name=consumption_path.name,
        scenario=django_scenario,
        columns=columns,
        data_points=datapoints,
        values=values,
        vehicle_class=vehicle_class,
    )


# ToDo Do somewhere else?
def filter_inconsistent_trips_and_rotations(simba_schedule):
    # Some filter functions to handle messy bvg input
    counter = 0
    del_rots = []
    for key, rotation in simba_schedule.rotations.items():
        depart_times = [t.departure_time for t in rotation.trips]
        arrival_times = [t.arrival_time for t in rotation.trips]
        start = 0
        while True:
            for i, _ in enumerate(rotation.trips[start:]):
                i = i + start
                if (
                    depart_times.count(rotation.trips[i].departure_time) > 1
                    or arrival_times.count(rotation.trips[i].arrival_time) > 1
                ):
                    break
            else:
                rotation.trips = list(sorted(rotation.trips, key=lambda x: x.departure_time))
                break
            counter += 1
            rotation.trips.pop(i)
            depart_times = [t.departure_time for t in rotation.trips]
            arrival_times = [t.arrival_time for t in rotation.trips]
            start = i

        if (
            rotation.trips[0].departure_name not in simba_schedule.stations
            or rotation.trips[-1].arrival_name not in simba_schedule.stations
        ):
            del_rots.append(key)
    print(
        f"Deleting {len(del_rots)} rotations since they dont start or end at electrified station:{del_rots}"
    )
    for rot_id in del_rots:
        del simba_schedule.rotations[rot_id]
    print(f"{counter} trips deleted") if counter > 0 else print()


def temperatures_to_db(
    temperature_file_path: Path,
    django_scenario: Scenario,
    use_only_time: bool,
) -> None:
    """Writes the temperatures to the database and connects it with the scenario"""
    delim = simba.util.get_csv_delim(temperature_file_path)
    with open(temperature_file_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delim)
        times = []
        temperatures = []
        for row in reader:
            times.append(datetime.fromisoformat(row["time"]))
            temperatures.append(row["temperature"])
        temperatures_instance = Temperatures(
            scenario=django_scenario,
            name=temperature_file_path.name,
            use_only_time=use_only_time,
            datetimes=times,
            data=temperatures,
        )
        temperatures_instance.make_aware()
        temperatures_instance.save()


def get_schedule_from_db(django_scenario: Scenario) -> tuple[simba.schedule.Schedule, Namespace]:
    """Takes a django Scenario and returns the simba Schedule and arguments

    Can be used to run a previously stored Django Scenario again straight from the database without
    using files, by returning schedule and args.

    :param django_scenario: Scenario
    :type django_scenario: .models.Scenario
    :return: (simba Schedule, args)
    :rtype: (simba.schedule.Schedule, Namespace)
    """
    # get SimBa station_data
    station_data = get_station_data_from_db(django_scenario)

    # get SimBA electrified stations from db
    stations_dict = get_electrified_stations_from_db(django_scenario)

    # get SimBA vehicle_types from db
    vehicle_types = get_vehicle_types_from_db(django_scenario)
    data_container = DataContainer()
    data_container.add_vehicle_types(vehicle_types)
    consumptions = Consumption.objects.filter(scenario__in=[django_scenario, None])
    for consumption in consumptions:
        data_container.add_consumption_data(consumption.name, consumption.to_df())

    # ToDo this might need refactoring since binding consumption to Trip Class is not versatile
    # in case of parallel schedules / scenarios, since both access the same Consumption
    # setup consumption calculator that can be accessed by all trips
    simba.trip.Trip.consumption = data_container.to_consumption()

    options = copy(django_scenario.simba_options)
    try:
        del options["electrified_stations"]
    except (TypeError, KeyError):
        pass

    schedule = SimbaSchedule(stations=stations_dict, vehicle_types=vehicle_types, **options)
    schedule.station_data = station_data

    # get SimBA rotations and trips from db
    rotations = get_rotations_and_trips_from_db(django_scenario, schedule, station_data)
    schedule.rotations = rotations

    add_temperatures_to_trips(django_scenario, schedule)

    # schedule.original_rotations = deepcopy(rotations)
    # Database does not store information about "original rotations yet"
    schedule.original_rotations = None

    args = get_args(django_scenario=django_scenario)
    # filter rotations
    schedule.rotation_filter(args)

    # calculate consumption of all trips
    schedule.calculate_consumption()

    # Create soc dispatcher
    schedule.init_soc_dispatcher(args)

    # Database should contain assigned vehicles already
    for rot in schedule.rotations.values():
        assert rot.vehicle_id is not None

    # This is done since vehicle counts were generated in vehicle_assignment.
    # ToDo replace with simba call
    # Calculate vehicle counts
    # count number of vehicles per type
    # used for unique vehicle id e.g. vehicletype_chargingtype_id
    vehicle_type_counts = {
        f"{vehicle_type}_{charging_type}": 0
        for vehicle_type, charging_types in schedule.vehicle_types.items()
        for charging_type in charging_types.keys()
    }
    unique_vids = {rot.vehicle_id for rot in schedule.rotations.values()}
    for vid in unique_vids:
        v_ls = vid.split("_")
        vehicle_type_counts[f"{v_ls[0]}_{v_ls[1]}"] += 1
    schedule.vehicle_type_counts = vehicle_type_counts

    # schedule.assign_only_new_vehicles()

    return schedule, args


def get_rotations_and_trips_from_db(django_scenario, schedule, station_data) -> dict:
    """Create simba rotations with trips from a database with a scenario as a key

    :param django_scenario: Django scenario
    :param schedule: SimBA Schedule
    :param station_data: dictionary with all stations and elevation
    :return: rotations
    :rtype: dict
    """
    rotations = {}
    lines_dict = {line.id: line for line in Line.objects.filter(scenario=django_scenario)}

    for rot in Rotation.objects.filter(scenario=django_scenario).select_related(
        "vehicle_type", "vehicle"
    ):
        vehicle_type = rot.vehicle_type.name_short
        # Use the id/pk instead of the name, since names might not be unique, when database is
        # filled with non simba ingesters
        simba_id = rot.id
        simba_rotation = SimbaRotation(
            id=simba_id,
            vehicle_type=vehicle_type,
            schedule=schedule,
        )
        simba_rotation.vehicle_id = rot.vehicle.to_simba_name()
        simba_rotation.charging_type = (
            EnumChargeType.OPPORTUNITY.value
            if rot.allow_opportunity_charging
            else EnumChargeType.DEPOT.value
        )

        rotations[simba_id] = simba_rotation

        # select related means later db access can be skipped
        query = (
            Trip.objects.filter(rotation=rot)
            .select_related("route__arrival_station", "route__departure_station", "route__line")
            .order_by("arrival_time")
        )

        for trip in query:
            simba_trip_dict = {
                "departure_time": str(trip.departure_time),
                "departure_name": trip.route.departure_station.name,
                "arrival_time": str(trip.arrival_time),
                "arrival_name": trip.route.arrival_station.name,
                "distance": trip.route.distance,
                "line": lines_dict[trip.route.line.id].name,
                "height_diff": (
                    station_data[trip.route.arrival_station.name]["elevation"]
                    - station_data[trip.route.departure_station.name]["elevation"]
                ),
                "level_of_loading": trip.loaded_mass,
                "mean_speed": trip.speed * 3.6,
                "temperature": 20.0,
            }
            simba_rotation.add_trip(simba_trip_dict)
    return rotations


def get_vehicle_types_from_db(django_scenario) -> dict:
    """Create simba rotations with trips from database with scenario as key

    :param django_scenario: Django scenario
    :return: vehicle_types
    :rtype: dict
    """
    vehicle_types = dict()
    for vehicle_type in VehicleType.objects.filter(scenario=django_scenario):
        charge_type = (
            EnumChargeType.OPPORTUNITY.value
            if vehicle_type.opportunity_charging_capable
            else EnumChargeType.DEPOT.value
        )
        try:
            vehicle_types[vehicle_type.name_short]
        except KeyError:
            vehicle_types[vehicle_type.name_short] = dict()

        mileage = vehicle_type.consumption
        query = VehicleClass.objects.filter(vehicle_types=vehicle_type).exclude(consumption=None)
        if len(query) > 0:
            assert mileage is None
            assert len(query) == 1
            mileage = Consumption.objects.get(vehicle_class=query[0]).name

        vehicle_types[vehicle_type.name_short][charge_type] = {
            "name": vehicle_type.name,
            "capacity": vehicle_type.battery_capacity,
            "charging_curve": vehicle_type.charging_curve,
            "min_charging_power": vehicle_type.minimum_charging_power,
            "v2g": (vehicle_type.v2g_curve is not None),
            # ToDo use vehicle to grid curve
            # vehicle_to_grid_curve ....
            "mileage": mileage,
            "battery_efficiency": vehicle_type.charging_efficiency,
        }
    return vehicle_types


def get_electrified_stations_from_db(django_scenario) -> dict:
    """Create simba electrified stations from database with scenario as key

    :param django_scenario: Django scenario
    :return: electrified_stations
    :rtype: dict
    """
    stations_dict = dict()
    for station in Station.objects.filter(scenario=django_scenario):
        if not station.is_electrified:
            continue
        stat_dict = {
            "type": charge_type_from_db_to_station(station.charge_type.lower(), is_station=True),
            "n_charging_stations": station.amount_charging_places,
            "cs_power_deps_oppb": station.power_per_charger,
            "cs_power_deps_depb": station.power_per_charger,
            "cs_power_opps": station.power_per_charger,
            "gc_power": station.power_total,
            "voltage_level": station.voltage_level,
        }
        stations_dict[station.name] = stat_dict
    return stations_dict


def get_station_data_from_db(django_scenario) -> dict:
    """Create station_data from database with scenario as key

    :param django_scenario: Django scenario
    :return: station_data
    :rtype: dict
    """
    station_data = dict()
    for station in Station.objects.filter(scenario=django_scenario):
        station_data[station.name] = {
            "long": station.geom.x,
            "lat": station.geom.y,
            "elevation": station.geom.z,
        }

    return station_data


def get_schedule_from_args(
    original_args, django_scenario
) -> tuple[simba.schedule.Schedule, Namespace]:
    """Create SimBA Schedule from arguments with existing files and restrict features.

    This creates a schedule object without making use of the database. Instead the passed arguments
    and files are used.
    :param django_scenario: Django scenario
    :return: simba schedule and args
    :rtype: simba.schedule.Schedule, Namespace
    """

    data_container = DataContainer()
    data_container.add_vehicle_types_from_json(original_args.vehicle_types_path)
    consumptions = Consumption.objects.filter(scenario__in=[django_scenario, None])
    for consumption in consumptions:
        data_container.add_consumption_data(consumption.name, consumption.to_df())

    simba_schedule, new_args = simba.simulate.pre_simulation(original_args, data_container)

    # # Set Up Consumption to use database / file values
    # some_trip = next(iter(next(iter(simba_schedule.rotations.values())).trips))
    # simba_consumption = some_trip.__class__.consumption
    # consumptions = Consumption.objects.filter(scenario=django_scenario)
    # for consumption in consumptions:
    #     simba_consumption.set_consumption_interpolation(consumption.name, consumption.to_df())

    simba_schedule.assign_only_new_vehicles()

    add_temperatures_to_trips(django_scenario, simba_schedule)
    # Changed temperatures can affect the consumption
    simba_schedule.calculate_consumption()

    # Remove simba features which are not used
    remove_station_attributes = ["battery", "energy_feed_in", "distance_to_grid", "external_load"]
    for attribute in remove_station_attributes:
        stations_copy = deepcopy(simba_schedule.stations)
        for key, station in stations_copy.items():
            if attribute in station:
                del simba_schedule.stations[key][attribute]

    # Mutate values according to models and make values explicit, i.e. dictionary contains all
    # values, even if defaults would be used anyway. This makes sure the database contains all
    # the needed information to recreate the exact scenario.
    # ToDo: Another way could be to use properties in the models which look up scenario provided
    # data if no specific data (e.g. station.cs_power) is provided.
    for station in simba_schedule.stations.values():
        if str(station["type"]).lower() != "opps":
            try:
                station["cs_power_deps_depb"] = station["cs_power_opps"] = station[
                    "cs_power_deps_oppb"
                ]
            except KeyError:
                station["cs_power_deps_depb"] = station["cs_power_opps"] = station[
                    "cs_power_deps_oppb"
                ] = original_args.cs_power_deps_oppb
        else:
            try:
                station["cs_power_opps"] = station["cs_power_opps"]
            except KeyError:
                station["cs_power_opps"] = original_args.cs_power_opps
                station["cs_power_deps_depb"] = station["cs_power_deps_oppb"] = station[
                    "cs_power_opps"
                ]

        try:
            station["voltage_level"] = station["voltage_level"]
        except KeyError:
            station["voltage_level"] = original_args.default_voltage_level

        try:
            station["gc_power"] = station["gc_power"]
        except KeyError:
            station["gc_power"] = vars(original_args).get(
                "gc_power_" + str(station["type"]).lower()
            )

    return simba_schedule, new_args


def add_temperatures_to_trips(django_scenario, simba_schedule):
    temperatures = Temperatures.objects.get(scenario=django_scenario)
    # set temperatures according to temperature file
    for rot in simba_schedule.rotations.values():
        for trip in rot.trips:
            # ToDo: Make times from db unaware once? so every function does not have to check
            # for awareness? or other way around. make simba times aware early?
            middle_time = trip.departure_time + 0.5 * (trip.arrival_time - trip.departure_time)
            temp_time = middle_time if is_aware(middle_time) else make_aware(middle_time)
            trip.temperature = temperatures.get_interpolated_temperature(temp_time)


def get_args(django_scenario) -> Namespace:
    """Creates arguments from django Scenario

    Creates arguments for SimBA by getting default arguments from simba, updating them with
    the options from the django_scenario and

    :param django_scenario: Scenario in the django database
    :type django_scenario: models.Scenario
    :return:
    """
    # Get parser from SimBA
    parser = simba.util.get_parser()
    # Read the parse values, in this case the default values
    args, _ = parser.parse_known_args()
    # Overwrite args with scenario specific data
    vars(args).update(vars(Namespace(**django_scenario.simba_options)))
    # arguments relevant to SpiceEV, setting automatically to reduce clutter in config
    simba.util.mutate_args_for_spiceev(args)

    return args


def scenario_to_db(cleaned_data, request) -> Scenario:
    scenario = Scenario.objects.create(name=cleaned_data["title"])
    args = dict(cleaned_data)
    args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(",")))
    # decimal -> float
    for k, v in args.items():
        if type(v) is Decimal:
            args[k] = float(v)
    # set default files if not given
    for k, v in {
        "input_schedule": "trips_example.csv",
        "electrified_stations": "electrified_stations.json",
        "vehicle_types_path": "vehicle_types.json",
        "station_data_path": "all_stations.csv",
        "outside_temperature_over_day_path": "default_temp_summer.csv",
        "consumption_path": "energy_consumption_example.csv",
        "temperature_time_series_path": "temperature_time_series.csv",
        "level_of_loading_over_day_path": "default_level_of_loading_over_day.csv",
        "cost_parameters_file": "cost_params.json",
        "optimizer_config": "default_optimizer.cfg",
    }.items():
        if args[k]:
            # uploaded file: store in upload folder
            f = UploadedFile.objects.create(scenario=scenario, file=request.FILES[k])
            args[k] = f.file.path
            continue
        p = Path(settings.STATIC_URL, __package__, "examples", v)
        if settings.DEBUG:
            # use app static folder
            if p.is_absolute():
                # remove first slash
                p = Path(str(p)[1:])
            p = Path(settings.BASE_DIR, __package__, p)
        if not p.exists():
            print(f"FILE ERROR: {k} COULD NOT BE SET ({str(p)})")
            continue
        args[k] = str(p)
    scenario.simba_options = args
    scenario.save()

    return scenario


def schedule_to_db(schedule: simba.schedule.Schedule, django_scenario: Scenario) -> None:
    """Takes a simba Schedule and writes it into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    model_rotations = []
    model_trips = []
    model_lines = []
    model_routes = []
    rot_id = 1 if Rotation.objects.last() is None else Rotation.objects.last().id + 1
    trip_id = 1 if Trip.objects.last() is None else Trip.objects.last().id + 1

    station_dict = Station.objects.filter(scenario=django_scenario)
    station_dict = {station.name: station for station in station_dict}
    line_dict = {}
    for key, rot in tqdm.tqdm(schedule.rotations.items(), total=len(schedule.rotations)):
        assert rot.charging_type in EnumChargeType.values
        assert rot.vehicle_id is not None
        opportunity_charging_capable = rot.charging_type == EnumChargeType.OPPORTUNITY.value
        vehicletype = VehicleType.objects.get(
            scenario=django_scenario,
            name_short=rot.vehicle_type,
            opportunity_charging_capable=opportunity_charging_capable,
        )

        vehicle = Vehicle.objects.create(
            vehicle_type=vehicletype, scenario=django_scenario, name=rot.vehicle_id
        )
        r = Rotation(
            name=key,
            vehicle_type=vehicletype,
            scenario=django_scenario,
            allow_opportunity_charging=opportunity_charging_capable,
            vehicle=vehicle,
        )
        r.id = rot_id
        rot_id += 1
        model_rotations.append(r)

        trips = sorted(rot.trips, key=lambda x: x.arrival_time)
        for trip in trips:
            # Get the proper Line
            if trip.line in line_dict:
                line = line_dict[trip.line]
            else:
                line = Line(scenario=django_scenario, name=trip.line)
                line_dict[trip.line] = line
                model_lines.append(line)
            route = Route(
                name=trip.departure_name + " - " + trip.arrival_name,
                scenario=django_scenario,
                departure_station=station_dict[trip.departure_name],
                arrival_station=station_dict[trip.arrival_name],
                distance=trip.distance,
                line=line,
            )
            model_routes.append(route)
            # ToDo: loaded_mass is level_of_loading * vehicle_capacity[kg]
            # ToDo: How do we know if its a type, e.g. passanger trip or not ? Right now instance
            #  uses default passanger_trip
            t = Trip(
                rotation=r,
                route=route,
                scenario=django_scenario,
                departure_time=make_aware(trip.departure_time),
                arrival_time=make_aware(trip.arrival_time),
                loaded_mass=trip.level_of_loading,
            )

            t.id = trip_id
            model_trips.append(t)
            trip_id += 1
    Line.objects.bulk_create(model_lines)
    Route.objects.bulk_create(model_routes)
    Rotation.objects.bulk_create(model_rotations)
    Trip.objects.bulk_create(model_trips)


def vehicles_to_db(vehicle_types: dict, scenario: Scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """

    # ToDo: Get real data
    DEFAULT_WIDTH = 2.54
    DEFAULT_HEIGHT = 3.375

    for name, v_type in vehicle_types.items():
        for charge_name, charge_type in v_type.items():
            consumption = None
            mileage_text = charge_type.get("mileage")

            add_to_vehicle_class = False
            try:
                consumption = float(mileage_text)
            except ValueError:
                # The milage can be a link/ str to a consumption_table.In this case link
                # the VehicleClass with this name to this vehicle
                add_to_vehicle_class = True
                pass
            params = dict(
                name=charge_type.get("name", "unnamed bus"),
                name_short=name,
                scenario=scenario,
                opportunity_charging_capable=(charge_name.lower() == "oppb"),
                battery_capacity=charge_type["capacity"],
                charging_efficiency=charge_type.get("battery_efficiency", 0.95),
                minimum_charging_power=charge_type.get("min_charging_power"),
                charging_curve=charge_type["charging_curve"],
                v2g_curve=charge_type.get("v2g_curve", None),
                consumption=consumption,
                length=charge_type.get("length", 0),
                width=DEFAULT_WIDTH,
                height=DEFAULT_HEIGHT,
            )
            vt = VehicleType.objects.create(**params)
            if add_to_vehicle_class:
                VehicleClass.objects.get(scenario=scenario, name=mileage_text).vehicle_types.add(
                    vt, through_defaults=None
                )


def stations_to_db(station_data, electrified_stations, scenario):
    """Takes a dictionary of vehicle types and writes them into the db with the scenario as handle
    :param schedule: simba Schedule
    :param scenario: django model Scenario
    :return: None
    """
    object_list = []
    try:
        last_id = Station.objects.aggregate(Max("id"))["id__max"]
        if last_id is None:
            last_id = -1
    except Exception:
        last_id = -1
    for key, station in station_data.items():
        last_id += 1
        try:
            long = float(station["long"])
            lat = float(station["lat"])
            elevation = float(station["elevation"])
            geom = GEOSGeometry(f"POINT({long} {lat} {elevation})")
            params = dict(id=last_id, scenario=scenario, geom=geom, name=str(key))
            object_list.append(Station(**params))
        except Exception:
            print(traceback.format_exc())
            pass
    Station.objects.bulk_create(object_list)

    # Update db stations which are electrified with info from electrified_stations dictionary
    update_electrified_stations_db(electrified_stations, scenario)


def update_electrified_stations_db(electrified_stations, scenario):
    """Update stations which are electrified with info from electrified_stations dictionary"""
    for name, ele_station in electrified_stations.items():
        station = Station.objects.get(name=name, scenario=scenario)
        station.is_electrified = True

        charge_type = ele_station.get("type")
        # SimBA calls station types opps and deps which is not the same as
        # EnumChargeTypes. This needs a translation.
        station.charge_type = charge_type_from_simba_to_db(charge_type)

        station.voltage_level = ele_station.get(
            "voltage_level", scenario.simba_options.get("default_voltage_level")
        )
        station.amount_charging_places = ele_station.get("n_charging_stations")
        # ToDo how do we handle differences in charging power depending on oppb or depb
        if station.charge_type == EnumChargeType.OPPORTUNITY.value:
            power_per_charger = ele_station.get("cs_power_opps")
        else:
            power_per_charger = ele_station.get("cs_power_deps_oppb")
        station.power_per_charger = power_per_charger
        station.power_total = ele_station.get(
            "gc_power", scenario.simba_options.get("gc_power_" + station.charge_type)
        )
        station.save()


def generate_zipped_scenario(task_id: str):
    if settings.CELERY_USE:
        print("Using Celery")
        _celery_generate_zipped_scenario.apply_async((str(task_id),), task_id=task_id)
    else:
        _generate_zipped_scenario(task_id)


def _generate_zipped_scenario(task_id: str):
    task_id = str(task_id)
    folder_path = Path(settings.UPLOAD_PATH, task_id)
    output_path = settings.MEDIA_ROOT / (task_id + ".zip")
    if not folder_path.exists():
        print("input folder for zipping not found")
        return
    if output_path.is_file():
        print("Zip already exists")
        return
    shutil.make_archive(output_path.with_suffix(""), "zip", folder_path)


@shared_task(bind=True)
def init_db_with_trips(self, scenario_id: int, reader_num: int, files: dict, cleaned_data):
    progress = Progress.objects.create(task_id=self.request.id, status="Starting")
    # files is a dict with values of (path, file_id)
    file_paths = {key: value[0] for key, value in files.items()}
    try:
        schedule_reader_factory = schedule_readers.get_schedule_reader_factory(reader_num)
        schedule_reader: ScheduleReader = schedule_reader_factory(**file_paths, **cleaned_data)
        schedule_reader.set_observer(progress)
        scenario = Scenario.objects.get(id=scenario_id)
        progress.scenario = scenario
        progress.save()
        delete_old_scenario_data(scenario)
        # Read the file and write it to database
        progress.refresh_from_db()
        progress.success = schedule_reader.write_to_db(scenario.id)
        progress.save()
    except Exception as e:
        traceback.print_exc()
        progress.status = "Failed"
        progress.errors.append(str(e))
    finally:
        try:
            progress.errors.extend(schedule_reader.get_errors())
        except:  # noqa
            pass
        progress.status = "Finished"
        if not progress.success:
            progress.status = "Failed"
        # delete all uploaded files
        try:
            for file_path, file_id in files.values():
                UploadedFile.objects.get(id=file_id).delete()
        except Exception:
            traceback.print_exc()
        progress.running = False
        progress.save()

        # Make sure postgres auto increment is up to date
        core.deepcopy.reset_postgres_auto_increments(["ebustoolbox"])


@atomic()
def delete_old_scenario_data(scenario: Scenario):
    Rotation.objects.filter(scenario=scenario).delete()
    Station.objects.filter(scenario=scenario).delete()
    VehicleType.objects.filter(scenario=scenario).delete()
    Vehicle.objects.filter(scenario=scenario).delete()
    Trip.objects.filter(scenario=scenario).delete()
    Route.objects.filter(scenario=scenario).delete()
    Line.objects.filter(scenario=scenario).delete()


@shared_task(bind=True)
def _celery_generate_zipped_scenario(self, task_id: str):
    _generate_zipped_scenario(task_id)


def run_ebus_toolchain(task_id):
    _ = _run_ebus_toolchain.apply_async((str(task_id),), task_id=str(task_id))


def vary_depot_rotations(schedule) -> "collections.Iterable[SimbaRotation]":
    """Generator that creates schedules with varying vehicle types for"""
    # Keep original rotations to restore them later and keep track of depot rotations
    orig_rotations = deepcopy(schedule.rotations)
    # depot rotations
    depot_rotations = {
        r_id: rotation
        for r_id, rotation in orig_rotations.items()
        if rotation.charging_type == "depb"
    }
    for rot_id, rotation in depot_rotations.items():
        vt = rotation.vehicle_type
        # Iterate over both charging types of this vehicle type, e.g., depot and opp bus.
        for charging_type in EnumChargeType.values:
            # Skip rotation with a vehicle type / charging type combination, if it does not exist
            try:
                schedule.vehicle_types[vt][charging_type]
            except KeyError:
                continue
            # in case of a depot rotation, the vehicle type is adjusted and both
            # charging types are used, even the "oppb". This way calculate_consumption() also
            # calculates the "non-charging" consumption of a depot rotation which is run with
            # an opportunity bus.
            if orig_rotations[rot_id].charging_type == EnumChargeType.DEPOT:
                # Charging type is mutated, since this is used to determine the exact vehicle
                schedule.rotations[rot_id].charging_type = charging_type
                schedule.rotations[rot_id].vehicle_type = vt
                yield schedule.rotations[rot_id]
    # Restore rotations before leaving generator
    schedule.rotations = orig_rotations


def run_toolchain_from_scenario(django_scenario: Scenario, assign_vehicles=False):
    """Run a Scenario from the database with SimBA

    The provided scenario must contain all information including Temperatures, Vehicle_Types,
    station information and electrified_station information.
    :param django_scenario: Scenario which is simulated
    :param assign_vehicles: boolean if the vehicles should be added to rotations.
    Previous assignments will be deleted
    :return:
    """
    if assign_vehicles:
        assign_new_vehicles_to_db(django_scenario)
    run_ebus_toolchain(django_scenario.task_id)


def run_simba_scenario(django_scenario: Scenario, assign_vehicles=False):
    """Run a Scenario from the database with SimBA

    The provided scenario must contain all information including Temperatures, Vehicle_Types,
    station information and electrified_station information.
    :param django_scenario: Scenario which is simulated
    :param assign_vehicles: boolean if the vehicles should be added to rotations.
    Previous assignments will be deleted
    :return:
    """
    if assign_vehicles:
        assign_new_vehicles_to_db(django_scenario)
    simba_schedule_db, args_db = get_schedule_from_db(django_scenario)
    run_simba(simba_schedule_db, args_db, django_scenario.task_id)


def assign_new_vehicles_to_db(django_scenario: Scenario) -> None:
    """Assign a new vehicle to every rotation

    Already assigned vehicles are deleted
    :param django_scenario: Scenario that gets added vehicles and rotation assignments.
    :return: None
    """
    Vehicle.objects.filter(scenario=django_scenario).delete()
    # ToDo bulk updating. replace with independent simba vehicle naming
    for i, r in enumerate(Rotation.objects.filter(scenario=django_scenario)):
        vt = r.vehicle_type
        v_name = "Vehicle_" + str(i)
        vehicle = Vehicle.objects.create(scenario=django_scenario, vehicle_type=vt, name=v_name)
        r.vehicle = vehicle
        r.save()


@shared_task(bind=True)
def _run_ebus_toolchain(self, task_id):
    """Run the tool chain"""
    db_scenario = Scenario.objects.get(task_id=task_id)
    schedule, args = get_schedule_from_db(db_scenario)
    # django_scenario = Scenario.objects.get(task_id=task_id)
    # schedule, args = get_schedule_from_args(args, django_scenario)
    # call simba and eflips
    wanted_modes = args.modes.split(",")
    assert wanted_modes[-1] == "report"
    simba_scenario = None

    # Chain of modes with mode->eflips -> sim. Last mode is "report" and can be outside of loop
    for mode in wanted_modes[:-1]:
        # Delete old events
        Event.objects.filter(scenario=db_scenario).delete()

        schedule, simba_scenario = run_simba(
            schedule, args, task_id, mode=mode, scenario=simba_scenario
        )
        # ToDo remove Flag. Eflips should not stay optional
        # ToDo Maybe create class SimulationMode: which couple logical toolchain steps, e.g.
        # simba.sim -> eflips -> simba.sim
        # simba.station_opt -> eflips -> simba.sim
        if settings.EFLIPS_USE:
            run_eflips(task_id)
            eflips_assignment = get_assigned_vehicles(task_id)
            schedule.assign_vehicles_for_django(eflips_assignment)
            schedule, simba_scenario = run_simba(schedule, args, task_id, mode="sim")

    # Post Processing or Clean Up ########
    # ToDo: Report run might be removed at some point
    _, __ = run_simba(schedule, args, task_id, mode=wanted_modes[-1], scenario=simba_scenario)

    db_scenario.refresh_from_db()
    db_scenario.finished = timezone.now()
    db_scenario.save()


def get_assigned_vehicles(task_id: str) -> List[dict]:
    """
    Retrieves assigned vehicles for a given task ID, considering previous events.

    Args:
        task_id (str): The ID of the task associated with the scenario.

    Returns:
        List[dict]: A list of dictionaries containing assigned vehicle information, including
        rotation name, vehicle ID, and state of charge (SOC) at the end of the previous event.

    Raises:
        Scenario.DoesNotExist: If the scenario with the specified task ID does not exist.

    Note:
        This function retrieves assigned vehicles based on the given task ID and considers previous
        events to determine the state of charge (SOC) at the end of the previous event.
    """

    scenario = Scenario.objects.get(task_id=task_id)
    used_vehicles = Vehicle.objects.filter(rotation__scenario=scenario).distinct()
    # Delete the old vehicles which are not used anymore
    Vehicle.objects.filter(scenario=scenario).exclude(id__in=used_vehicles).delete()
    events = Event.objects.filter(scenario=scenario)

    all_rotations = Rotation.objects.filter(scenario=scenario)
    vehicle_assigns = []
    vehicle_counter_dict = {
        v.vehicle_type.name_short: {EnumChargeType.OPPORTUNITY: 0, EnumChargeType.DEPOT: 0}
        for v in used_vehicles
    }
    counted_vehicles = set()
    for rot in all_rotations:
        first_trip = Trip.objects.filter(rotation=rot).order_by("departure_time").first()
        vehicle = rot.vehicle
        # ToDo vehicle does not have a short name from eflips. Save initializes it. Simba needs
        # a special vehicle name for vehicle identification
        if vehicle not in counted_vehicles:
            vt = vehicle.vehicle_type
            if vt.opportunity_charging_capable:
                ct = EnumChargeType.OPPORTUNITY.value
            else:
                ct = EnumChargeType.DEPOT.value

            vehicle_counter_dict[vt.name_short][ct] += 1
            counted_vehicles.add(vehicle)

        prev_event = (
            events.filter(time_end__lte=first_trip.departure_time, vehicle=vehicle)
            .order_by("time_end")
            .last()
        )

        vehicle_assigns.append(
            {"rot": rot.id, "v_id": vehicle.to_simba_name(), "soc": prev_event.soc_end}
        )
    return vehicle_assigns


def run_simba(schedule: SimbaSchedule, args, task_id, mode=None, scenario=None):
    print(f"Running Simba {datetime.now()}")
    # TODO don't overwrite output on multiple function calls
    args.output_directory = Path(settings.UPLOAD_PATH) / str(task_id)
    args.attach_vehicle_soc = True

    db_scenario = Scenario.objects.get(task_id=task_id)
    if mode is None:
        scenario = schedule.run(args)
    else:
        func = getattr(simba.simulate.Mode, mode)
        # Run this mode. Iteration number is not changed right now since only the last report is
        # used from the generated simba files
        schedule, scenario = func(schedule, scenario, args, 1)
        match mode:
            case "sim" | "report":
                pass
            case "station_optimization":
                update_electrified_stations_db(schedule.stations, db_scenario)
            case _:
                raise NotImplementedError

    print(f"Plotting {datetime.now()}")

    db_scenario.finished = timezone.now()
    db_scenario.save()

    print(f"Creating Simba Events {datetime.now()}")

    create_event_output(scenario, task_id)

    reset_postgres_auto_increments(apps=[Event._meta.app_label])
    return schedule, scenario


def opportunity_rotation_to_eflips_input(
    db_rotation, db_scenario, input_for_eflips, rot_id, rotation, scenario, schedule
):
    input_for_eflips = copy(input_for_eflips)
    v_soc, start, end = simba.optimizer_util.get_rotation_soc_util(
        rot_id=rot_id, schedule=schedule, scenario=scenario
    )
    # Start is the first index during the rotation, with a decreased soc already, therefore
    # use the index before
    start_idx = max(start - 1, 0)
    rot_soc = v_soc[start_idx:end]
    vehicle_type_db = VehicleType.objects.get(
        scenario=db_scenario,
        name_short=rotation.vehicle_type,
        opportunity_charging_capable=True,
    )
    input_for_eflips[db_rotation.id] = dict(
        departure_soc=rot_soc[0],
        arrival_soc=rot_soc[-1],
        minimal_soc=min(rot_soc),
        charging_type=rotation.charging_type,
        vehicle_type=vehicle_type_db.id,
    )
    return input_for_eflips


def depot_rotation_to_eflips_input(db_rotation, db_scenario, input_for_eflips, rotation, schedule):
    input_for_eflips = copy(input_for_eflips)
    input_for_eflips[db_rotation.id].update(
        departure_soc=schedule.min_recharge_deps_depb,
        charging_type="depb",
    )
    vehicle_type_db = VehicleType.objects.get(
        scenario=db_scenario,
        name_short=rotation.vehicle_type,
        opportunity_charging_capable=(rotation.charging_type == "oppb"),
    )
    input_for_eflips[db_rotation.id]["vehicle_type"].append(vehicle_type_db.id)
    vehicle = schedule.vehicle_types[rotation.vehicle_type][rotation.charging_type]
    input_for_eflips[db_rotation.id]["delta_soc"].append(rotation.consumption / vehicle["capacity"])
    return input_for_eflips


def run_eflips(task_id) -> None:
    # ToDo Replace with logger
    print(f"Running eflips {datetime.now()}")
    db_scenario = Scenario.objects.get(task_id=task_id)

    # Constructing the database URL manually
    db_url = create_db_url()
    generate_depot_layout(
        db_scenario, database_url=db_url, charging_power=90, delete_existing_depot=True
    )
    simulate_scenario(db_scenario, database_url=db_url)


def create_db_url():
    db_dict = settings.DATABASES["default"]
    engine = db_dict["ENGINE"].split(".")[-1]
    # sqlalchemy needs a translation of the engine
    if engine in ["postgres", "postgis"]:
        engine = "postgresql"
    db_url = (
        f"{engine}://{db_dict['USER']}:{db_dict['PASSWORD']}@{db_dict['HOST']}/{db_dict['NAME']}"
    )
    return db_url


def get_timestep(simba_scenario: "SimbaScenario", timestamp: datetime) -> int:
    """Returns time steps into the scenario for a given scenario and datetime"""
    # calculate the corresponding time step
    timedelta_into_scenario = timestamp - simba_scenario.start_time
    minutes_into_scenario = timedelta_into_scenario.total_seconds() / 60
    return round(minutes_into_scenario * (simba_scenario.stepsPerHour / 60))


def get_datetime(simba_scenario: "SimbaScenario", timestep: int) -> datetime:
    """Returns datetime for a given scenario and time steps into the scenario"""
    # calculate the corresponding datetime
    minutes = timestep * (60 / simba_scenario.stepsPerHour)
    return simba_scenario.start_time + timedelta(minutes=minutes)


def create_event_output(simba_scenario: "SimbaScenario", task_id):  # noqa: C901
    # collect data from DB
    db_scenario = Scenario.objects.get(task_id=task_id)
    vehicle_dict = Vehicle.objects.filter(scenario=db_scenario)
    vehicle_dict = {vehicle.to_simba_name(): vehicle for vehicle in vehicle_dict}
    vehicle_type_dict = VehicleType.objects.filter(scenario=db_scenario)
    vehicle_type_dict = {
        vehicle_type.name_short: vehicle_type for vehicle_type in vehicle_type_dict
    }

    vehicle_events = [e for e in simba_scenario.events.vehicle_events]

    # Departures and arrivals with the same vehicle_id and start time are ordered
    # "arrival" -> "departure".
    # this assumes there are no 0-duration trips but 0 duration stops
    vehicle_events = sorted(
        vehicle_events,
        key=lambda e: (e.vehicle_id, e.start_time, ["arrival", "departure"].index(e.event_type)),
    )

    last_id = None
    counter = 0

    for i, e in enumerate(vehicle_events):
        if last_id != e.vehicle_id:
            counter = 0
            last_id = e.vehicle_id
        assert e.event_type == ["departure", "arrival"][counter % 2], str(i) + str(counter)
        counter += 1

    vehicle_trips_dict = dict()
    current_rotation = None
    events = []
    event_id = 1 if Event.objects.last() is None else Event.objects.last().id + 1
    last_arrival_time = None
    current_vehicle = None
    last_aware = None
    for counter, vehicle_event in enumerate(vehicle_events):
        start_timestep = get_timestep(simba_scenario, vehicle_event.start_time)

        event_time = vehicle_event.start_time
        aware_start_time = make_aware(event_time) if not is_aware(event_time) else event_time

        try:
            if vehicle_events[counter + 1].vehicle_id == vehicle_event.vehicle_id:
                end_time = vehicle_events[counter + 1].start_time
            else:
                end_time = simba_scenario.stop_time
        except IndexError:
            end_time = simba_scenario.stop_time

        vehicle = vehicle_dict[vehicle_event.vehicle_id]
        if current_vehicle is not None:
            # current vehicle is set to None if a new rotation is reached.
            # during a rotation the vehicle must be the same
            assert (
                vehicle == current_vehicle
            ), f"{counter} {vehicle} , {last_aware}, {aware_start_time}"
        vehicle_trips = vehicle_trips_dict.get(vehicle, None)
        if vehicle_trips is None:
            vehicle_trips_dict[vehicle] = Trip.objects.filter(rotation__vehicle=vehicle)
            vehicle_trips = vehicle_trips_dict[vehicle]

        # trips are sorted by time. all trips before the current rotation end time belong to the
        # same rotation
        if current_rotation is None:
            # first event must be a departure
            assert vehicle_event.event_type == "departure"
            current_rotation = vehicle_trips.get(departure_time=aware_start_time).rotation
            current_vehicle = vehicle

            last_trip = (
                Trip.objects.filter(rotation=current_rotation).order_by("arrival_time").last()
            )

            last_arrival_time = last_trip.arrival_time
            # print(current_rotation,last_trip.departure_time, last_arrival_time)

        if aware_start_time >= last_arrival_time:
            current_rotation = None
            current_vehicle = None
            # Do not save events passed their rotation time. This is done by eflips
            continue
        else:
            last_aware = aware_start_time

        # Skip events with no duration
        if vehicle_event.start_time == end_time:
            continue

        end_timestep = min(get_timestep(simba_scenario, end_time), simba_scenario.step_i - 1)
        simba_vehicle_type = vehicle_event.vehicle_id.split("_")[0]
        vehicle_type = vehicle_type_dict[simba_vehicle_type]

        # figure out the location of the event
        station = None
        trip = None
        if not len(vehicle_trips):
            raise RuntimeError(
                f"No trip assigned to vehicle {vehicle.name_short}/ID:{vehicle.id} found in database."
            )

        if vehicle_event.event_type == "arrival":
            station = vehicle_trips.get(arrival_time=aware_start_time).route.arrival_station
            is_charging = vehicle_event.update["connected_charging_station"] is not None
            event_type = (
                EventType.CHARGING_OPPORTUNITY if is_charging else EventType.STANDBY_DEPARTURE
            )
        elif vehicle_event.event_type == "departure":
            trip = vehicle_trips.get(departure_time=aware_start_time)
            event_type = EventType.DRIVING
        else:
            raise NotImplementedError("Unknown vehicle event type")

        timestamp_list = [
            get_datetime(simba_scenario, t).astimezone().isoformat()
            for t in range(start_timestep, end_timestep + 1, int(60 / simba_scenario.stepsPerHour))
        ]
        timeseries = {
            "time": timestamp_list,
            "soc": simba_scenario.vehicle_socs[vehicle.to_simba_name()][
                start_timestep : end_timestep + 1
            ],
        }
        if None in timeseries["soc"]:
            print("Warning: None Values found in timeseries")
            forward_fill_last_value(timeseries["soc"])
        # grab current vehicle SoC at timestep
        soc_start = timeseries["soc"][0]
        soc_end = timeseries["soc"][-1]
        if None in timeseries["soc"]:
            raise Exception(
                f"{vehicle.to_simba_name()}/{vehicle.id} has None values in between socs"
            )
        event = Event(
            id=event_id,
            scenario=db_scenario,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            station=station,
            trip=trip,
            soc_start=soc_start,
            soc_end=soc_end,
            time_start=vehicle_event.start_time.astimezone(),
            time_end=end_time.astimezone(),
            timeseries=timeseries,
            event_type=event_type,
        )
        event_id += 1
        events.append(event)
    Event.objects.bulk_create(events)


def forward_fill_last_value(list_with_nones):
    """Forward fill the last non None value

    :param list_with_nones: List containing nones at the end
    :return: list without None values at the end
    """
    for idx in range(len(list_with_nones) - 1, -1, -1):
        last_soc = list_with_nones[idx]
        last_idx = idx
        if last_soc is not None:
            break
    else:
        raise Exception("Timeseries has only None values as soc")
    list_with_nones[last_idx:] = [last_soc for _ in range(last_idx, len(list_with_nones))]
