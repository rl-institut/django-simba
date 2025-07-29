"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""

import warnings
import logging
import datetime

import numpy as np
import sqlalchemy
from django.db.models import Prefetch, Sum, F, FloatField, ExpressionWrapper
from django.db.models.functions import Coalesce, Extract
from sqlalchemy.orm import Session

from ebustoolbox.models import (
    Scenario,
    Event,
    get_longest_distance_rotation,
    get_shortest_distance_rotation,
    Vehicle,
    VehicleType,
    Rotation,
    Station,
    EventType,
    Trip,
    Route,
    EnumChargeType,
)

import pandas as pd
from dash.exceptions import PreventUpdate

from eflips.depot.api import simulate_scenario  # noqa
from eflips.eval.output.prepare import power_and_occupancy

# Maximum number of cached results per function
MAX_SIZE = 10
# stores scenario_id and finished time
last_simulations = list()
CRITICAL_SOC = 0.0
logger = logging.getLogger("custom")


class SqlAlchemyEngine:
    engine: None | sqlalchemy.engine.Engine = None

    @staticmethod
    def get_engine() -> sqlalchemy.engine.Engine:
        """
        Create a sqlalchemy engine from the DATABASE_URL environment variable.
        Replace the 'postgis' scheme with 'postgresql'
        """
        if not __class__.engine:
            from ebustoolbox.tasks import create_db_url

            db_url = create_db_url()
            __class__.engine = sqlalchemy.create_engine(db_url)
        return __class__.engine

    @staticmethod
    def dispose() -> None:
        if __class__.engine:
            __class__.engine.dispose()


def vid_human_readable(vehicle: Vehicle, counter, name="", c_type=False, rotation=None) -> str:
    # Create a user friendly vehicle identifier for plotting

    if c_type:
        c_type_str = "Oppb"
    else:
        c_type_str = "Depb"

    running_id = counter

    identifier = str(name) + "_" + c_type_str + "_" + str(running_id) + "_" + str(vehicle.id)

    if rotation:
        return identifier + " in Rotation " + str(rotation.id)
    else:
        return identifier


def get_total_consumption(s: Scenario):
    vehicles = Vehicle.objects.filter(scenario_id=s.id)

    df = recent_memoizer(get_all_event_info, s.id)(s.id)

    # Convert time columns to datetime
    df["time_start"] = pd.to_datetime(df["time_start"])
    df["time_end"] = pd.to_datetime(df["time_end"])

    # Filter the dataframe for 'DRIVING' events
    driving_events = df[df["event_type"] == "DRIVING"]

    # Fetch battery capacity and charging efficiency for all vehicle types
    vehicle_types = VehicleType.objects.in_bulk([vehicle.vehicle_type_id for vehicle in vehicles])
    battery_capacities = {
        v_id: vehicle_types[v_type_id].battery_capacity
        for v_id, v_type_id in zip(
            vehicles.values_list("id", flat=True),
            vehicles.values_list("vehicle_type_id", flat=True),
        )
    }

    total_energy_difference = 0

    for index, event in driving_events.iterrows():
        # Calculate the difference between soc_end and soc_start
        total_energy_difference += (
            abs(event["soc_start"] - event["soc_end"]) * battery_capacities[event["V_id"]]
        )
    return total_energy_difference


def get_all_buses(task_id: str) -> list[str]:
    """
    Retrieves a list of all buses associated with a specific task ID.

    :param task_id: The ID of the task.
    :type task_id: str
    :return: A list of short names of all buses associated with the task.
    :rtype: list[str]
    """
    s = Scenario.objects.get(task_id=task_id)
    all_buses = list(Vehicle.objects.filter(scenario=s).values_list("id", flat=True))
    return all_buses


def get_all_buses_labeled(task_id: str) -> list[dict]:
    """
    Retrieves two dictionaries with all buses and their labels in both directions.

    :param task_id: The ID of the task.
    :type task_id: str
    :return: A list of short names of all buses associated with the task.
    :rtype: tuple[dict, dict]
    """
    s = Scenario.objects.get(task_id=task_id)
    v_name_dict, v_name_dict_reverse = recent_memoizer(get_vehicle_dictionaries, s.id)(s.id)
    return [v_name_dict, v_name_dict_reverse]


def get_rotation_dictionaries(scenario_id: int) -> tuple[dict, dict]:
    rotations = Rotation.objects.filter(scenario_id=scenario_id)

    rotation_name_dict = dict()
    rotation_name_dict_reverse = dict()
    for r in rotations:
        label_name = f"{r.name}_{r.id}"
        rotation_name_dict[r.id] = label_name
        rotation_name_dict_reverse[label_name] = r.id

    assert len(rotation_name_dict) == len(rotation_name_dict_reverse)
    return rotation_name_dict, rotation_name_dict_reverse


def get_vehicle_dictionaries(scenario_id: int) -> tuple[dict, dict]:
    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    # Fetch all vehicle types
    vehicle_types = VehicleType.objects.in_bulk({vehicle.vehicle_type_id for vehicle in vehicles})
    vehicles = sorted(vehicles, key=lambda v: v.vehicle_type_id)

    vehicle_name_dict = dict()
    vehicle_name_dict_reverse = dict()
    for i, v in enumerate(vehicles):
        vt: VehicleType = vehicle_types[v.vehicle_type_id]
        label_name = vid_human_readable(
            v, i + 1, name=vt.name, c_type=vt.opportunity_charging_capable
        )
        vehicle_name_dict[v.id] = label_name
        vehicle_name_dict_reverse[label_name] = v.id

    assert len(vehicle_name_dict) == len(vehicle_name_dict_reverse)
    return vehicle_name_dict, vehicle_name_dict_reverse


