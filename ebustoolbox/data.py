"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""

import logging
import datetime

import numpy as np
import sqlalchemy
from django.db.models import Prefetch, Sum, F, FloatField, ExpressionWrapper, Max, Min
from django.db.models.functions import Coalesce, Extract
from django.utils.translation import gettext as _
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
        return identifier + _(" in Rotation ") + str(rotation.id)
    else:
        return identifier


def get_total_consumption(s: Scenario):
    vehicles = Vehicle.objects.filter(scenario_id=s.id)

    df = recent_memoizer(get_gantt, s.id)(s.id)

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


def get_vehicle_display_names(scenario_id: int) -> dict[int, dict]:
    """Short, descriptive labels for the vehicles of a scenario.

    Database ids carry no meaning outside the database, so the result charts label a vehicle with
    its vehicle type, the depot it is stationed at, and a counter. The counter restarts at 1 for
    every (vehicle type, depot) pair within the scenario and is handed out in order of first
    activity, which is the order the Gantt charts already stack their rows in.

    :param scenario_id: The id of the scenario the vehicles belong to.
    :return: ``{vehicle_id: {"name", "vehicle_type", "depot", "number"}}``
    """
    vehicles = list(Vehicle.objects.filter(scenario_id=scenario_id).select_related("vehicle_type"))

    # A vehicle is stationed wherever it parks, which is the depot owning its area events. In
    # practice that is a single depot per vehicle; if it ever is not, take the first by name so
    # the label stays stable between requests.
    depot_by_vehicle: dict[int, str] = {}
    depot_rows = sorted(
        Event.objects.filter(scenario_id=scenario_id)
        .exclude(area=None)
        .exclude(vehicle=None)
        .values_list("vehicle_id", "area__depot__name_short", "area__depot__name")
        .distinct(),
        key=lambda row: (row[0], str(row[1] or row[2] or "")),
    )
    for vehicle_id, depot_short, depot_name in depot_rows:
        depot_by_vehicle.setdefault(vehicle_id, depot_short or depot_name or "")

    first_activity = {
        row["vehicle_id"]: row["first_start"]
        for row in Event.objects.filter(scenario_id=scenario_id)
        .exclude(vehicle=None)
        .values("vehicle_id")
        .annotate(first_start=Min("time_start"))
    }

    def sort_key(vehicle: Vehicle):
        vt = vehicle.vehicle_type
        started = first_activity.get(vehicle.id)
        return (
            str(vt.name_short or vt.name),
            depot_by_vehicle.get(vehicle.id, ""),
            # Vehicles without a single event sort last, but still deterministically
            (started is None, started or datetime.datetime.min),
            vehicle.id,
        )

    display_names: dict[int, dict] = {}
    counters: dict[tuple[str, str], int] = {}
    for vehicle in sorted(vehicles, key=sort_key):
        vt = vehicle.vehicle_type
        type_name = str(vt.name_short or vt.name)
        depot_name = depot_by_vehicle.get(vehicle.id, "")

        key = (type_name, depot_name)
        counters[key] = counters.get(key, 0) + 1
        number = counters[key]

        parts = [part for part in (type_name, depot_name) if part]
        parts.append(str(number))
        display_names[vehicle.id] = {
            "name": " ".join(parts),
            "vehicle_type": type_name,
            "depot": depot_name,
            "number": number,
        }

    return display_names


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
    return _(f"{station.name},  {frequency} mal")


def get_scenario_duration(task_id: str) -> dict:
    s = Scenario.objects.get(task_id=task_id)
    df = recent_memoizer(get_gantt, s.id)(s.id)

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
        return [f"{longest_rotation.name}: {longest_rotation.distance / 1000:.1f}"]
    else:
        return [_("Keine Rotation gefunden!")]


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
        return f"{shortest_rotation.name}: \n{shortest_rotation.distance / 1000:.1f}"
    else:
        return [_("Keine Rotation gefunden!")]


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
                logger.debug(f"S.ID:{scenario_id}:Deleting deprecated scenario ", scenario_id)
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


def get_soc_as_dataframe(scenario_id, buses) -> pd.DataFrame:
    """
    Retrieves state of charge (SOC) data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve SOC data for.
    :type buses: list[str]

    :return: DataFrame containing SOC data for specified buses.
    :rtype: pandas.DataFrame
    """

    result_df = recent_memoizer(get_gantt, scenario_id)(scenario_id)
    filtered_df = result_df.query(f"vehicle_id in {buses}")
    return filtered_df


def get_duration_as_dataframe(scenario_id, buses) -> pd.DataFrame:
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


def get_distances_as_dataframe(scenario_id, buses) -> pd.DataFrame:
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


