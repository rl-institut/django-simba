"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""
import warnings
import logging

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
)
import pandas as pd
from dash.exceptions import PreventUpdate

# Maximum number of cached results per function
MAX_SIZE = 10
# stores scenario_id and finished time
last_simulations = list()
CRITICAL_SOC = 0.0
logger = logging.getLogger("custom")


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
    vehicles = filter_dict.pop("vehicle__id__in")

    return [
        "Ausgewählte / Gesamt Anzahl an Bussen:",
        str(len(vehicles)) + " / " + str(len(get_all_buses(task_id))),
    ]


def get_number_of_stations(task_id: str, get_electrified=True) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    # Count all Station objects for the scenario
    total_stations = Station.objects.filter(scenario_id=s.id).count()

    if get_electrified:
        # Count Station objects where is_electrified is True for the scenario
        electrified_stations = Station.objects.filter(scenario_id=s.id, is_electrified=True).count()

        return [
            "Anzahl elektrifizierter Stationen / Anzahl Stationen",
            f"{electrified_stations} / {total_stations}",
        ]

    else:
        return [
            "Anzahl Stationen im Szenario:",
            f"{total_stations}",
        ]


def get_frequently_served_station(task_id: str) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    df = recent_memoizer(get_all_routes, s.id)(s.id)

    # Finding the most common item in a specific column
    most_common_station = df["arrival_station_id"].mode()[0]
    frequency = df["arrival_station_id"].value_counts()[most_common_station]

    station = Station.objects.get(scenario_id=s.id, id=most_common_station)

    return [
        "Am häufigsten angefahrene Station:",
        f"{station.name},  {frequency} mal",
    ]


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
        return [f"Längste Rotation {longest_rotation.name}", f"{longest_rotation.distance:.1f} m"]
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
        return [
            f"Kürzeste Rotation {shortest_rotation.name}",
            f"{shortest_rotation.distance:.1f} m",
        ]
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
    Retrieves critical rotations data as a DataFrame for specified buses in a given scenario.

    :param scenario_id: The ID of the scenario.
    :type scenario_id: str
    :param buses: List of bus IDs to retrieve critical rotations data for.
    :type buses: list[str]

    :return: DataFrame containing critical rotation data.
    :rtype: pandas.DataFrame
    """
    result_df = recent_memoizer(get_all_event_info, scenario_id)(scenario_id)

    df = result_df[result_df["V_id"].isin(buses)]

    df = df.explode("R_id")
    df["R_id"] = df["R_id"].apply(apply_id)

    df = df.groupby(["R_id", "V_id"])["soc_end"].min().reset_index()

    df["SOC_category"] = df["soc_end"].apply(
        lambda x: "Nicht kritisch" if x > CRITICAL_SOC else "kritisch"
    )

    category_counts = df["SOC_category"].value_counts()

    # Ensure all categories are included, even if the count is zero
    all_categories = ["Nicht kritisch", "kritisch"]
    category_counts = category_counts.reindex(all_categories, fill_value=0)

    # Convert to DataFrame suitable for plotly
    category_counts_df = category_counts.reset_index()
    category_counts_df.columns = ["Category", "Count"]

    return category_counts_df


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