def get_number_of_buses(filter_dict: dict) -> list[str]:
    """
    Gets the longest rotation distance and its associated name based on the provided filter criteria.

    :param filter_dict: A dictionary containing filter criteria, task_id and vehicle__id__in.
    :type filter_dict: dict
    :return: A list containing the name and distance of the longest rotation in the format:
        ["Longest Rotation rotation_name", "distance m"]
    :rtype: list[str]
    """
    task_id = filter_dict.pop("task_id")

    return f"{len(get_all_buses(task_id))}"


def get_number_of_stations(task_id: str, get_electrified=True) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    # Count all Station objects for the scenario
    total_stations = Station.objects.filter(scenario_id=s.id).count()

    if get_electrified:
        # Count Station objects where is_electrified is True for the scenario
        electrified_stations = Station.objects.filter(scenario_id=s.id, is_electrified=True).count()

        return f"{electrified_stations} / {total_stations}"

    else:
        return total_stations


def get_frequently_served_station(task_id: str) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    df = recent_memoizer(get_all_routes, s.id)(s.id)

    # Finding the most common item in a specific column
    most_common_station = df["arrival_station_id"].mode()[0]
    frequency = df["arrival_station_id"].value_counts()[most_common_station]

    station = Station.objects.get(scenario_id=s.id, id=most_common_station)

    return f"{station.name},  {frequency} mal"


def get_scenario_duration(task_id: str) -> dict:
    s = Scenario.objects.get(task_id=task_id)
    df = recent_memoizer(get_all_event_info, s.id)(s.id)

    # Convert time columns to datetime
    df["time_start"] = pd.to_datetime(df["time_start"])
    df["time_end"] = pd.to_datetime(df["time_end"])

    start = df["time_start"].min()
    end = df["time_end"].max()

    duration = end - start

    result_dict = {"start": start, "end": end, "duration": duration}
    return result_dict


def get_number_longest_rot(filter_dict: dict):
    """
    Gets the longest rotation distance and its associated name based on the provided filter criteria.

    Args:
        filter_dict (dict): A dictionary containing filter criteria, task_id and vehicle__id__in.

    Returns:
        list[str]: A list containing the name and distance of the longest rotation in the format:
            ["Longest Rotation rotation_name", "distance m"]
    """

    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s

    if (
        "vehicle__id__in" in filter_dict and len(filter_dict["vehicle__id__in"]) == 0
    ) and sim_is_finished(task_id):
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    longest_rotation = get_longest_distance_rotation(filter_dict)

    if longest_rotation and longest_rotation.distance:
        return f"{longest_rotation.name}: {longest_rotation.distance / 1000:.1f}"
    else:
        return ["Keine Rotation gefunden!"]


def get_number_shortest_rot(filter_dict: dict):
    """
    Gets the shortest rotation distance and its associated name based on the provided filter criteria.

    :param filter_dict: A dictionary containing filter criteria, task_id and vehicle__id__in.
    :type filter_dict: dict
    :return: A list containing the name and distance of the shortest rotation in the format:
        ["Shortest Rotation rotation_name", "distance m"]
    :rtype: list[str]
    """
    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s

    if (
        "vehicle__id__in" in filter_dict and len(filter_dict["vehicle__id__in"]) == 0
    ) and sim_is_finished(task_id):
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    shortest_rotation = get_shortest_distance_rotation(filter_dict)

    # Add style if text should have special style
    if shortest_rotation and shortest_rotation.distance:
        return f"{shortest_rotation.name}: {shortest_rotation.distance / 1000:.1f}"
    else:
        return ["Keine Rotation gefunden!"]


def recent_memoizer(function, scenario_id, _dcache1=dict(), _result_cache2=dict()):  # noqa
    """Decorator function

    :param function: function do be decorated
    :type function: function
    :param _dcache1: storage of recent calls
    :param _result_cache2: storage of results of recent calls
    :return: decorated function or timer if given function is None
    :rtype function or dict

    """
    # Clean up cache if Scenario changed, i.e. finished time changed
    scenario = Scenario.objects.get(id=scenario_id)
    try:
        index = [s[0] for s in last_simulations].index(scenario_id)
        # result data is not up to date
        if not scenario.finished == last_simulations[index][1]:
            last_simulations.pop(index)
            last_simulations.append((scenario_id, scenario.finished))
            for function_key, all_f_args in _dcache1.copy().items():
                f_args_w_scenario_id = filter(lambda x: x[0] == scenario_id, all_f_args)
                logger.debug("Deleting deprecated scenario ", scenario_id)
                for f_args in f_args_w_scenario_id:
                    try:
                        _dcache1[function_key].remove(f_args)
                    except ValueError:
                        pass
                    try:
                        del _result_cache2[function_key][f_args]
                    except KeyError:
                        pass
            last_simulations.pop(index)
            last_simulations.append((scenario_id, scenario.finished))
    except ValueError:
        last_simulations.append((scenario_id, scenario.finished))

    # Cap size of list
    if len(last_simulations) >= MAX_SIZE:
        last_simulations.pop(0)

    def decorated_function(*this_args, **kwargs):
        key = function.__name__
        if key not in _dcache1:
            _dcache1[key] = list()
            _result_cache2[key] = dict()

        inputs = tuple((scenario_id, this_args, *list(kwargs)))

        if inputs in _dcache1[key]:
            _dcache1[key].remove(inputs)
            _dcache1[key].append(inputs)
            if inputs in _result_cache2[key]:
                logger.debug(f"Using cache for {key} for scenario {scenario_id}")
                return _result_cache2[key][inputs]
        else:
            # Storage is full. Delete oldest storage
            if len(_dcache1[key]) >= MAX_SIZE:
                logger.debug("Storage full, deleting", scenario_id)
                try:
                    del _result_cache2[key][_dcache1[key][0]]
                except KeyError:
                    pass
                try:
                    del _dcache1[key][0]
                except IndexError:
                    pass
            _dcache1[key].append(inputs)
        logger.debug(f"Calculating {key} for scenario {scenario_id}")
        return_value = function(*this_args, **kwargs)
        _result_cache2[key][inputs] = return_value
        return return_value

    return decorated_function