def get_activities_as_dataframe(scenario_id, buses) -> pd.DataFrame:
    """
    Retrieves activity data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve activity data for.
    :type buses: list[str]

    :return: DataFrame containing activity data for specified buses.
    :rtype: pandas.DataFrame
    """
    result_df = recent_memoizer(get_gantt, scenario_id)(scenario_id)

    filtered_df = result_df.query(f"vehicle_id in {buses}")
    return filtered_df


def get_powerdraw_as_dataframe(scenario_id, buses=None) -> pd.DataFrame:
    """
    Retrieves power draw data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve power draw data for.
    :type buses: list[str]

    :return: DataFrame containing power draw data for specified buses.
    :rtype: pandas.DataFrame
    """
    df = recent_memoizer(get_all_powerdraw, scenario_id)(scenario_id)
    if buses is not None:
        df = df.query(f"V_id in {buses}")
    return df


def get_vehicle_types(scenario_id, buses) -> pd.DataFrame:
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
        vt_filter = Vehicle.objects.filter(vehicle_type=vt)
        if buses is not None:
            vt_filter = vt_filter.filter(pk__in=buses)
        count = vt_filter.count()
        if count == 0:
            continue
        vehicle_type_counts.append({"name": vt.name, "count": count})

    df = pd.DataFrame(vehicle_type_counts)
    return df


def get_critical_rotations_as_dataframe(scenario_id, buses) -> pd.DataFrame:
    """
    Retrieves per-rotation criticality information for specified buses in a scenario.

    :param scenario_id: The ID of the scenario.
    :param buses: List of bus IDs to include.
    :return: DataFrame with R_id, V_id, soc_end, and SOC_category columns.
    """
    df = recent_memoizer(get_gantt, scenario_id)(scenario_id)

    if buses is not None:
        df = df[df["vehicle_id"].isin(buses)]

    df = df.explode("R_id")

    df = df.groupby(["R_id", "vehicle_id"])["soc_end"].min().reset_index()

    df["SOC_category"] = df["soc_end"].apply(
        lambda x: "Nicht kritisch" if x > CRITICAL_SOC else "kritisch"
    )
    return df


def apply_id(rotation):
    try:
        return rotation.id
    except AttributeError:
        return None


def get_critical_rotations_and_score_as_dataframe(scenario_id, buses) -> pd.DataFrame:
    """
    TODO
    """
    df = recent_memoizer(get_gantt, scenario_id)(scenario_id)

    if buses is not None:
        df = df[df["V_id"].isin(buses)]

    df = df.explode("R_id")
    df["R_id"] = df["R_id"].apply(apply_id)

    # If events with no rotation should be returned dropna needs to be False
    df = df.groupby(["R_id", "V_id"], dropna=True)["soc_end"].min().reset_index()
    df = pd.DataFrame(df)
    v_dict, unused_variable = recent_memoizer(get_vehicle_dictionaries, scenario_id)(scenario_id)
    r_dict, unused_variable = recent_memoizer(get_rotation_dictionaries, scenario_id)(scenario_id)

    df["V_id"] = df["V_id"].apply(lambda x: v_dict[x])
    df["R_id"] = df["R_id"].apply(lambda x: r_dict[x])

    return df


def get_all_routes(scenario_id) -> pd.DataFrame:
    qs = Route.objects.filter(scenario_id=scenario_id)

    # Convert the QuerySet to a DataFrame
    data = list(qs.values())
    df = pd.DataFrame(data)

    return df


def get_all_trip_info(scenario_id) -> pd.DataFrame:
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


def get_all_powerdraw(scenario_id) -> pd.DataFrame:
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
                        # Station_id is the primary key, so callers can match a Station without
                        # guessing which of its two names ended up here. The readable name rides
                        # along in its own column for exports.
                        "Station_id": station.id,
                        "Station": stations_name_short_dict.get(station.id),
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
        # Same columns as the populated frame. This used to announce an "Energy" column that
        # nothing produces, so consumers reading "Power" raised a KeyError on a scenario without
        # charging events.
        result_df = pd.DataFrame(
            {
                "V_id": [],
                "time_start": [],
                "time_end": [],
                "Power": [],
                "Station_id": [],
                "Station": [],
            }
        )

    return result_df


