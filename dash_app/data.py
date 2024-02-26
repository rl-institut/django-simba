"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""
import numpy as np

from ebustoolbox.models import (
    Scenario,
    Vehicle,
    Event,
    Route,
    Trip,
    get_longest_distance_rotation,
    get_shortest_distance_rotation,
    EventType,
    Vehicle,
    VehicleType,
)
import pandas as pd
from django.db.models import Min, Sum, Count
from dash.exceptions import PreventUpdate


import time
df_perf = pd.DataFrame({'name': [], 'start': [], 'end': [], 'process': []})

def get_all_buses(task_id: str) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    all_buses = list(Vehicle.objects.filter(scenario=s).values_list("name_short", flat=True))
    return all_buses

def get_number_of_buses(filter_dict: dict) -> list[str]:
    task_id = filter_dict.pop("task_id")
    vehicles = filter_dict.pop("vehicle__name_short__in")
    return ["Selected / Total number of Buses:", str(len(vehicles)) + " / " + str(len(get_all_buses(task_id)))]

def get_number_longest_rot(filter_dict: dict):

    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s
    if len(filter_dict["vehicle__name_short__in"]) == 0:
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    longest_rotation = get_longest_distance_rotation(filter_dict)

    return [f"Longest Rotation {longest_rotation.name}", f"{longest_rotation.distance} m"]

def get_number_shortest_rot(filter_dict: dict):

    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s
    if len(filter_dict["vehicle__name_short__in"]) == 0:
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    shortest_rotation = get_shortest_distance_rotation(filter_dict)

    # Add style if text should have special style
    return [f"Shortest Rotation {shortest_rotation.name}", f"{shortest_rotation.distance} m"]

def get_scatter_plot_data(filter_dict: dict) -> dict:

    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s
    if len(filter_dict["vehicle__name_short__in"]) == 0:
        raise PreventUpdate

    queryset = Event.objects.filter(**filter_dict).distinct("vehicle")
    vehicles = [e.vehicle for e in queryset]
    data = dict()
    for vehicle in vehicles:

        vehicle_events = Event.objects.filter(**filter_dict, vehicle=vehicle.id).order_by(
            "time_start"
        )
        first_event = vehicle_events.first()
        socs = [first_event.soc_start]
        times = [first_event.time_start]
        for event in vehicle_events[1:]:
            socs.append(event.soc_start)
            times.append(event.time_start)
        data[vehicle.name_short] = [times, socs]

    return data

def get_bar_plot_data(filter_dict: dict) -> list[str, float, float]:

    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s
    queryset = Event.objects.filter(**filter_dict).distinct("vehicle")
    if len(filter_dict["vehicle__name_short__in"]) == 0:
        raise PreventUpdate

    vehicles = [e.vehicle for e in queryset]
    vehicle_data = np.empty((len(vehicles), 2)).astype(object)
    for i, vehicle in enumerate(vehicles):

        vehicle_events = Event.objects.filter(
            **filter_dict, vehicle=vehicle.id, event_type=EventType.DRIVING
        )
        min_soc = next(iter(vehicle_events.aggregate(Min("soc_end")).values()))
        vehicle_data[i, :] = vehicle.name_short, min_soc

    soc_data = vehicle_data[:, 1]
    lower_end = min(soc_data) // 0.05 * 0.05
    upper_end = 1
    bins = int((upper_end - lower_end) / 0.05)
    hist_data = np.histogram(soc_data, bins=bins, range=(lower_end, upper_end), density=False)
    data = np.empty((bins, 3)).astype(object)
    for i, amount in enumerate(hist_data[0]):
        bin = round(hist_data[1][i], 2)
        next_bin = round(hist_data[1][i + 1], 2)
        vehicles = vehicle_data[:, 0][(next_bin > vehicle_data[:, 1]) & (vehicle_data[:, 1] >= bin)]
        data[i, :] = ",".join(vehicles), bin, amount
    data[:, 1] = 0.5 * (hist_data[1][:-1] + hist_data[1][1:])

    return data

def get_soc(scenario_id):
    """
    Get SoC timeseries for each vehicle in a scenario

    :param scenario_id: ID of scenario to aggregate
    :type scenario_id: int
    :return: vehicle ID -> event dict (station ID, soc start/end, time start/end, soc timeseries)
    :rtype: JSON
    """

    scenario = Scenario.objects.get(id=scenario_id)
    # get all vehicle events from this scenario at a station
    events = scenario.event_set.filter(vehicle__isnull=False, station__isnull=False)
    socs = dict()
    for event in events:
        if event.vehicle_id not in socs:
            socs[event.vehicle_id] = []
        socs[event.vehicle_id].append({
            "station":    event.station_id,
            "time_start": event.time_start.isoformat(),
            "time_end": event.time_end.isoformat(),
            "soc_start":  event.soc_start,
            "soc_end":    event.soc_end,
            "timeseries": event.timeseries["soc"],
        })

    return socs

def get_soc_as_dataframe(scenario_id, buses):

    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    # get all vehicle events from this scenario at a station


    dfs = []

    for vehicle in vehicles:
        if vehicle.name_short in buses:
            v_id = vehicle.id
            events = scenario.event_set.filter(vehicle__isnull=False, vehicle_id=v_id)
            for event in events:
                time_start = event.time_start
                soc_start = event.soc_start
                # Add a row to the DataFrame
                df = pd.DataFrame({'V_id': [v_id], 'Time': [time_start], 'SOC': [soc_start]})
                dfs.append(df)
                time_end = event.time_end
                soc_end = event.soc_end
                # Add a row to the DataFrame
                df = pd.DataFrame({'V_id': [v_id], 'Time': [time_end], 'SOC': [soc_end]})
                dfs.append(df)
    result_df = pd.concat(dfs, ignore_index=True)

    # Convert the 'Time' column to datetime format
    result_df['Time'] = pd.to_datetime(result_df['Time'])

    # Sort the DataFrame based on the 'Time' column
    result_df = result_df.sort_values(by='Time')

    return result_df


