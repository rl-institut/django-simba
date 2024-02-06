"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""
import numpy as np

from ebustoolbox.models import (
    Scenario,
    Event,
    Rotation,
    get_longest_distance_rotation,
    get_shortest_distance_rotation,
    EventType,
)

from django.db.models import Min
from dash.exceptions import PreventUpdate


def get_all_buses(task_id: str) -> list[str]:
    s = Scenario.objects.get(task_id=task_id)
    rotations = Rotation.objects.filter(scenario=s)
    all_buses = [r.vehicle.name_short for r in rotations]
    return all_buses


def get_report_numbers_text(filter_dict: dict):
    task_id = filter_dict.pop("task_id")
    s = Scenario.objects.get(task_id=task_id)
    filter_dict["scenario"] = s
    if len(filter_dict["vehicle__name_short__in"]) == 0:
        raise PreventUpdate

    # Function calls annotate distance to Rotation
    longest_rotation = get_longest_distance_rotation(filter_dict)
    shortest_rotation = get_shortest_distance_rotation(filter_dict)
    # lowest_soc_event = Event.objects.filter(**filter_dict).order_by("soc_start").first()

    # Add style if text should have special style
    styles = [{}, {}]
    return [
        f"Longest Rotation {longest_rotation.name} with {longest_rotation.distance} m",
        f"Shortest Rotation {shortest_rotation.name} with {shortest_rotation.distance} m",
        # f"Lowest soc {lowest_soc_event.soc_start:.2f} at {lowest_soc_event.station.name} for "
        # f"Vehicle {lowest_soc_event.vehicle.name_short} at"
        # f"{lowest_soc_event.time_start:%d-%m-%Y %H:%M}",
    ], styles


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