def station_power_samples(events) -> pd.DataFrame:
    """Charging power of the given events, at the resolution the simulation recorded.

    :func:`get_all_powerdraw` collapses a charging event into one average power, which averages
    away the taper of the charging curve: a bus that starts at 250 kW and tails off shows up as
    the ~200 kW it averaged, so a peak taken from it is not a peak at all. Each charging event
    carries a minute-wise soc timeseries spanning its whole duration, so the power actually drawn
    between two samples can be recovered instead.

    Takes the events rather than a scenario, because a depot's events are sampled per minute
    across the whole night and expanding every one of them costs seconds for a caller that only
    wants a single terminus.

    :param events: Charging events, with ``vehicle_type`` selected.
    :return: One row per sample interval, in the same shape as :func:`get_all_powerdraw` so that
        :func:`station_power_profile` can consume either.
    """
    rows = []
    for event in events:
        if event.event_type == EventType.CHARGING_DEPOT:
            station_id = event.area.depot.station_id if event.area else None
        else:
            station_id = event.station_id
        if station_id is None:
            continue

        capacity = event.vehicle_type.battery_capacity
        efficiency = event.vehicle_type.charging_efficiency
        timeseries = event.timeseries or {}
        times = timeseries.get("time") or []
        socs = timeseries.get("soc") or []

        if len(times) < 2 or len(times) != len(socs):
            # Nothing sampled, so the event's average is the best available. Formatted like the
            # samples so the column stays homogeneous strings and can be parsed in one go.
            samples = [
                (
                    event.time_start.isoformat(),
                    event.time_end.isoformat(),
                    event.soc_end - event.soc_start,
                )
            ]
        else:
            samples = [
                (times[i], times[i + 1], socs[i + 1] - socs[i]) for i in range(len(times) - 1)
            ]

        # Kept as raw values here; parsing timestamps one at a time dominates the runtime of this
        # function, and a depot's events expand to hundreds of thousands of samples
        for start, end, delta_soc in samples:
            if delta_soc > 0:
                rows.append((start, end, delta_soc * efficiency * capacity, station_id))

    frame = pd.DataFrame(rows, columns=["time_start", "time_end", "energy", "Station_id"])
    if frame.empty:
        return pd.DataFrame(columns=["time_start", "time_end", "Power", "Station_id"])

    # ISO8601 rather than a fixed format: the simulation writes fractional seconds only sometimes
    frame["time_start"] = pd.to_datetime(frame["time_start"], utc=True, format="ISO8601")
    frame["time_end"] = pd.to_datetime(frame["time_end"], utc=True, format="ISO8601")
    hours = (frame["time_end"] - frame["time_start"]).dt.total_seconds() / 3600
    frame = frame[hours > 0]
    frame["Power"] = frame["energy"] / hours[hours > 0]

    return frame[["time_start", "time_end", "Power", "Station_id"]]


def sim_is_finished(task_id: str) -> bool:
    return Scenario.objects.filter(task_id=task_id, finished__isnull=False).exists()


def get_soc_as_json(task_id: str) -> dict:
    s = Scenario.objects.get(task_id=task_id)

    vehicle_name_dict, unused_variable = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())
    df = get_soc_as_dataframe(s.id, buses)

    selected_columns = df[["vehicle_id", "end", "soc_end", "start", "soc_start"]].copy()

    # Convert both 'time_end' and 'time_start' to Unix timestamps (in milliseconds)
    selected_columns["timestamp_end"] = pd.to_datetime(selected_columns["end"]).astype(int) // 10**6
    selected_columns["timestamp_start"] = (
        pd.to_datetime(selected_columns["start"]).astype(int) // 10**6
    )
    selected_columns["help1"] = 1
    selected_columns["help2"] = 2

    # Combine both start and end points
    # Each group will contain a list of [timestamp, soc] pairs for both start and end
    soc_data = (
        selected_columns.groupby("vehicle_id")
        .apply(
            lambda group: sorted(
                group[["timestamp_start", "soc_start", "help1"]].values.tolist()
                + group[["timestamp_end", "soc_end", "help2"]].values.tolist(),
                key=lambda x: (x[0], -x[2]),  # Sort by timestamp; if equal,
                # place end points (help2=2) before start points (help1=1)
            ),
            include_groups=False,
        )
        .to_dict()
    )
    # The chart keys its series by vehicle id, so ship the labels alongside rather than renaming
    # the keys and losing the link back to the database row.
    names = {
        vehicle_id: display["name"]
        for vehicle_id, display in get_vehicle_display_names(s.id).items()
    }
    return {"data": soc_data, "names": names}