def get_soc_as_dataframe(scenario_id, buses):
    """
    Retrieves state of charge (SOC) data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve SOC data for.
    :type buses: list[str]

    :return: DataFrame containing SOC data for specified buses.
    :rtype: pandas.DataFrame
    """

    result_df = recent_memoizer(get_all_event_info, scenario_id)(scenario_id)
    filtered_df = result_df.query(f"V_id in {buses}")
    return filtered_df


def get_duration_as_dataframe(scenario_id, buses):
    """
    Retrieves duration data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve duration data for.
    :type buses: list[str]

    :return: DataFrame containing duration data for specified buses.
    :rtype: pandas.DataFrame
    """
    result_df = recent_memoizer(get_all_trip_info, scenario_id)(scenario_id)

    result_df = result_df.groupby(["R_id", "V_id"])["duration"].sum().reset_index()
    filtered_df = result_df.query(f"V_id in {buses}")
    return filtered_df


def get_distances_as_dataframe(scenario_id, buses):
    """
    Retrieves distance data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve distance data for.
    :type buses: list[str]

    :return: DataFrame containing distance data for specified buses.
    :rtype: pandas.DataFrame
    """
    result_df = recent_memoizer(get_all_trip_info, scenario_id)(scenario_id)

    result_df = result_df.groupby(["R_id", "V_id"])["total_distance"].sum().reset_index()
    filtered_df = result_df.query(f"V_id in {buses}")
    return filtered_df


def get_activities_as_dataframe(scenario_id, buses):
    """
    Retrieves activity data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve activity data for.
    :type buses: list[str]

    :return: DataFrame containing activity data for specified buses.
    :rtype: pandas.DataFrame
    """
    result_df = recent_memoizer(get_all_event_info, scenario_id)(scenario_id)
    vehicle_labels, _ = recent_memoizer(get_vehicle_dictionaries, scenario_id)(scenario_id)

    filtered_df = result_df.query(f"V_id in {buses}")
    return filtered_df


def get_powerdraw_as_dataframe(scenario_id, buses=None):
    """
    Retrieves power draw data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve power draw data for.
    :type buses: list[str]

    :return: DataFrame containing power draw data for specified buses.
    :rtype: pandas.DataFrame
    """
    df = recent_memoizer(get_all_powerdraw_as_dataframe, scenario_id)(scenario_id)
    if buses is not None:
        df = df.query(f"V_id in {buses}")
    return df


def get_vehicle_types(scenario_id, buses):
    """
    Retrieves vehicle types and their counts as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to filter the vehicle types.
    :type buses: list[str]

    :return: DataFrame containing vehicle types and their counts.
    :rtype: pandas.DataFrame
    """
    filter_dict = dict(scenario_id=scenario_id)
    vehicle_type_counts = []
    for vt in VehicleType.objects.filter(**filter_dict):
        count = Vehicle.objects.filter(pk__in=buses, vehicle_type=vt).count()
        if count == 0:
            continue
        vehicle_type_counts.append({"name": vt.name, "count": count})

    df = pd.DataFrame(vehicle_type_counts)
    return df


def get_critical_rotations_as_dataframe(scenario_id, buses):
    """
    Retrieves per-rotation criticality information for specified buses in a scenario.

    :param scenario_id: The ID of the scenario.
    :param buses: List of bus IDs to include.
    :return: DataFrame with R_id, V_id, soc_end, and SOC_category columns.
    """
    result_df = recent_memoizer(get_all_event_info, scenario_id)(scenario_id)

    df = result_df[result_df["V_id"].isin(buses)]

    df = df.explode("R_id")
    df["R_id"] = df["R_id"].apply(apply_id)

    df = df.groupby(["R_id", "V_id"])["soc_end"].min().reset_index()

    df["SOC_category"] = df["soc_end"].apply(
        lambda x: "Nicht kritisch" if x > CRITICAL_SOC else "kritisch"
    )

    print("critical_df:", df)

    return df


def apply_id(rotation):
    try:
        return rotation.id
    except AttributeError:
        return None


def get_critical_rotations_and_score_as_dataframe(scenario_id, buses):
    """
    TODO
    """
    result_df = recent_memoizer(get_all_event_info, scenario_id)(scenario_id)

    df = result_df[result_df["V_id"].isin(buses)]

    df = df.explode("R_id")
    df["R_id"] = df["R_id"].apply(apply_id)

    # If events with no rotation should be returned dropna needs to be False
    df = df.groupby(["R_id", "V_id"], dropna=True)["soc_end"].min().reset_index()
    df = pd.DataFrame(df)
    v_dict, _ = recent_memoizer(get_vehicle_dictionaries, scenario_id)(scenario_id)
    r_dict, _ = recent_memoizer(get_rotation_dictionaries, scenario_id)(scenario_id)

    df["V_id"] = df["V_id"].apply(lambda x: v_dict[x])
    df["R_id"] = df["R_id"].apply(lambda x: r_dict[x])

    return df


