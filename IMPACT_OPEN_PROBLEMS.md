# eflips-impact integration — open problems

Working list for the TCO/LCA integration in `ebustoolbox/impact.py`. Nothing here is
a blocker for the committed code; all of it is known and deferred.

Status as of 2026-08-13. Committed: `4d79867e` `4f42a3ef` `68aabb72` `54f5ab6e`
(TCO steps 1–4 + eflips-model 11.2.0), `518373ea` (Celery guard), `8c52cf61` (LCA).

Fixed since the list was written: **A12** below.

---

## A. LCA backend — found in the audit of `8c52cf61`

### A1. `lca.json`'s `eta_avail` is never used

`_eta_avail` prefers `Scenario.tco_parameters["eta_avail"]`, which `tco.json` sets to
**1.0**; `lca.json` says **0.9**. The LCA builds `ceil(n_ready / eta_avail)` vehicles,
so at 1.0 it builds ~11% fewer than the reference dataset assumes, and every
vehicle-production figure scales with that.

Sharing the value with the TCO is deliberate — the alternative is two calculations
costing different fleets — but editing `lca.json` here has no effect and the file does
not say so. Decide which file owns the number, then make the other one point at it.

Related: `if stored:` treats a stored `0.0` as unset.

### A2. The two default files disagree on battery life

`tco.json` `useful_life = 7` vs `lca.json` `battery_lifetime_years = 8.0`.
eflips-impact emits a `UserWarning` and continues, so the TCO amortises a battery over
7 years while the LCA amortises it over 8.

### A3. Hydrogen vehicle types are treated as battery-electric

`EnumEnergySource` has three members (`BATTERY_ELECTRIC`, `DIESEL`, `HYDROGEN`);
`ensure_lca_parameters` branches DIESEL vs *everything else*. A hydrogen bus gets BEB
parameters and grid-electricity emission factors, and `_write_lca_consumption` writes
measured kWh/km into it.

The pre-existing TCO default branch (`_tco_vehicle_type_defaults`) has the same shape,
so this is not new — but the LCA turns it from a cost error into an emissions error.
Compounding it, upstream's Area and vehicle queries filter on `BATTERY_ELECTRIC`, so a
hydrogen fleet's infrastructure is skipped rather than flagged: the result looks
plausible and is partial.

### A4. Battery chemistry falls back to LFP silently

Upstream `OpenLCAData.make_battery_type_lca_parameters` is
`chemistry.upper().startswith("NMC")` → NMC factors, *everything else including
`None`* → LFP. A transposition (`"NCM622"`) or an unmodelled chemistry (NCA, LTO)
becomes LFP with no warning. `fleet.json` ships `"lfp"`, so today it lands right by
intent rather than by check.

Fix on our side: validate `BatteryType.chemistry` against the set eflips-impact
actually distinguishes, and log when it does not match.

### A5. Sizing scenarios store an LCA result too

`_run_ebus_toolchain` runs once for the sizing scenario (deliberately extreme
consumption and temperature) and once for the default one; both now write
`lca_result`. The result page reads the DEFAULT child, so the user sees the right
figure — but the sizing scenario's stored value is roughly double and is not a
meaningful environmental result.

Anything reading `Scenario.lca_result` without filtering on `sim_type` (export,
compare, a future endpoint) will pick it up. Either skip the LCA for sizing runs or
make every reader filter.

### A6. `_write_charging_places` counts a narrower event set than upstream needs — crash path

**The most concrete item on this list.** `_write_charging_places` sweeps only
`CHARGING_OPPORTUNITY` events. Upstream's `extract_station_peaks` →
`eflips.eval.output.prepare.power_and_occupancy` matches **any** event with that
`station_id` (`SERVICE`, `STANDBY`, `STANDBY_DEPARTURE`, `CHARGING_DEPOT` all
reference stations) and skips a station only when it has none at all.

So a station with events but no opportunity charging gets `peak == 0` from our sweep,
is skipped, keeps `amount_charging_places = None`, and still reaches
`calculate_terminal_station_emissions`, where `n_plugs = None` →
`TypeError: unsupported operand type(s) for *: 'float' and 'NoneType'`. That is exactly
the crash the function was written to prevent.

Fix: sweep all events at the station, not just `CHARGING_OPPORTUNITY`. That also makes
the count match the `occupancy_total` upstream compares against in its oversizing
warning.

### A7. One missing `charging_point_type` aborts the whole LCA

`_get_cpt_params` raises `ValueError` for any Area or Station without one. It
propagates out of `calculate_lca` into the toolchain's `except`, so a single unattached
area costs the entire Ökobilanz, and the reason appears only in the log.