def get_binned_soc(task_id: str) -> pd.DataFrame:
    # load raw SOC events
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, unused_variable = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())
    df = get_soc_as_dataframe(scenario.id, buses)

    # parse timestamps
    df["timestamp"] = pd.to_datetime(df["start"])
    df = df[["vehicle_id", "timestamp", "soc_end"]]

    # build the global hourly index
    all_hours = pd.date_range(
        start=df["timestamp"].min().floor("h"), end=df["timestamp"].max().ceil("h"), freq="1h"
    )

    filled_dfs = []
    # for each vehicle, bucket into 1-hour bins taking the MIN soc_end per hour
    for vid, group in df.groupby("vehicle_id"):
        # ensure time ordering
        group = group.set_index("timestamp").sort_index()

        # 1) resample to hourly, pick the *lowest* SOC seen in that hour
        hourly_min = group["soc_end"].resample("1h").min()

        # 2) align to the full global index and forward-fill
        hourly_min = hourly_min.reindex(all_hours)  # introduce any missing hours
        hourly_filled = hourly_min.ffill()  # carry last known SOC forward

        # 3) package back into a DataFrame
        tmp = hourly_filled.reset_index()
        tmp.columns = ["timestamp", "soc_end"]
        tmp["V_id"] = vid

        filled_dfs.append(tmp)

    # concatenate all vehicles
    df_filled = pd.concat(filled_dfs, ignore_index=True)

    # drop initial hours where we never had a reading
    df_filled = df_filled.dropna(subset=["soc_end"])

    # Restrict to the simulation window, the same one the depot occupancy chart uses. The events
    # span more than the schedule itself, because eflips simulates a period before and after it to
    # reach a steady state, and those padding days are not a result worth plotting. The resample
    # and ffill above deliberately run on the full range, so a vehicle whose last reading predates
    # the window still carries its SOC into it.
    time_start, time_end = get_start_end_time(scenario)
    df_filled = df_filled[
        df_filled["timestamp"].between(pd.Timestamp(time_start).floor("h"), pd.Timestamp(time_end))
    ].copy()

    def soc_bin(soc):
        if soc < 0:
            return "<0"
        # each bin is 0–9%, 10–19%, …, 90–100%
        return f"{int((soc * 100) // 10) * 10}"

    df_filled["soc_bin"] = df_filled["soc_end"].apply(soc_bin)

    # The reindex above puts every vehicle on exactly one row per hour, so each count is a number
    # of vehicles. Hours stay absolute instead of being folded onto a 24h axis, which keeps this
    # comparable to the depot occupancy chart and stops the ~03:00 start of the operating day from
    # wrapping the night onto both edges of the plot.
    heatmap_data = df_filled.groupby(["timestamp", "soc_bin"]).size().reset_index(name="count")
    heatmap_data["time"] = heatmap_data["timestamp"].apply(lambda t: t.isoformat())

    return heatmap_data[["time", "soc_bin", "count"]]


def get_power_draw(request, task_id: str) -> list:
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


def get_stats(task_id: str) -> dict:
    scenario = Scenario.objects.get(task_id=task_id)

    filter_dict = dict(task_id=task_id)

    vehicle_name_dict, unused_variable = get_all_buses_labeled(task_id)
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

    time_start, time_end = get_start_end_time(scenario)
    # Filter events to the simulation range
    events = events.filter(time_start__gte=time_start, time_end__lte=time_end)

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
    electrified_stations_list = list(opp_stations.values_list("name", flat=True))

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
    charging_events = charging_events.filter(time_start__gte=time_start, time_end__lte=time_end)

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
        for unused_variable, delta in timeline:
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
        "total_consumption": np.round(energy_deps + energy_opps, 0).item(),
        "avg_consumption": np.abs(np.round(average_consumption, 3)).item(),
        "installed_power": np.round(total_installed_power, 0).item(),
        "depot_energy": np.round(energy_deps, 0).item(),
        "peak_depot_power": np.round(peak_power_kw, 0).item(),
        "electrified_stations_list": electrified_stations_list,
    }

    return resp


def station_power_profile(power_df: pd.DataFrame) -> pd.Series:
    """Turn per-vehicle charging intervals into the power drawn over time.

    Every row of ``power_df`` is one vehicle charging at a constant power between time_start and
    time_end. The draw at any moment is the sum of the rows covering it, so add each row's power
    at its start, take it away again at its end and accumulate.

    How fine the result is depends on the rows given: one row per charging event
    (:func:`get_all_powerdraw`) yields event averages, one row per recorded sample
    (:func:`station_power_samples`) yields the instantaneous power.

    :param power_df: Rows from :func:`get_powerdraw_as_dataframe`, for a single station.
    :return: Power in kW indexed by time, empty if there is nothing to charge.
    """
    charging = power_df[power_df["Power"] > 0]
    if charging.empty:
        return pd.Series(dtype=float)

    deltas = pd.concat(
        [
            charging[["time_start", "Power"]].rename(columns={"time_start": "time"}),
            charging[["time_end", "Power"]]
            .rename(columns={"time_end": "time"})
            .assign(Power=lambda frame: -frame["Power"]),
        ]
    )
    return deltas.groupby("time")["Power"].sum().sort_index().cumsum()


