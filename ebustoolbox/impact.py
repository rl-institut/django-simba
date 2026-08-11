"""Integration of eflips-impact (TCO) with the django-simba scenario graph.

The scenario the user edits in the wizard is not the scenario that is simulated::

    SOURCE_FILE -- SOURCE ---- MUTATION            (the wizard writes here)
                      |            |
                      +------------+--> SIMULATION (deepcopy(SOURCE) + mutations)

:func:`ensure_fleet_topology` creates the BatteryType / ChargingPointType rows that a
TCO calculation needs and seeds every ``tco_parameters`` column from the JSON defaults
in ``defaults/impact``. It runs on the MUTATION while the user is still in the wizard,
so the values it writes are on screen and editable before the simulation starts.
"""

import json
import logging
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from django.db.transaction import atomic

from .models import (
    BatteryType,
    ChargingPointType,
    Depot,
    EnumEnergySource,
    Scenario,
    Station,
    VehicleType,
)

logger = logging.getLogger("custom")

DATA_DIR = Path(__file__).resolve().parent / "defaults" / "impact"
FLEET_DEFAULTS_PATH = DATA_DIR / "fleet.json"
TCO_DEFAULTS_PATH = DATA_DIR / "tco.json"

# ChargingPointType has no "type" column, so the depot/opportunity discriminator has
# to live in name_short. These are the values used in fleet.json.
DEPOT_CPT_NAME_SHORT = "DCS"
OPPORTUNITY_CPT_NAME_SHORT = "OCS"