Consider verifying coverage in `attach_charging_point_types` and failing loudly there,
where the cause is still visible.

### A8. A failed LCA creates no `Notification`

Every other failure path in `_run_ebus_toolchain` writes a `Notification`; this one
writes a `logger.error`. The user gets a finished simulation with the section absent
and no way to know why. Swallowing the exception is right; staying silent is not.

### A9. `ensure_lca_parameters` runs twice per toolchain pass

Once in `eflips_calculate_tco` (`tasks.py:2521`) and again at the top of
`calculate_lca`. The first is redundant. It also resets
`average_consumption_kwh_per_km` to zero, so if the LCA later fails the stored
parameters are left describing a zero use phase.

### A10. `_electricity_consumption` is N+1

`Event.get_energy_delta()` touches `event.vehicle_type.battery_capacity`, one query per
event — measured 92 queries for 90 events. Pre-existing in `_write_energy_consumption`,
but the LCA now calls it on a second path and the toolchain runs twice, so it is 4× per
simulation. `.select_related("vehicle_type")` collapses it to one query.

### A11. Progress text sticks on "Berechne Ökobilanz"

The new step sets `progress.status` without incrementing `current_work`, so the bar
does not move and the text stays up through `check_event_soc_consistency` to the end of
the run.

### A12. A shared BatteryType was silently dropped at simulation — **fixed**

A vehicle type taken from the public fleet keeps that fleet's `battery_type_id`, so a
MUTATION could point at a row owned by scenario 3. `apply_tco_mutation` remaps
`battery_type_id` through the rows it copies out of the mutation, and a third
scenario's row is not among them: `battery_type_map.get()` returned `None` and wrote it
back, leaving the simulated vehicle type with no battery at all — no battery cost, no
battery production emissions, no error. Three simulated scenarios in the dev database
(S34, S39, S44) are in that state; six MUTATIONs were heading for it.

`_ensure_battery_types` now adopts a foreign row into the scenario before anything
writes to it (`_adopt_battery_type`), which also stops one scenario's battery price or
specific mass from becoming everyone's.

Still open: the same pattern for `ChargingPointType`. `_ensure_charging_point_types`
uses `get_or_create(scenario=...)`, so it always owns its rows — but nothing checks
that a `Station.charging_point_type` points into the same scenario.

---

## B. Carried over from the earlier list

### B1. `get_scaling_factor` uses `ceil` on the departure span

`365.0 / max(1.0, ceil(days))`. Correct only for whole-day-aligned schedules. A
schedule running Mon 04:00 → Wed 12:00 spans 2.33 days but is annualised as 3,
understating annual km by ~22% and overstating every per-km figure. Affects the TCO and
the LCA identically.

**Correction to an earlier note in this list:** *not* passing `extraction_window` is
right, and the earlier claim that it was a defect was wrong. Tested on scenario 100 —
passing an explicit trip-departure window *removes* revenue km (274,418 → 244,162),
because trips departing before the last departure but arriving after it get clipped.
Upstream's default event-span window is the correct, inclusive one. Only the `ceil` is
a problem.

### B2. Consumption defaults are stale

`tco.json` says 1.48 kWh/km; measured on a simulated scenario it is 1.8388.

### B3. Three `on_delete=CASCADE` FKs should be `SET_NULL`

### B4. No schema test for `tco.json`

`LcaDefaultsTest` now does this for the two LCA files; `tco.json` has no equivalent.

### B5. `load_tco_defaults` needs a cache-invalidation comment

`lru_cache` means editing the JSON requires a restart. Same now applies to
`load_lca_defaults` and `load_lca_overrides`.

### B6. `VehicleType.save()` duplicates a dict

### B7. Dead commented-out `eflips-tco` line at `pyproject.toml:55`

### B8. `tco.json` has no units

---

## C. To raise upstream with eflips-impact

- `Station.amount_charging_places` is multiplied unguarded in
  `calculate_terminal_station_emissions` while the oversizing check just above it does
  guard for `None` (see A6).
- The alembic revision for the `BatteryType.chemistry` retype uses `chemistry::text`,
  which keeps the JSON quotes — `"NMC622"` with quotes fails
  `.upper().startswith("NMC")` and silently selects LFP factors. Our migration
  `0095` uses `#>> '{}'` instead. django-simba has no alembic, so this only bites
  installations that migrate with theirs.
- `pytest` is declared as a runtime dependency.
- Battery chemistry selection is a silent fallback with no warning for unrecognised
  values (A4).
