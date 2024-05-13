"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""
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
)
import pandas as pd
from django.db.models import Count
from dash.exceptions import PreventUpdate


def vid_for_plotting(vehicle: Vehicle):
    # Create a user friendly vehicle identifier for plotting
    return vehicle.id


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
    vehicles = filter_dict.pop("vehicle__id__in")
    return [
        "Selected / Total number of Buses:",
        str(len(vehicles)) + " / " + str(len(get_all_buses(task_id))),
    ]


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
    if len(filter_dict["vehicle__id__in"]) == 0:
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    longest_rotation = get_longest_distance_rotation(filter_dict)

    if longest_rotation:
        return [f"Longest Rotation {longest_rotation.name}", f"{longest_rotation.distance:.1f} m"]
    else:
        return ["No Rotations found!"]


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
    if len(filter_dict["vehicle__id__in"]) == 0:
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    shortest_rotation = get_shortest_distance_rotation(filter_dict)

    # Add style if text should have special style
    if shortest_rotation:
        return [
            f"Shortest Rotation {shortest_rotation.name}",
            f"{shortest_rotation.distance:.1f} m",
        ]
    else:
        return ["No Rotations found!"]


def recent_memoizer(function, _dcache1=dict(), _result_cache2=dict()):
    """Decorator function

    :param function: function do be decorated
    :type function: function
    :param _dcache1: storage of recent calls
    :param _result_cache2: storage of results of recent calls
    :return: decorated function or timer if given function is None
    :rtype function or dict

    """
    # Maximum number of cached results per function
    MAX_SIZE = 10

    def decorated_function(*this_args, **kwargs):
        key = function.__name__
        if key not in _dcache1:
            _dcache1[key] = list()
            _result_cache2[key] = dict()

        inputs = tuple((this_args, *list(kwargs)))
        if inputs in _dcache1[key] and inputs in _result_cache2[key]:
            _dcache1[key].remove(inputs)
            _dcache1[key].append(inputs)
            return _result_cache2[key][inputs]

        else:
            # Storage is full. Delete oldest storage
            if len(_dcache1[key]) >= MAX_SIZE:
                del _result_cache2[key][_dcache1[key][0]]
                del _dcache1[key][0]
            _dcache1[key].append(inputs)

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
    result_df = get_all_event_info(scenario_id)
    return result_df.query(f"V_id in {buses}")


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
    result_df = get_all_trip_info(scenario_id)
    result_df = result_df.groupby(["R_id", "V_id"])["duration"].sum().reset_index()
    return result_df.query(f"V_id in {buses}")


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
    result_df = get_all_trip_info(scenario_id)
    result_df = result_df.groupby(["R_id", "V_id"])["total_distance"].sum().reset_index()
    return result_df.query(f"V_id in {buses}")


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
    result_df = get_all_event_info(scenario_id)
    return result_df.query(f"V_id in {buses}")


def get_powerdraw_as_dataframe(scenario_id, buses):
    """
    Retrieves power draw data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve power draw data for.
    :type buses: list[str]

    :return: DataFrame containing power draw data for specified buses.
    :rtype: pandas.DataFrame
    """
    result_df = get_all_powerdraw_as_dataframe(scenario_id)
    return result_df.query(f"V_id in {buses}")


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

    values_with_counts = (
        VehicleType.objects.filter(**filter_dict).values("name").annotate(count=Count("name"))
    )
    df = pd.DataFrame(values_with_counts)

    return df


def get_critical_rotations_as_dataframe(scenario_id, buses):
    """
    Retrieves critical rotations data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve critical rotations data for.
    :type buses: list[str]

    :return: DataFrame containing critical rotation data.
    :rtype: pandas.DataFrame
    """
    result_df = get_all_event_info(scenario_id)
    df = result_df[result_df["V_id"].isin(buses)]

    df = df.explode("R_id")
    df["R_id"] = df["R_id"].apply(lambda rotation_obj: rotation_obj.id)

    df = df.groupby(["R_id", "V_id"])["soc_end"].min().reset_index()

    df["SOC_category"] = df["soc_end"].apply(lambda x: "Non-Critical" if x > 0.0 else "Critical")

    return pd.DataFrame(
        df["SOC_category"].value_counts().reset_index().values, columns=["Category", "Count"]
    )


def get_critical_rotations_and_score_as_dataframe(scenario_id, buses):
    """
    TODO
    """
    result_df = get_all_event_info(scenario_id)
    df = result_df[result_df["V_id"].isin(buses)]

    df = df.explode("R_id")
    df["R_id"] = df["R_id"].apply(lambda rotation_obj: rotation_obj.id)

    df = df.groupby(["R_id", "V_id"])["soc_end"].min().reset_index()

    return pd.DataFrame(df)


@recent_memoizer
def get_all_event_info(scenario_id):
    """
    Retrieves event information for all vehicles in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str

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

    # Initialize lists to store data
    dfs = []

    # Iterate over vehicles
    for vehicle in vehicles:
        v_id = vid_for_plotting(vehicle)
        if vehicle.id in events_by_vehicle:
            # Filter rotations for the current vehicle
            vehicle_rotations = all_rotations.filter(vehicle_id=vehicle.id)
            events = events_by_vehicle[vehicle.id]
            for event in events:
                time_start = event.time_start
                event_type = event.event_type
                duration = (event.time_end - event.time_start).total_seconds()
                time_end = event.time_end
                # Fetch events for the current rotation
                dfs.append(
                    {
                        "V_id": v_id,
                        "time_start": time_start,
                        "time_end": time_end,
                        "duration": duration,
                        "event_type": event_type,
                        "soc_start": event.soc_start,
                        "soc_end": event.soc_end,
                        "R_id": vehicle_rotations,
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
            }
        )

    return result_df


@recent_memoizer
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
        try:
            v_id = vid_for_plotting(rotation.vehicle)
        except AttributeError:
            v_id = None
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


# @recent_memoizer
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
    stations_name_short_dict = {station.id: station.name_short for station in all_stations}

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
                        "V_id": vid_for_plotting(vehicle),
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