def get_all_routes(scenario_id):
    qs = Route.objects.filter(scenario_id=scenario_id)

    # Convert the QuerySet to a DataFrame
    data = list(qs.values())
    df = pd.DataFrame(data)

    return df


def get_all_event_info(scenario_id):
    """
    Retrieves event information for all vehicles in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: int

    :return: DataFrame containing event information for all vehicles.
    :rtype: pandas.DataFrame
    """
    # Fetch vehicles and scenario
    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)

    # Fetch all events and rotations in advance
    all_events = Event.objects.filter(scenario=scenario, vehicle__isnull=False).prefetch_related(
        "vehicle"
    )
    all_rotations = Rotation.objects.filter(
        scenario=scenario, vehicle__isnull=False
    ).prefetch_related("vehicle")

    # Organize events by vehicle_id
    events_by_vehicle = {}
    for event in all_events:
        vehicle_id = event.vehicle_id
        if vehicle_id not in events_by_vehicle:
            events_by_vehicle[vehicle_id] = []
        events_by_vehicle[vehicle_id].append(event)

    # Get the translations from v.id to readable vehicle_name
    vehicle_name_dict, _ = recent_memoizer(get_vehicle_dictionaries, scenario_id)(scenario_id)

    # Initialize lists to store data
    dfs = []
    first_warning = True

    # Iterate over vehicles
    for vehicle in vehicles:
        v_id = vehicle.id
        if vehicle.id in events_by_vehicle:
            # Filter rotations for the current vehicle
            vehicle_rotations = all_rotations.filter(vehicle_id=vehicle.id)
            # Dictionary which finds the rotation according to the event time

            rotation_times = dict()
            for rot in vehicle_rotations:
                if rot in rotation_times:
                    continue
                trips = Trip.objects.filter(rotation=rot).order_by("departure_time")
                rstart = trips.first().departure_time
                rend = trips.last().arrival_time
                rotation_times[rot] = rstart, rend

            events = events_by_vehicle[vehicle.id]
            for event in events:
                time_start = event.time_start
                event_type = event.event_type
                duration = (event.time_end - event.time_start).total_seconds()
                time_end = event.time_end
                # Fetch events for the current rotation
                vehicle_rotation = None
                for rot, times in rotation_times.items():
                    if time_start >= times[0] and time_end <= times[1]:
                        if vehicle_rotation is not None:
                            raise Exception("Multiple rotations detected")
                        vehicle_rotation = rot

                else:
                    if vehicle_rotation is None and first_warning:
                        warnings.warn(
                            f"No rotation detected for event {event}. "
                            f"Similar warnings will be omitted."
                        )
                        vehicle_rotation = None
                        first_warning = False
                suffix = ""
                if vehicle_rotation is not None:
                    suffix = f" in Rotation {vehicle_rotation.id}"
                dfs.append(
                    {
                        "V_id": v_id,
                        "time_start": time_start,
                        "time_end": time_end,
                        "duration": duration,
                        "event_type": event_type,
                        "soc_start": event.soc_start,
                        "soc_end": event.soc_end,
                        "R_id": vehicle_rotation,
                        "readable_name": vehicle_name_dict[vehicle.id] + suffix,
                    }
                )

    # Create DataFrame from collected data
    if dfs:
        result_df = pd.DataFrame(dfs).drop_duplicates()
        result_df["time_start"] = pd.to_datetime(result_df["time_start"])
        result_df["time_end"] = pd.to_datetime(result_df["time_end"])
        result_df = result_df.sort_values(by="time_start")

    else:
        result_df = pd.DataFrame(
            {
                "V_id": [None],
                "R_id": [None],
                "time_end": [None],
                "time_start": [None],
                "duration": [None],
                "event_type": [None],
                "soc_start": [None],
                "soc_end": [None],
                "readable_name": [None],
            }
        )

    return result_df


def get_all_trip_info(scenario_id):
    """
    Retrieves trip related information for all vehicles in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str

    :return: DataFrame containing event information for all vehicles.
    :rtype: pandas.DataFrame
    """
    # Prefetch all related objects for the scenario
    scenario = Scenario.objects.prefetch_related(
        "rotation_set__trip_set__route",  # Prefetch trips and their routes
        "rotation_set__vehicle",  # Prefetch vehicles for rotations
    ).get(id=scenario_id)

    # Initialize lists to store data
    v_ids = []
    r_ids = []
    distances = []
    durations = []

    # Iterate over rotations in the scenario
    for rotation in scenario.rotation_set.all():
        # Get vehicle ID for the rotation
        v_id = rotation.vehicle_id
        r_id = rotation.id
        # Iterate over trips in the rotation
        for trip in rotation.trip_set.all():
            # Get distance for the trip's route
            distance = trip.route.distance
            duration = (trip.arrival_time - trip.departure_time).total_seconds()

            # Append data to lists
            v_ids.append(v_id)
            r_ids.append(r_id)
            distances.append(distance)
            durations.append(duration)

    # Create DataFrame from collected data
    result_df = pd.DataFrame(
        {"V_id": v_ids, "R_id": r_ids, "total_distance": distances, "duration": durations}
    )

    return result_df