def occupancy_metrics(intervals: list, window_seconds: float, configured_places) -> dict:
    """Occupancy of one station's charging points over the simulated period.

    :param intervals: ``(time_start, time_end)`` of every charging event at the station.
    :param window_seconds: Length of the simulated period.
    :param configured_places: ``amount_charging_places``, or None where no limit was set.
    :return: ``{"chargers", "peak", "avg_concurrent", "utilization"}``
    """
    timeline = sorted(
        [(start, 1) for start, unused_end in intervals]
        + [(end, -1) for unused_start, end in intervals]
    )
    concurrent = 0
    peak = 0
    for unused_time, delta in timeline:
        concurrent += delta
        peak = max(peak, concurrent)

    # Without a configured limit the station has to be built for its busiest moment
    chargers = configured_places or peak
    occupied_seconds = sum((end - start).total_seconds() for start, end in intervals)
    avg_concurrent = occupied_seconds / window_seconds if window_seconds else 0.0

    return {
        "chargers": chargers,
        "peak": peak,
        "avg_concurrent": avg_concurrent,
        "utilization": avg_concurrent / chargers if chargers else 0.0,
    }


def station_charging_events(station, time_start, time_end):
    """The charging events at one station, within the simulated period.

    A depot's buses charge on an area belonging to the depot rather than at the station itself, so
    a depot station has to be looked up through its areas and by the other event type.

    :param station: The station whose charging events are wanted.
    :param time_start: Start of the simulated period.
    :param time_end: End of the simulated period.
    """
    window = {"time_start__gte": time_start, "time_end__lte": time_end}
    if station.charge_type == EnumChargeType.DEPOT:
        return station.scenario.event_set.filter(
            event_type=EventType.CHARGING_DEPOT, area__depot__station=station, **window
        ).select_related("vehicle_type", "area__depot")
    return station.scenario.event_set.filter(
        event_type=EventType.CHARGING_OPPORTUNITY, station=station, **window
    ).select_related("vehicle_type")


def get_station_summary(station) -> dict:
    """The figures the map popup shows for one electrified station.

    Same definitions as :func:`get_electrified_stations`, for a single station, so the popup and
    the table below the map cannot drift apart.
    """
    scenario = station.scenario
    time_start, time_end = get_start_end_time(scenario)
    window_seconds = (time_end - time_start).total_seconds()

    events = list(station_charging_events(station, time_start, time_end))

    intervals = [(event.time_start, event.time_end) for event in events]
    energy = sum(
        (event.soc_end - event.soc_start) * event.vehicle_type.battery_capacity
        for event in events
    )

    metrics = occupancy_metrics(intervals, window_seconds, station.amount_charging_places)
    profile = station_power_profile(station_power_samples(events))

    # Asked of Route, not Trip: every trip on a route shares that route's terminus and line, so
    # going through Trip scans every trip in the scenario to produce the same handful of names
    lines = sorted(
        {
            str(name)
            for name in Route.objects.filter(scenario=scenario, arrival_station=station)
            .exclude(line=None)
            .values_list("line__name", flat=True)
            .distinct()
        }
    )

    return {
        "chargers": metrics["chargers"],
        "power_installed": round((station.power_per_charger or 0.0) * metrics["chargers"], 1),
        "power_peak": round(float(profile.max()) if not profile.empty else 0.0, 1),
        "energy": round(energy, 1),
        "utilization": round(metrics["utilization"], 4),
        "utilization_pct": round(metrics["utilization"] * 100, 1),
        "avg_concurrent": round(metrics["avg_concurrent"], 2),
        "arrivals": len(intervals),
        "lines": lines,
    }