def get_activities_as_dataframe(scenario_id, buses):

    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    # get all vehicle events from this scenario at a station

    dfs = []

    for vehicle in vehicles:

        if vehicle.name_short in buses:
            v_id = vehicle.id
            events = scenario.event_set.filter(vehicle__isnull=False, vehicle_id=v_id)
            for event in events:
                time_start = event.time_start
                event_type = event.event_type
                duration = (pd.to_datetime(event.time_end) - pd.to_datetime(event.time_start)).total_seconds()
                time_end = event.time_end
                # Add a row to the DataFrame
                df = pd.DataFrame({'V_id': [v_id], 'time_start': [time_start],'time_end': [time_end],'duration': [duration], 'event_type': [event_type]})
                dfs.append(df)
    result_df = pd.concat(dfs, ignore_index=True)

    # Convert the 'Time' column to datetime format
    result_df['time_start'] = pd.to_datetime(result_df['time_start'])
    result_df['time_end'] = pd.to_datetime(result_df['time_end'])
    # Sort the DataFrame based on the 'Time' column
    result_df = result_df.sort_values(by='time_start')

    return result_df


def get_distances_as_dataframe(scenario_id, buses):

    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    # get all vehicle events from this scenario at a station

    dfs = []

    for vehicle in vehicles:
        if vehicle.name_short in buses:
            v_id = vehicle.id
            rotations = scenario.rotation_set.filter(vehicle__isnull=False, vehicle_id=v_id)
            for rotation in rotations:
                r_id = rotation.id
                trips = scenario.trip_set.filter(rotation_id=rotation.id)
                for trip in trips:
                    routes = Route.objects.filter(scenario_id=scenario_id, id=trip.route_id)
                    for route in routes:
                        distance = route.distance

                        df = pd.DataFrame({'V_id': [v_id], 'R_id': [r_id], 'total_distance': [distance]})
                        dfs.append(df)

    result_df = pd.concat(dfs, ignore_index=True)
    result_df = result_df.groupby('R_id')['total_distance'].sum().reset_index()

    return result_df


def get_duration_as_dataframe(scenario_id, buses):
    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    # get all vehicle events from this scenario at a station

    dfs = []

    for vehicle in vehicles:
        if vehicle.name_short in buses:
            v_id = vehicle.id
            rotations = scenario.rotation_set.filter(vehicle__isnull=False, vehicle_id=v_id)
            for rotation in rotations:
                r_id = rotation.id
                trips = scenario.trip_set.filter(rotation_id=rotation.id)
                for trip in trips:
                    duration = (pd.to_datetime(trip.arrival_time) - pd.to_datetime(trip.departure_time)).total_seconds()
                    df = pd.DataFrame({'V_id': [v_id], 'R_id': [r_id], 'duration': [duration]})
                    dfs.append(df)
    result_df = pd.concat(dfs, ignore_index=True)
    result_df = result_df.groupby('R_id')['duration'].sum().reset_index()

    return result_df

def get_powerdraw_as_dataframe(scenario_id, buses):

    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    # get all vehicle events from this scenario at a station

    dfs = []

    for vehicle in vehicles:
        if vehicle.name_short in buses:
            v_id = vehicle.id
            v_typeid = vehicle.vehicle_type_id
            batterycapacity = VehicleType.objects.get(id=v_typeid)#
            charge_eff = VehicleType.objects.get(id=v_typeid)#

            events = scenario.event_set.filter(vehicle__isnull=False, station_id__isnull=False, vehicle_id=v_id)
            for event in events:
                time_start = event.time_start
                soc_start = event.soc_start
                station = event.station_id
                # Add a row to the DataFrame
                time_end = event.time_end
                soc_end = event.soc_end
                if soc_end > soc_start:
                    energy = soc_end-soc_start * charge_eff * batterycapacity
                    # Add a row to the DataFrame
                    df = pd.DataFrame({'V_id': [v_id], 'Time': [time_end], 'Energy': [energy], 'Station_id': [station] })
                    dfs.append(df)
    if dfs: #if not empty
        result_df = pd.concat(dfs, ignore_index=True)
    else:
        result_df = pd.DataFrame({'V_id': [None], 'Time': [None], 'Energy': [None], 'Station_id': [None]})


    return result_df

def get_vehicle_types(scenario_id, buses):
    filter_dict = dict(scenario_id=scenario_id)

    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!111",len(VehicleType.objects.filter(**filter_dict)))

    values_with_counts = VehicleType.objects.filter(**filter_dict).values('name').annotate(count=Count('name'))

    print("$$$$$$$$$$$$$$$$$$$$$$444")
    print(values_with_counts)

    df = pd.DataFrame(values_with_counts)

    return df

def get_df_perf():
    global df_perf
    return df_perf

def register_time(name, start, end, process):
    global df_perf
    df = pd.DataFrame({'name': [name], 'start': [pd.to_datetime(start, unit='s')], 'end': [pd.to_datetime(end, unit='s')], 'process': [process]})
    #df['start'] = pd.to_datetime(df['start'])
    #df['end'] = pd.to_datetime(df['end'])
    df_perf = pd.concat([df_perf, df], ignore_index=True, sort=False)

def reset_df_perf():
    global df_perf
    df_perf = pd.DataFrame({'name': [], 'start': [], 'end': [], 'process': []})
    df_perf['start'] = pd.to_datetime(df_perf['start'])
    df_perf['end'] = pd.to_datetime(df_perf['end'])