def get_all_powerdraw_as_dataframe(scenario_id):
    """
    Retrieves charging information for all vehicles in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str

    :return: DataFrame containing event information for all vehicles.
    :rtype: pandas.DataFrame
    """
    # Fetch vehicles and scenario
    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    all_stations = Station.objects.filter(scenario_id=scenario_id)
    stations_name_short_dict = {}
    for station in all_stations:
        if station.name_short is not None:
            stations_name_short_dict[station.id] = station.name_short
        else:
            stations_name_short_dict[station.id] = station.name

    # Fetch battery capacity and charging efficiency for all vehicle types
    vehicle_types = VehicleType.objects.in_bulk([vehicle.vehicle_type_id for vehicle in vehicles])
    battery_capacities = {
        v_id: vehicle_types[v_type_id].battery_capacity
        for v_id, v_type_id in zip(
            vehicles.values_list("id", flat=True),
            vehicles.values_list("vehicle_type_id", flat=True),
        )
    }
    charging_efficiencies = {
        v_id: vehicle_types[v_type_id].charging_efficiency
        for v_id, v_type_id in zip(
            vehicles.values_list("id", flat=True),
            vehicles.values_list("vehicle_type_id", flat=True),
        )
    }

    # Fetch all events for the scenario with prefetching
    all_events = Event.objects.filter(scenario=scenario, vehicle__isnull=False).prefetch_related(
        "vehicle"
    )

    # Initialize list to store DataFrames
    dfs = []

    # Iterate over vehicles
    for vehicle in vehicles:
        v_id = vehicle.id
        batterycapacity = battery_capacities[v_id]
        charge_eff = charging_efficiencies[v_id]

        # Filter events for the current vehicle from the prefetched queryset and
        # charging in some way
        events = []
        for event in all_events:
            if event.vehicle_id == v_id and event.event_type in [
                EventType.CHARGING_DEPOT,
                EventType.CHARGING_OPPORTUNITY,
            ]:
                events.append(event)

        for event in events:
            time_start = event.time_start
            time_end = event.time_end

            if event.event_type == EventType.CHARGING_DEPOT:
                station = event.area.depot.station
            else:
                station = event.station

            soc_start = event.soc_start
            soc_end = event.soc_end
            if soc_end > soc_start:
                energy = (soc_end - soc_start) * charge_eff * batterycapacity
                # Append data to the list
                if len(dfs) > 0:
                    # update the last disconnection of the dataframe with new start event time
                    dfs[-1]["time_end"] = time_start
                dfs.append(
                    {
                        "V_id": vehicle.id,
                        "time_start": time_start,
                        "time_end": time_end,
                        "Power": energy / ((time_end - time_start).total_seconds() / 3600),
                        "Station_id": stations_name_short_dict.get(station.id),
                    }
                )
                # Disconnection of vehicle after event. Copy last event and change power
                dfs.append(dfs[-1].copy())
                dfs[-1]["time_start"] = time_end
                dfs[-1]["Power"] = 0

    # Create DataFrame from collected data
    if dfs:
        result_df = pd.DataFrame(dfs).drop_duplicates()
        result_df["time_start"] = pd.to_datetime(result_df["time_start"])
        result_df["time_end"] = pd.to_datetime(result_df["time_end"])
        result_df = result_df.sort_values(by="time_start")
    else:
        result_df = pd.DataFrame(
            {
                "V_id": [None],
                "time_end": [None],
                "time_start": [None],
                "Energy": [None],
                "Station_id": [None],
            }
        )

    return result_df


def sim_is_finished(task_id):
    return Scenario.objects.filter(task_id=task_id, finished__isnull=False).exists()


def _get_soc(task_id: str) -> pd.DataFrame:
    """
    Build and return a long-form DataFrame with columns:
        V_id | timestamp | soc
    Each rotation contributes two rows (start & end).
    """
    s = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    df = get_soc_as_dataframe(s.id, buses)

    df = df[["V_id", "time_end", "soc_end", "time_start", "soc_start"]].copy()

    # Convert timestamps to Unix ms
    df["timestamp_end"] = pd.to_datetime(df["time_end"]).astype("int64") // 10**6
    df["timestamp_start"] = pd.to_datetime(df["time_start"]).astype("int64") // 10**6

    # Build long-form: one row per timestamp/soc event
    df_long = pd.concat(
        [
            df.rename(columns={"timestamp_start": "timestamp", "soc_start": "soc"})[
                ["V_id", "timestamp", "soc"]
            ],
            df.rename(columns={"timestamp_end": "timestamp", "soc_end": "soc"})[
                ["V_id", "timestamp", "soc"]
            ],
        ]
    )

    return df_long.sort_values(["V_id", "timestamp"]).reset_index(drop=True)


def get_soc_as_df(task_id: str) -> pd.DataFrame:
    return _get_soc(task_id)


def get_soc_as_json(task_id: str) -> dict:
    df_long = _get_soc(task_id)

    grouped = (
        df_long.groupby("V_id").apply(lambda g: g[["timestamp", "soc"]].values.tolist()).to_dict()
    )

    return {"data": grouped}