def get_station_load_profile(station, bin_minutes: int = 60) -> list[dict]:
    """The station's load over the simulated period, in fixed bins.

    Per bin the occupancy is averaged, because a mean is what "how busy was this hour" means,
    while the power is the highest value reached, because that is what the connection has to
    carry. Bins with no charging are kept so the time axis stays continuous.

    :param station: The station to profile.
    :param bin_minutes: Width of one bin in minutes.
    :return: ``[{"time", "utilization", "power"}]`` ordered by time.
    """
    scenario = station.scenario
    time_start, time_end = get_start_end_time(scenario)
    bin_size = pd.Timedelta(minutes=bin_minutes)

    charging_events = list(station_charging_events(station, time_start, time_end))
    events = [(event.time_start, event.time_end) for event in charging_events]

    metrics = occupancy_metrics(
        events, (time_end - time_start).total_seconds(), station.amount_charging_places
    )
    chargers = metrics["chargers"]

    profile = station_power_profile(station_power_samples(charging_events))

    bins = pd.date_range(
        pd.Timestamp(time_start).floor(f"{bin_minutes}min"), pd.Timestamp(time_end), freq=bin_size
    )

    rows = []
    for bin_start in bins:
        bin_end = bin_start + bin_size

        # Occupied charger-seconds that fall inside this bin
        occupied = 0.0
        for start, end in events:
            overlap_start = max(pd.Timestamp(start), bin_start)
            overlap_end = min(pd.Timestamp(end), bin_end)
            if overlap_end > overlap_start:
                occupied += (overlap_end - overlap_start).total_seconds()

        utilization = occupied / (chargers * bin_size.total_seconds()) if chargers else 0.0

        # The profile is a step function, so the value in force at the bin start still counts
        in_bin = profile[(profile.index >= bin_start) & (profile.index < bin_end)]
        before = profile[profile.index <= bin_start]
        candidates = list(in_bin.values) + ([before.iloc[-1]] if not before.empty else [])
        power = max(candidates) if candidates else 0.0

        rows.append(
            {
                "time": bin_start.isoformat(),
                "utilization": round(utilization, 4),
                "power": round(float(power), 1),
            }
        )

    return rows


def get_electrified_stations(task_id: str) -> pd.DataFrame:
    """One row per electrified terminus stop.

    Terminus electrification is modelled as opportunity charging, so every opportunity charging
    station is an electrified terminus. The number of charging points is whatever the operator
    configured; where that was left open, it is the peak number of buses charging there at the
    same time, which is the number the station has to be built for.

    Two power figures are reported, because they answer different questions and differ a lot.
    ``power_installed`` is the nameplate capacity that has to be built, charging points times the
    power of one. ``power_peak`` is the highest draw the simulation actually produced, taken from
    the recorded soc samples rather than from per-event averages, so it is the diversified load a
    grid connection would be negotiated on.

    ``utilization`` is the mean number of occupied charging points over the available ones. Since
    the count of charging points is itself the peak occupancy wherever the operator set no limit,
    it is then a mean-over-peak load factor: it cannot reach 100%, and a low value means arrivals
    are peaky rather than that a charging point could be dropped. ``avg_concurrent`` is the same
    figure before dividing, i.e. the average number of buses charging here.

    :param task_id: The task id of the (simulated) scenario.
    :return: One row per terminus with its lines, charging points, power, energy and occupancy.
    """
    scenario = Scenario.objects.get(task_id=task_id)
    time_start, time_end = get_start_end_time(scenario)
    window_seconds = (time_end - time_start).total_seconds()

    opp_stations = scenario.station_set.filter(charge_type=EnumChargeType.OPPORTUNITY)

    # Fetched once and reused: the sample expansion below needs the same rows, and reading them a
    # second time through a values_list only to re-derive energy is a wasted pass over the events
    charging_events = list(
        scenario.event_set.filter(
            event_type=EventType.CHARGING_OPPORTUNITY,
            time_start__gte=time_start,
            time_end__lte=time_end,
        )
        .exclude(station=None)
        .select_related("vehicle_type")
    )
    events_by_station: dict[int, list] = {}
    energy_by_station: dict[int, float] = {}
    for event in charging_events:
        events_by_station.setdefault(event.station_id, []).append(
            (event.time_start, event.time_end)
        )
        energy = (event.soc_end - event.soc_start) * event.vehicle_type.battery_capacity
        energy_by_station[event.station_id] = (
            energy_by_station.get(event.station_id, 0.0) + energy
        )

    # Which lines terminate here. This is what makes a stop worth electrifying, so it answers
    # "what breaks if this one is skipped".
    lines_by_station: dict[int, set] = {}
    for station_id, line_name in (
        Route.objects.filter(scenario=scenario)
        .exclude(line=None)
        .values_list("arrival_station_id", "line__name")
        .distinct()
    ):
        lines_by_station.setdefault(station_id, set()).add(str(line_name))

    # Only the terminus events, so the depot's night-long minute-wise samples are not expanded
    power_df = station_power_samples(charging_events)
    peak_power_by_station: dict[int, float] = {}
    if not power_df.empty:
        for station_id, group in power_df.groupby("Station_id"):
            profile = station_power_profile(group)
            if not profile.empty:
                peak_power_by_station[station_id] = float(profile.max())

    records = []
    for station in opp_stations:
        events = events_by_station.get(station.id, [])
        metrics = occupancy_metrics(events, window_seconds, station.amount_charging_places)
        chargers = metrics["chargers"]

        records.append(
            {
                "station_id": station.id,
                "name": station.name,
                # Joined rather than a list so the CSV export stays readable; the table splits
                # it again to draw one chip per line
                "lines": ", ".join(sorted(lines_by_station.get(station.id, set()))),
                "chargers": chargers,
                "power_installed": round((station.power_per_charger or 0.0) * chargers, 1),
                "power_peak": round(peak_power_by_station.get(station.id, 0.0), 1),
                "energy": round(energy_by_station.get(station.id, 0.0), 1),
                "utilization": round(metrics["utilization"], 4),
                "avg_concurrent": round(metrics["avg_concurrent"], 2),
                "arrivals": len(events),
                # So a row in the table can point the map at its stop
                "lon": station.geom.x if station.geom else None,
                "lat": station.geom.y if station.geom else None,
            }
        )

    df = pd.DataFrame(
        records,
        columns=[
            "station_id",
            "name",
            "lines",
            "chargers",
            "power_installed",
            "power_peak",
            "energy",
            "utilization",
            "avg_concurrent",
            "arrivals",
            "lon",
            "lat",
        ],
    )
    return df.sort_values("name").reset_index(drop=True)