# Key under which the depot/station site-build costs are kept inside
# Scenario.tco_parameters. See charging_infrastructure_parameters().
CHARGING_INFRASTRUCTURE_KEY = "charging_infrastructure"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def load_fleet_defaults() -> dict:
    """Return the parsed contents of ``defaults/impact/fleet.json``."""
    with open(FLEET_DEFAULTS_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_tco_defaults() -> dict:
    """Return the parsed contents of ``defaults/impact/tco.json``."""
    with open(TCO_DEFAULTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _by_name_short(entries: list[dict], key: str) -> dict[str, dict]:
    """Index a list of JSON entries by one of their string fields."""
    return {entry[key]: entry for entry in entries if entry.get(key) is not None}


def _fleet_battery_defaults(name_short: str | None) -> dict:
    """Return specific_mass / chemistry for a vehicle type, falling back to the default."""
    fleet = load_fleet_defaults()
    override = _by_name_short(fleet.get("battery_types", []), "vehicle_name_short")
    entry = override.get(name_short, fleet["battery_type_default"])
    return {
        "specific_mass": float(entry["specific_mass"]),
        "chemistry": entry["chemistry"],
    }


def _tco_vehicle_type_defaults(vehicle_type: VehicleType) -> dict:
    """Return the default ``VehicleType.tco_parameters`` for a vehicle type."""
    tco = load_tco_defaults()
    override = _by_name_short(tco.get("vehicle_types", []), "name_short")
    entry = override.get(vehicle_type.name_short)
    if entry is not None:
        return {k: v for k, v in entry.items() if k != "name_short"}
    if vehicle_type.energy_source == EnumEnergySource.DIESEL:
        return deepcopy(tco["vehicle_type_default_diesel"])
    return deepcopy(tco["vehicle_type_default"])


def _tco_battery_type_defaults(name_short: str | None) -> dict:
    """Return the default ``BatteryType.tco_parameters`` for a vehicle type."""
    tco = load_tco_defaults()
    override = _by_name_short(tco.get("battery_types", []), "vehicle_name_short")
    entry = override.get(name_short)
    if entry is not None:
        return {k: v for k, v in entry.items() if k != "vehicle_name_short"}
    return deepcopy(tco["battery_type_default"])


def scenario_tco_defaults() -> dict:
    """Return the default ``Scenario.tco_parameters``."""
    return deepcopy(load_tco_defaults()["scenario"])


# ---------------------------------------------------------------------------
# Step 1: pre-simulation fleet topology
# ---------------------------------------------------------------------------


@atomic()
def ensure_fleet_topology(scenario: Scenario) -> None:
    """Create the fleet rows a TCO calculation needs, if the scenario has none.

    Idempotent and non-destructive: existing rows and non-default parameter values
    are never overwritten, so it is safe to call on every request and again from the
    toolchain. It is called from :class:`ebustoolbox.views.CostsView` so the values
    it writes are on screen and editable before the user starts the simulation.

    Creates one :class:`~ebustoolbox.models.BatteryType` per battery-electric vehicle
    type that has none, one :class:`~ebustoolbox.models.ChargingPointType` per
    charging type, and seeds the ``tco_parameters`` of the scenario, its vehicle
    types and its stations from ``defaults/impact/tco.json``.

    :param scenario: The scenario to complete. In the wizard this is the MUTATION.
    """
    _ensure_scenario_parameters(scenario)
    _ensure_battery_types(scenario)
    _ensure_charging_point_types(scenario)
    _ensure_station_parameters(scenario)


def _seed_from_json(current: dict | None, json_defaults: dict, model_default: dict | None) -> dict:
    """Merge JSON defaults into a stored ``tco_parameters`` value, key by key.

    Postgres fills these columns from the model's ``db_default`` at INSERT, so a row
    is never empty by the time this runs; a plain "keep whatever is stored" merge
    would make ``defaults/impact/tco.json`` inert and leave two competing sources of
    truth for the same numbers. A key that is missing, or still holds the model
    default, therefore counts as unset and takes the JSON value. Any other value is
    the user's and is kept.

    The comparison is per key rather than on the whole dict, so a row written before a
    key was added to the model default is not mistaken for a user edit. Keys present
    only in the stored value (such as the ``procurement_cost_diesel`` left over from
    eflips-tco) are carried through untouched.

    A user who types a value identical to the model default loses that edit on the
    next call. Distinguishing the two would need the edits tracked separately, which
    is not worth a column here.

    :param current: The stored value, possibly ``None``.
    :param json_defaults: The corresponding block of ``defaults/impact/tco.json``.
    :param model_default: The field's ``db_default``.
    :returns: The value to store.
    """
    current = current or {}
    model_default = model_default or {}

    result = dict(current)
    for key, json_value in json_defaults.items():
        if isinstance(json_value, dict):
            # fuel_cost, cost_escalation_rate, ... follow the same rule one level down.
            result[key] = _seed_from_json(current.get(key), json_value, model_default.get(key))
        elif key not in current or current[key] == model_default.get(key):
            result[key] = json_value
    return result


def _ensure_scenario_parameters(scenario: Scenario) -> None:
    """Seed ``Scenario.tco_parameters`` from ``defaults/impact/tco.json``.

    Also backfills ``eta_avail`` for scenarios stored before it was added, which
    ``TCOCalculator`` reads unconditionally and would otherwise fail on.
    """
    merged = _seed_from_json(
        scenario.tco_parameters,
        scenario_tco_defaults(),
        Scenario._meta.get_field("tco_parameters").db_default,
    )
    if merged != scenario.tco_parameters:
        scenario.tco_parameters = merged
        scenario.save(update_fields=["tco_parameters"])


def _ensure_battery_types(scenario: Scenario) -> None:
    """Create and assign a BatteryType for every BEB vehicle type that lacks one.

    One row per vehicle type rather than one shared row: eflips-impact prices a
    battery as ``procurement_cost * VehicleType.battery_capacity``, so the parameters
    are only meaningful per vehicle type.
    """
    for vehicle_type in VehicleType.objects.filter(scenario=scenario):
        update_fields = []

        merged = _seed_from_json(
            vehicle_type.tco_parameters,
            _tco_vehicle_type_defaults(vehicle_type),
            VehicleType._meta.get_field("tco_parameters").db_default,
        )
        if merged != vehicle_type.tco_parameters:
            vehicle_type.tco_parameters = merged
            update_fields.append("tco_parameters")

        is_beb = vehicle_type.energy_source == EnumEnergySource.BATTERY_ELECTRIC
        if is_beb and vehicle_type.battery_type_id is None:
            fleet_defaults = _fleet_battery_defaults(vehicle_type.name_short)
            battery_type = BatteryType.objects.create(
                scenario=scenario,
                specific_mass=fleet_defaults["specific_mass"],
                chemistry=fleet_defaults["chemistry"],
                tco_parameters=_tco_battery_type_defaults(vehicle_type.name_short),
            )
            vehicle_type.battery_type = battery_type
            update_fields.append("battery_type")
            logger.info(
                f"S.ID:{scenario.id}:Created BatteryType {battery_type.id} "
                f"for VehicleType {vehicle_type.id} ({vehicle_type.name_short})"
            )

        if update_fields:
            vehicle_type.save(update_fields=update_fields)


def _ensure_charging_point_types(scenario: Scenario) -> None:
    """Create the depot and opportunity ChargingPointType rows if they are missing.

    Both are created unconditionally. Which one is actually used is only decided
    after the simulation, in :func:`attach_charging_point_types`; an unused
    ChargingPointType contributes nothing to the TCO because eflips-impact prices it
    from the Areas and Stations pointing at it.
    """
    tco_defaults = load_tco_defaults()["charging_point_types"]
    for entry in load_fleet_defaults()["charging_point_types"]:
        # name_short carries the type, so it is the lookup key rather than a default.
        _, created = ChargingPointType.objects.get_or_create(
            scenario=scenario,
            name_short=entry["name_short"],
            defaults={
                "name": entry["name"],
                "tco_parameters": deepcopy(tco_defaults[entry["type"]]),
            },
        )
        if created:
            logger.info(f"S.ID:{scenario.id}:Created ChargingPointType '{entry['name_short']}'")


def charging_infrastructure_parameters(scenario: Scenario) -> dict:
    """Return the site-build costs for this scenario, keyed ``depot`` / ``station``.

    Stored on the scenario rather than per station because the wizard has to collect
    them before it is known which station becomes a depot — the costs page runs before
    the depot page, and the :class:`~ebustoolbox.models.Depot` rows themselves are only
    created during the simulation. :func:`_ensure_station_parameters` distributes them
    onto the stations once that is settled.

    eflips-impact reads named keys out of ``Scenario.tco_parameters`` and ignores the
    rest, so keeping them there costs nothing and means they ride along with the
    existing mutation carry-over instead of needing one of their own.
    """
    defaults = load_tco_defaults()["charging_infrastructure"]
    stored = (scenario.tco_parameters or {}).get(CHARGING_INFRASTRUCTURE_KEY) or {}
    return {key: {**value, **(stored.get(key) or {})} for key, value in defaults.items()}


def _ensure_station_parameters(scenario: Scenario) -> None:
    """Distribute the site-build costs onto the scenario's stations.

    These are the costs of building the charging site itself, which eflips-impact
    prices per station and groups by the value of this column. The model's
    ``db_default`` has ``procurement_cost: 0``, so without this every station would be
    free.

    Which stations are depots is only known once a :class:`~ebustoolbox.models.Depot`
    exists, and that happens during the simulation — after this has already run once on
    the MUTATION, where every station necessarily looks like an ordinary charging
    station. Since both parameter sets live on the scenario, the toolchain's second
    call simply re-runs the split and the depot stations pick up the depot values then.
    """
    parameters = charging_infrastructure_parameters(scenario)
    depot_station_ids = set(
        Depot.objects.filter(scenario=scenario).values_list("station_id", flat=True)
    )

    for station in Station.objects.filter(scenario=scenario):
        wanted = deepcopy(parameters["depot" if station.id in depot_station_ids else "station"])
        if station.tco_parameters == wanted:
            continue
        station.tco_parameters = wanted
        station.save(update_fields=["tco_parameters"])
