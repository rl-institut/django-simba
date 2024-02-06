"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""
from ebustoolbox.models import (
    Scenario,
    Vehicle,
    Rotation,
    get_longest_distance_rotation,
    get_shortest_distance_rotation,
)
import pandas as pd

def get_all_buses(task_id: str) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    rotations = Rotation.objects.filter(scenario=s)
    all_buses = [r.vehicle.name_short for r in rotations]
    return all_buses


def get_report_numbers_text(filter_dict: dict):
    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s

    # Function calls annotate distance to Rotation
    longest_rotation = get_longest_distance_rotation(filter_dict)
    shortest_rotation = get_shortest_distance_rotation(filter_dict)
    # Add style if text should have special style
    styles = [{}, {}]
    return [
        f"Longest Rotation {longest_rotation.name} with {longest_rotation.distance} m",
        f"Shortest Rotation {shortest_rotation.name} with {shortest_rotation.distance} m",
    ], styles

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
def get_soc_as_dataframe(scenario_id):

    socs = get_soc(scenario_id)
    scenario = Scenario.objects.get(id=scenario_id)
    vehicles = Vehicle.objects.filter(scenario_id=scenario_id)

    dfs = []

    for vehicle in vehicles:
        v_id = vehicle.id
        print(socs.keys(), v_id)
        print(socs)
        for element in socs[vehicle.id]:
            time_start = element['time_start']
            soc_start = element['soc_start']
            # Add a row to the DataFrame
            df = pd.DataFrame({'V_id': [v_id], 'Time': [time_start], 'SOC': [soc_start]})
            dfs.append(df)
            time_start = element['time_end']
            soc_start = element['soc_end']
            # Add a row to the DataFrame
            df = pd.DataFrame({'V_id': [v_id], 'Time': [time_start], 'SOC': [soc_start]})
            dfs.append(df)
    result_df = pd.concat(dfs, ignore_index=True)

    # Convert the 'Time' column to datetime format
    result_df['Time'] = pd.to_datetime(result_df['Time'])

    # Sort the DataFrame based on the 'Time' column
    result_df = result_df.sort_values(by='Time')

    return result_df