def get_speed_hist(task_id: str) -> pd.DataFrame:
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, unused_variable = get_all_buses_labeled(task_id)
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

    return pd.DataFrame(
        {
            "bins": [
                f"{bin_edges[i]:.1f}-{bin_edges[i + 1]:.1f} km/h" for i in range(len(bin_edges) - 1)
            ],
            "counts": hist.tolist(),
        }
    )


def get_dist_hist(task_id: str) -> dict:
    scenario = Scenario.objects.get(task_id=task_id)
    vehicle_name_dict, unused_variable = get_all_buses_labeled(task_id)
    buses = list(vehicle_name_dict.keys())

    filter_dict = dict(task_id=task_id)
    if buses:
        filter_dict["vehicle__id__in"] = buses

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

    return {
        "bins": grouped.index.tolist(),
        "data": {
            _("Nicht kritisch"): grouped["Nicht kritisch"].tolist(),
            _("kritisch"): grouped["kritisch"].tolist(),
        },
    }


def get_power_draw_and_occ(task_id: str, depot_id: None | int = None) -> dict:
    avgScenario = Scenario.objects.get(task_id=task_id)

    # Find the associated "extreme"/sizing scenario
    extrScenario = avgScenario.get_sizing_scenario()

    if not depot_id:
        all_areas = extrScenario.area_set.all()
    else:
        all_areas = extrScenario.area_set.filter(depot_id=depot_id)

    all_area_ids = [area.id for area in all_areas]

    time_start, time_end = get_start_end_time(extrScenario)

    with Session(SqlAlchemyEngine.get_engine()) as session:
        df = power_and_occupancy(
            all_area_ids, session, sim_start_time=time_start, sim_end_time=time_end
        )

    # ---- KPIs -------------------------------------------------
    max_charging = int(df["occupancy_charging"].max())
    max_total = int(df["occupancy_total"].max())
    peak_power = float(df["power"].max())

    # assume power in kW, time in uniform steps
    df = df.sort_values("time")
    dt_hours = (df["time"].iloc[1] - df["time"].iloc[0]).total_seconds() / 3600.0

    total_energy = float((df["power"] * dt_hours).sum())
    # ----------------------------------------------------------

    return {
        "data": df,
        "kpis": {
            "max_charging": max_charging,
            "max_total": max_total,
            "peak_power_kw": round(peak_power, 1),
            "energy_kwh": round(total_energy, 1),
        },
    }


def get_gantt(scenario_id: str) -> pd.DataFrame:
    # Get all events for the scenario, ordered

    scenario = Scenario.objects.get(id=scenario_id)
    events = (
        scenario.event_set.exclude(vehicle=None)
        .order_by("vehicle__id", "time_start")
        .select_related(
            "vehicle",
            "vehicle__vehicle_type",
            "trip__rotation",
            "trip__route",
            "station",
        )
    )

    display_names = get_vehicle_display_names(scenario_id)

    records = []
    for event in events:
        vehicle_id = event.vehicle.id
        display = display_names.get(vehicle_id, {})
        # Fall back to the long type name only if the vehicle somehow has no label
        vehicle_type_name = display.get("vehicle_type") or event.vehicle.vehicle_type.name

        if event.trip:
            rotation_id = event.trip.rotation.id if event.trip.rotation else None
            rotation_name = event.trip.rotation.name if event.trip.rotation else None
            route_name = event.trip.route.name if event.trip.route else None
        else:
            rotation_id, rotation_name, route_name = None, None, None

        station_name = event.station.name if event.station else None

        records.append(
            {
                "vehicle_id": vehicle_id,
                "vehicle_type": vehicle_type_name,
                "bus_name": display.get("name", str(vehicle_id)),
                "vehicle_number": display.get("number"),
                "depot": display.get("depot", ""),
                "start": event.time_start.isoformat(),
                "end": event.time_end.isoformat(),
                "soc_start": event.soc_start,
                "soc_end": event.soc_end,
                "route_name": route_name,
                "rotation_name": rotation_name,
                "station_name": station_name,
                "R_id": rotation_id,
                "event_type": event.event_type,
            }
        )

    records = pd.DataFrame(records)
    records["R_id"] = records["R_id"].astype(object)  # Needed, since R_id has mixed Int and None,
    # which would otherwise json serialize to nan but needs to serialize to null
    records = records.where(
        pd.notna(records), None
    )  # replaces NaN with None across the ENTIRE dataframe

    return records


