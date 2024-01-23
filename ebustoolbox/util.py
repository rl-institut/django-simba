from .models import Scenario
from celery import uuid

# make matplotlib not use Tk, run on main thread
import matplotlib

matplotlib.use("Agg")


def get_unique_task_id() -> str:
    task_id_not_unique = True
    task_id = None
    # Create unique ids for as long as needed, so no duplicate ids exist
    while task_id_not_unique:
        try:
            task_id = uuid()
            Scenario.objects.get(task_id=task_id)
        except Scenario.DoesNotExist:
            task_id_not_unique = False
    return task_id


# aggregate events
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


def get_stations(scenario_id):
    """
    Get station data for a scenario.

    per station and timestep:
    - grid power
    - occupancy
    - charged energy

    per station total:
    - charged power

    :param scenario_id: ID of scenario to aggregate
    :type scenario_id: int
    :return: station ID -> aggregated information
    :rtype: JSON
    """
    scenario = Scenario.objects.get(id=scenario_id)
    events = scenario.event_set.filter(station__isnull=False)
    return dict()


def rotation_filter(scenario_id, threshold=0):
    """
    Find all rotations in a scenario where the SoC does not sink below a given threshold.

    :param scenario_id: ID of scenario to aggregate
    :type scenario_id: int
    :param threshold: lower SoC limit. Optional, default 0. Between 0 and 1
    :type scenario_id: numeric
    :return: rotation IDs where SoC never below threshold
    :rtype: list
    """
    scenario = Scenario.objects.get(id=scenario_id)
    events = scenario.event_set.filter(trip__isnull=False)
    # get all rotations with trips below threshold
    below_threshold = events.filter(soc_end__lt=threshold).values('trip__rotation')
    # get all scenario rotations with no trip below
    rotations = scenario.rotation_set.exclude(id__in=below_threshold)
    return [r.id for r in rotations]