def _get_binned_soc(task_id: str) -> pd.DataFrame:
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())
    df = get_soc_as_dataframe(scenario.id, buses)

    df["timestamp"] = pd.to_datetime(df["time_start"])
    df = df[["V_id", "timestamp", "soc_end"]]

    all_hours = pd.date_range(
        start=df["timestamp"].min().floor("h"), end=df["timestamp"].max().ceil("h"), freq="1h"
    )

    filled_dfs = []
    for vid, group in df.groupby("V_id"):
        group = group.set_index("timestamp").sort_index()

        hourly_min = group["soc_end"].resample("1h").min()
        hourly_min = hourly_min.reindex(all_hours)
        hourly_filled = hourly_min.ffill()

        tmp = hourly_filled.reset_index()
        tmp.columns = ["timestamp", "soc_end"]
        tmp["V_id"] = vid

        filled_dfs.append(tmp)

    df_filled = pd.concat(filled_dfs, ignore_index=True)
    df_filled = df_filled.dropna(subset=["soc_end"])

    df_filled["hour"] = df_filled["timestamp"].dt.hour

    def soc_bin(soc):
        if soc < 0:
            return "<0"
        return f"{int((soc * 100) // 10) * 10}"

    df_filled["soc_bin"] = df_filled["soc_end"].apply(soc_bin)

    return df_filled


def get_binned_soc_as_df(task_id: str) -> pd.DataFrame:
    return _get_binned_soc(task_id)


def get_binned_soc_as_json(task_id: str) -> list[dict]:
    df = _get_binned_soc(task_id)

    grouped = (
        df.groupby(["hour", "soc_bin"]).size().reset_index(name="count").to_dict(orient="records")
    )

    return grouped


def get_power_draw_as_json(request, task_id: str):
    scenario = Scenario.objects.get(task_id=task_id)

    buses = request.GET.getlist("buses[]")
    df = get_powerdraw_as_dataframe(scenario.id, buses)

    df["time_start"] = pd.to_datetime(df["time_start"])
    df["time_end"] = pd.to_datetime(df["time_end"])

    charging_status = []

    all_times = pd.date_range(start=df["time_start"].min(), end=df["time_end"].max(), freq="min")

    for time_point in all_times:
        charging_vehicles = df[
            (df["time_start"] <= time_point) & (df["time_end"] > time_point) & (df["Power"] > 0)
        ]
        total_power = charging_vehicles["Power"].sum()
        charging_status.append({"time": time_point.isoformat(), "total_power": total_power})

    return charging_status


def _get_event_gantt(task_id: str) -> pd.DataFrame:
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    df = get_activities_as_dataframe(scenario.id, buses)

    df["time_start"] = pd.to_datetime(df["time_start"])
    df["time_end"] = pd.to_datetime(df["time_end"])

    return df


def get_event_gantt_as_json(task_id: str) -> tuple[list[str], list[dict]]:
    df = _get_event_gantt(task_id)

    buses = df["V_id"].unique()
    categories = [f"Bus {bus}" for bus in buses]

    gantt_data = []
    bus_index_map = {bus: idx for idx, bus in enumerate(buses)}

    for _, row in df.iterrows():
        start_time = int(row["time_start"].timestamp() * 1000)
        end_time = int(row["time_end"].timestamp() * 1000)
        duration = row["duration"]
        bus_index = bus_index_map[row["V_id"]]

        gantt_data.append(
            {
                "name": row["readable_name"],
                "value": [bus_index, start_time, end_time, duration],
                "event_type": row["event_type"],
            }
        )

    return categories, gantt_data


def get_event_gantt_as_df(task_id: str) -> pd.DataFrame:
    return _get_event_gantt(task_id)