def get_start_end_time(scenario: Scenario) -> tuple[datetime.datetime, datetime.datetime]:
    """Get the time_start and the time_end of the scenario"""
    # Fallback if a scenario should be plotted without a parent and a simulation range
    trips = Trip.objects.filter(scenario=scenario).order_by("departure_time")
    time_start = trips.first().departure_time
    time_end = trips.last().arrival_time
    return time_start, time_end


def get_cumulative_energy(task_id: str) -> dict:
    rotations = (
        Rotation.objects.filter(scenario__task_id=task_id)
        .annotate(
            energy_kwh=ExpressionWrapper(
                (Max("trip__event__soc_start") - Min("trip__event__soc_end"))
                * Max("vehicle_type__battery_capacity"),
                output_field=FloatField(),
            ),
            v_type=F("vehicle_type__name"),  # Assuming 'name' is the field
        )
        .values("energy_kwh", "v_type")
    )

    # Return raw list so JS can filter and calculate CDF
    raw_data = [
        {"energy": round(float(r["energy_kwh"]), 2), "type": r["v_type"]}
        for r in rotations
        if r["energy_kwh"] is not None
    ]

    unique_types = list(set(r["type"] for r in raw_data))

    return {"raw_data": raw_data, "unique_types": unique_types}


def get_rotation_table_data(task_id: str) -> pd.DataFrame:
    rotations = Rotation.objects.filter(scenario__task_id=task_id).annotate(
        max_soc=Max("trip__event__soc_start"),
        min_soc=Min("trip__event__soc_end"),
        cap=Max("vehicle_type__battery_capacity"),
        total_dist_m=Sum("trip__route__distance"),
        start_t=Min("trip__departure_time"),
        end_t=Max("trip__arrival_time"),
    )

    rows = []
    for r in rotations:
        m_soc = r.max_soc if r.max_soc is not None else 0
        l_soc = r.min_soc if r.min_soc is not None else 0
        capacity = r.cap if r.cap is not None else 0

        soc_delta = m_soc - l_soc
        energy_kwh = soc_delta * capacity
        dist_km = (r.total_dist_m or 0) / 1000.0

        efficiency = energy_kwh / dist_km if dist_km > 0 else 0

        duration_h = 0
        if r.start_t and r.end_t:
            duration_h = (r.end_t - r.start_t).total_seconds() / 3600.0

        rows.append(
            {
                "id": r.id,
                "name": r.name or f"ID: {r.id}",
                "vehicle": r.vehicle_type.name_short or r.vehicle_type.name,
                "distance": round(dist_km, 2),
                "consumption": round(energy_kwh, 2),
                "efficiency": round(efficiency, 3),
                "duration": round(duration_h, 2),
                "soc_spread_pct": round(soc_delta * 100, 1),
            }
        )
    return pd.DataFrame(rows)


def get_tco(task_id: str) -> dict:
    # return tco_result JsonField of scenario
    s = Scenario.objects.get(task_id=task_id)
    result = s.tco_result
    return result


def get_combined_piecharts(task_id: str, filters=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns two DataFrames:
    1. critical_rotations_df: aggregated counts by SOC_category
    2. bus_types_df: bus type counts
    Both DataFrames can be turned into JSON or CSV.
    """

    s = Scenario.objects.get(task_id=task_id)

    # Get raw DataFrames
    critical_raw = get_critical_rotations_as_dataframe(s.id, filters)
    bustype_raw = get_vehicle_types(s.id, filters)

    # Aggregate critical rotations by SOC_category
    critical_rotations_df = (
        critical_raw.groupby("SOC_category")
        .size()
        .reset_index(name="count")
        .rename(columns={"SOC_category": "category"})
    )

    # bus_types_df already has 'name' and 'count'
    bus_types_df = bustype_raw.rename(columns={"name": "category"})

    return critical_rotations_df, bus_types_df
