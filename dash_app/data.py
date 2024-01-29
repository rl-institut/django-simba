"""This file should handle all data access, and handling of the dash app, including imports

This way data should be easily swappable, while the dash_layout allows for swapping of the design
"""
from ebustoolbox.models import (
    Scenario,
    Rotation,
    get_longest_distance_rotation,
    get_shortest_distance_rotation,
)


def get_all_buses(task_id) -> list[str]:
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