def _get_stats_as_json(task_id: str):
    scenario = Scenario.objects.get(task_id=task_id)

    filter_dict = dict(task_id=task_id)

    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    if buses:  # In Presim buses will be None, if later no buses are selected, it will be empty
        filter_dict["vehicle__id__in"] = buses

    longest_rot = get_number_longest_rot(filter_dict.copy())
    shortest_rot = get_number_shortest_rot(filter_dict.copy())
    num_busses = get_number_of_buses(filter_dict.copy())
    most_freq = get_frequently_served_station(task_id)

    dist_df = get_distances_as_dataframe(scenario.id, buses)
    total_dist = round(dist_df["total_distance"].sum() / 1000, 0)

    stations = scenario.station_set.all()
    depots = scenario.depot_set.all()
    num_electrified_opps = stations.filter(charge_type=EnumChargeType.OPPORTUNITY).count()
    events = scenario.event_set.select_related("vehicle_type").all()
    # calculate charged energy for all events
    events = events.annotate(
        charged=(F("soc_end") - F("soc_start")) * F("vehicle_type__battery_capacity"),
        # Convert the duration to seconds and then divide by 3600 to get hours
        duration_seconds=Extract(F("time_end") - F("time_start"), "epoch"),
        duration_hours=(F("duration_seconds") / 3600),
        charging_power=ExpressionWrapper(
            (F("charged") / F("duration_hours")), output_field=FloatField()
        ),
    )

    # Calculate sum of charged energy for different event types
    energy_opps = events.filter(event_type=EventType.CHARGING_OPPORTUNITY).aggregate(
        sum_charged=Coalesce(Sum("charged"), 0.0)
    )["sum_charged"]

    energy_deps = events.filter(event_type=EventType.CHARGING_DEPOT).aggregate(
        sum_charged=Coalesce(Sum("charged"), 0.0)
    )["sum_charged"]

    # Aggregate total installed power
    # first, for vehicles with non-null amount of charging spaces
    stations = scenario.station_set.all()
    opp_stations = stations.filter(charge_type=EnumChargeType.OPPORTUNITY)

    installed_power = opp_stations.annotate(
        charger_count=F("amount_charging_places"),
        charger_power=F("power_per_charger"),
        installed_power=ExpressionWrapper(
            F("amount_charging_places") * F("power_per_charger"), output_field=FloatField()
        ),
    ).aggregate(total_installed_power=Coalesce(Sum("installed_power"), 0.0))[
        "total_installed_power"
    ]

    # Some stations may not have a specified amount of chargers,
    # the maximum amount of simultaneously charging buses is determined
    charging_events = events.filter(event_type=EventType.CHARGING_OPPORTUNITY).select_related(
        "station"
    )

    stations_with_null_amount = opp_stations.filter(
        amount_charging_places__isnull=True,
    ).prefetch_related(Prefetch("event_set", queryset=charging_events, to_attr="charging_events"))

    station_peak_chargers = {}

    for station in stations_with_null_amount:
        timeline = []
        for event in station.charging_events:
            timeline.append((event.time_start, +1))  # Charger starts
            timeline.append((event.time_end, -1))  # Charger ends

        # Sort timeline
        timeline.sort()
        concurrent = 0
        peak = 0
        for _, delta in timeline:
            concurrent += delta
            peak = max(peak, concurrent)

        station_peak_chargers[station.id] = peak

    fallback_power = sum(
        peak * station.power_per_charger
        for station in stations_with_null_amount
        if station.power_per_charger and (peak := station_peak_chargers.get(station.id))
    )

    total_installed_power = installed_power + fallback_power

    # Average consumption
    driving_events = events.filter(event_type=EventType.DRIVING)

    total_energy_used = driving_events.aggregate(sum_energy=Coalesce(Sum("charged"), 0.0))[
        "sum_energy"
    ]

    average_consumption = total_energy_used / total_dist

    # Query the Area table using SQLAlchemy
    all_areas = scenario.area_set.all()
    all_area_ids = [area.id for area in all_areas]
    with Session(SqlAlchemyEngine.get_engine()) as session:
        prepared_data = power_and_occupancy(all_area_ids, session)

    # Extract the 'power' column and find the maximum value
    peak_power_kw = prepared_data["power"].max()

    resp = {
        "longest_rotation": longest_rot,
        "shortest_rotation": shortest_rot,
        "total_dist": total_dist,
        "num_stations": f"{num_electrified_opps} / {stations.count() - depots.count()}",
        "num_busses": num_busses,
        "most_frequented": most_freq,
        "total_consumption": np.round(energy_deps + energy_opps, 0),
        "avg_consumption": np.abs(np.round(average_consumption, 3)),
        "installed_power": np.round(total_installed_power, 0),
        "depot_energy": np.round(energy_deps, 0),
        "peak_depot_power": np.round(peak_power_kw, 0),
    }

    return resp

def get_stats_as_json(task_id: str):
    return _get_stats_as_json(task_id)


def get_stats_as_df(task_id: str):
    stats_dict = _get_stats_as_json(task_id)

    stat_rename = {
        "longest_rotation": "Längster Umlauf, Name: Länge (km)",
        "shortest_rotation": "Kürzester Umlauf, Name: Länge (km)",
        "total_dist": "Gesamtdistanz (km)",
        "num_stations": "Anzahl elektrifizierter Stationen / Anzahl aller Stationen",
        "num_busses": "Anzahl Busse",
        "most_frequented": "Am häufigsten angefahrene Station",
        "total_consumption": "Gesamtverbrauch (kWh)",
        "avg_consumption": "Durchschnittserbrauch (kWh/km)",
        "installed_power": "Installierte Leistung (kW)",
        "depot_energy": "Depotverbrauch (kWh)",
        "peak_depot_power": "Spitzenleistung Depot (kW)",
    }

    # Rename keys to German
    renamed_items = [(stat_rename.get(k, k), v) for k, v in stats_dict.items()]

    return pd.DataFrame(renamed_items, columns=["Statistik", "Wert"])


def _get_speed_hist(task_id: str):
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    dur_df = get_duration_as_dataframe(scenario.id, buses)
    dist_df = get_distances_as_dataframe(scenario.id, buses)

    # Calculate average speed in km/h
    dur_df["avg_speed_kmh"] = (dist_df["total_distance"] / 1000) / (dur_df["duration"] / 3600)

    # Bin speeds
    bin_width_kmh = 10
    max_speed_kmh = dur_df["avg_speed_kmh"].max()
    bins = np.arange(0, max_speed_kmh + bin_width_kmh, bin_width_kmh)

    hist, bin_edges = np.histogram(dur_df["avg_speed_kmh"], bins=bins)

    bin_labels = [
        f"{bin_edges[i]:.1f}-{bin_edges[i + 1]:.1f} km/h" for i in range(len(bin_edges) - 1)
    ]

    return bin_labels, hist.tolist()


def get_speed_hist_as_json(task_id: str):
    bin_labels, counts = _get_speed_hist(task_id)
    return {"bins": bin_labels, "counts": counts}


def get_speed_hist_as_df(task_id: str):
    bin_labels, counts = _get_speed_hist(task_id)
    return pd.DataFrame({"Geschwindigkeitsspanne (km/h)": bin_labels, "Anzahl": counts})


def _get_dist_hist(task_id: str) -> pd.DataFrame:
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    df = get_distances_as_dataframe(scenario.id, buses)
    critical_df = get_critical_rotations_as_dataframe(scenario.id, buses)

    df = df.rename(columns={"rotation_id": "R_id"})
    df["total_distance_km"] = df["total_distance"] / 1000

    merged_df = df.merge(critical_df, how="left", on="R_id")
    merged_df["SOC_category"] = merged_df["SOC_category"].fillna("Nicht kritisch")

    bin_width_km = 50
    max_distance_km = merged_df["total_distance_km"].max()
    bins = np.arange(0, max_distance_km + bin_width_km, bin_width_km)

    bin_labels = [f"{bins[i]:.1f}-{bins[i+1]:.1f} km" for i in range(len(bins) - 1)]
    merged_df["distance_bin"] = pd.cut(
        merged_df["total_distance_km"],
        bins=bins,
        labels=bin_labels,
        include_lowest=True,
    )

    grouped = (
        merged_df.groupby(["distance_bin", "SOC_category"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["Nicht kritisch", "kritisch"], fill_value=0)
    )

    grouped.index.name = "distanz bin"

    return grouped


def get_dist_hist_as_df(task_id: str) -> pd.DataFrame:
    return _get_dist_hist(task_id)


def get_dist_hist_as_json(task_id: str) -> tuple[list[str], dict[str, list[int]]]:
    df = _get_dist_hist(task_id)

    bins = df.index.tolist()
    data_dict = {
        "Nicht kritisch": df["Nicht kritisch"].tolist(),
        "kritisch": df["kritisch"].tolist(),
    }

    return bins, data_dict


def _get_power_draw_and_occ(task_id: str) -> pd.DataFrame:
    scenario = Scenario.objects.get(task_id=task_id)
    area_ids = [area.id for area in scenario.area_set.all()]

    with Session(SqlAlchemyEngine.get_engine()) as session:
        df = power_and_occupancy(area_ids, session)

    return df


def get_power_draw_and_occ_as_df(task_id: str) -> pd.DataFrame:
    return _get_power_draw_and_occ(task_id)


def get_power_draw_and_occ_as_json(task_id: str) -> list[dict]:
    df = _get_power_draw_and_occ(task_id)
    return df.to_dict(orient="records")


def _get_soc_gantt(task_id: str):
    scenario = Scenario.objects.get(task_id=task_id)
    events = (
        scenario.event_set.exclude(vehicle=None)
        .order_by("vehicle__id", "time_start")
        .select_related("vehicle")
    )

    df = recent_memoizer(get_all_event_info, scenario.id)(scenario.id)\

    print(events)
    print(df.columns)

    records = []
    for event in events:
        vehicle_id = event.vehicle.id
        tz_start = event.time_start
        tz_end = event.time_end

        if not event.timeseries or "time" not in event.timeseries or "soc" not in event.timeseries:
            records.append(
                {
                    "vehicle": vehicle_id,
                    "start": tz_start.isoformat(),
                    "end": tz_end.isoformat(),
                    "soc_start": event.soc_start,
                    "soc_end": event.soc_end,
                }
            )
            continue

        times = [datetime.datetime.fromisoformat(t) for t in event.timeseries["time"]]
        socs = event.timeseries["soc"]
        if len(times) != len(socs):
            continue

        times = [tz_start] + times + [tz_end]
        socs = [event.soc_start] + socs + [event.soc_end]

        for i in range(len(times) - 1):
            records.append(
                {
                    "vehicle": vehicle_id,
                    "start": times[i].isoformat(),
                    "end": times[i + 1].isoformat(),
                    "soc_start": socs[i],
                    "soc_end": socs[i + 1],
                }
            )

    vehicle_first_times = {v.id: float("inf") for v in scenario.vehicle_set.all()}
    for event in events.order_by("-vehicle__id", "-time_start"):
        ts = event.time_start.timestamp()
        vehicle_first_times[event.vehicle.id] = ts

    vehicles = [
        str(v) for v, _ in sorted(vehicle_first_times.items(), key=lambda x: x[1], reverse=True)
    ]

    return vehicles, records


def get_soc_gantt_as_json(task_id: str):
    vehicles, records = _get_soc_gantt(task_id)
    return vehicles, records


def get_soc_gantt_as_df(task_id: str) -> pd.DataFrame:
    _, records = _get_soc_gantt(task_id)
    return pd.DataFrame(records)


def _get_critical_rotations(task_id):
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    scenario = Scenario.objects.get(task_id=task_id)

    df = get_critical_rotations_as_dataframe(scenario.id, buses)

    print(df)

    return df["SOC_category"].value_counts().reindex(["Nicht kritisch", "kritisch"], fill_value=0)


def get_critical_rotations_as_json(task_id):
    category_counts = _get_critical_rotations(task_id)

    return [{"value": count, "name": category} for category, count in category_counts.items()]


def get_critical_rotations_as_df(task_id):
    category_counts = _get_critical_rotations(task_id)

    return category_counts.reset_index().rename(
        columns={"index": "SOC_category", "SOC_category": "Count"}
    )


def _get_bustype_df(task_id):
    vehicle_name_dict, _ = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    scenario = Scenario.objects.get(task_id=task_id)

    df = get_vehicle_types(scenario.id, buses)

    return df


def get_bustype_as_json(task_id):
    df = _get_bustype_df(task_id)

    if df.empty:
        return []

    return [{"value": row["count"], "name": row["name"]} for _, row in df.iterrows()]


def get_bustype_as_df(task_id):
    return _get_bustype_df(task_id)
