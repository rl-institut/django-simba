# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Template:
```
## [0.0.0] - Name of Release - 20YY-MM-DD

### Added
- [(#)]()
### Changed
- [(#)]()
### Removed
- [(#)]()
```

## [x.x.x] - Unreleased
### Changed
- [(#243)](https://github.com/rl-institut/django-simba/pull/243)
- The website supports basic translation.
```terminal
django-admin makemessages -a
django-admin compilemessages
```
- For now the language is set via a cookie when calling "de" or "en" as LANGUAGE_ABBREVIATION
`
/set_lang/LANGUAGE_ABBREVIATION
`

### Changed
- [(#242)](https://github.com/rl-institut/django-simba/pull/242)
- Updated station and depot icons on map

### Changed
- [(#223)](https://github.com/rl-institut/django-simba/pull/223)
- Dash app is removed completely from website
  - Visualization now uses [echarts.js]( https://echarts.apache.org/en/index.html)

### Changed
- [(#220)](https://github.com/rl-institut/django-simba/pull/220)
- SimbaScheduleReader determines encoding via charset_normalizer
- Uses this encoding to read files to database

### Changed
- [(#161)](https://github.com/rl-institut/django-simba/pull/161)
- Fixes #159
- Introduces new app for getting BusSystem Related Data called "data_scrapers"
- Further Information can be found here
    - [ReadMe](https://github.com/rl-institut/django-simba/tree/dev?tab=readme-ov-file#data_scrapers)


### Changed
- [(#152)](https://github.com/rl-institut/django-simba/pull/152)
- Fixes issues with plotting when simulation is not finished

### Changed
- [(#151)](https://github.com/rl-institut/django-simba/pull/151)
- Fixes #147
Fixes #145
- Changes proposed in this pull request:

    - Scenario handling is changed in the following way using the Wizard:

    - Raw Scenario is created through a ScheduleReader

    - Wizard: Saves Mutations of this scenario to a child scenario/ MutationScenario

    - Links are stored between the Raw Scenario and the mutation scenario, e.g. VehicleType 5 of RawScenario will be mutated by VehicleType 6 by the mutation Scenario

    - During Simulation a deepcopied RawScenario will be mutated by the Mutation scenario. This is a child Scenario of the Mutation Scenario.

- MutationScenarios can be copied by calling _simba/copy/_{_task_id_}



### Changed
- [(#124)](https://github.com/rl-institut/django-simba/pull/108)
- Make Area.VehicleType nullable (a None VehicleType represents "any").



## [x.x.x] - Unreleased
### Changed
- [(#108)](https://github.com/rl-institut/django-simba/pull/108)
- Add depot input page
- Add new default vehicle types
-
- [(#97)](https://github.com/rl-institut/django-simba/pull/97)
- Updates SimBA and eflips-ingest
- Implement VDV Ingester of eflips-ingest
- Properly populate scenario.simba_options with values after every call of a ScheduleReader
- Implement local elevation_api with similar spec to openelvation_api
  - Calling the API the first time will download the necessary dgm200m files
- Secure this API with an optional token DJANGO_ELEVATION_TOKEN in .env
  - In this case, a request must contain the key "token" with the given value
- Pass prints to logger instead
- Add default_optimizer.cfg file
- Add apply button for filtering vehicles in plots
- Fix issue with result cache for plotting




### Changed
- [(#98)](https://github.com/rl-institut/django-simba/pull/98)
- Fixed issuses from PR#76
- Fixed and added plots
- translation of plots and labels
- more readable Bus IDs


### Changed
- [(#93)](https://github.com/rl-institut/django-simba/pull/93)
- Database depot stations get electrified with default values if running the toolchain, if they are not previously electrified
- Fixes Issue with large charging powers leading to missing charge events
- Fixes issue with vehicle naming when plotting
- Allows missingimage warning of maplibre during testing

Optimizations
- SimBA scenario simulation is sped up by using a greedy SpiceEV simulation mode.
- EventOutput generation is sped up by using optimized querys and data structure

Features
- Added the Mode station_optimization_single_step which runs a single step of the station optimization, i.e. electrifies a single station with the highest potential.
- run_simba_scenario can now be used to run simba with a defined database scenario. A database url can be used to use a non-default django database
- Scenarios are now deepcopied before being simulated, and the deepcopy is added as parent. This gurantees a non mutated scenario is stored in the database, while the original scenario can be changed, e.g. stations can be electrified
- An option has been introduced to simulate the root parent scenario of a given scenario. In this case a child scenario is created. This feature is still work in progress


### Changed
- [(#95)](https://github.com/rl-institut/django-simba/pull/95)
- Add support for email backends, including Microsoft Exchange with self-signed certificates.

- [(#87)](https://github.com/rl-institut/django-simba/pull/87)
- Add a working (dummy for now) eflips-ingest ingester and the code around it.
- Change the models to use db_default values for compatibility with eflips-model

- [(#62)](https://github.com/rl-institut/django-simba/pull/62)
- `Scenario.created` is now a datebase default timestamp if no client side timestamp is given
  - Django will continue supplying the default timestamp to no break compatibility with existing migrations
- Fixed issue regarding failing local tests when sql-sequence resetting

- [(#88)](https://github.com/rl-institut/django-simba/pull/88)
- Fixes Issue where soc timeseries in events contained null/none values
- Fixes Issue with missing events
- Fixes Issue with unsorted trips from database
- Fixes Issue with SimBA being dependent on Vehicle Naming
- Speeds up reading Trips and Rotations from Database
- Remove tests without celery
- Error Propagation from celery is now turned on via default, if celery is in task_always_eager mode

- [(#82)](https://github.com/rl-institut/django-simba/pull/82)
- Fixed proper file deletion after reading the files using a ScheduleReader
- Added DJANGO_LOCAL_DEVELOPMENT=True setting to .env. Used if security features needed for production should be disabled.
- Override settings for tests so they run through

### Added
- [(#81)](https://github.com/rl-institut/django-simba/pull/81)
- Implements new frontend workflow
- SimBA schedule reader sets depots
- Add consumption to default VehicleTypes
- Combine _run_ebus_toolchain and _celery_run_ebus_toolchain

- [(#79)](https://github.com/rl-institut/django-simba/pull/79)
- Implements an abstract class of ScheduleReader which allows importing of different Schedule Types like SimBA, GTFS or VDE
- Implements a Progressbar with a Progress model
- Adds files for Front-End workflow

- [(#80)](https://github.com/rl-institut/django-simba/pull/80)
- Bump eflips-depot dependency to v3
- Add depot.station field to django model
- Fix test cases that are failing on py3.12 due to deprecations

- [(#60)](https://github.com/rl-institut/django-simba/pull/60)
- Adds loop over different toolchain modes
- Station-optimization is turned on per default
- Removes simba.report from loop and moves it to a single post processing call
- Missing: Frontend Implementation of choosing Modes

- [(#67)](https://github.com/rl-institut/django-simba/pull/67)
- run_simba_from_scenario and run_toolchain_from_scenario get keyword argument to assign vehicles to rotations using the
  simba naming convention.
- set delete_existing_depots=True in eflips depot generator
- Running a finished Scenario is now possible
- Running a Scenario with missing vehicle_assignments is now possible by setting assign_vehicles=True

- [(#57)](https://github.com/rl-institut/django-simba/pull/57)
- Add consumption model
  - Consumption model looks up consumption multidimensional input conditions. Nearest and Interpolation Lookup is
    possible
- Consumption models can be separated from a scenario to allow generic consumption table storage
- Vehicle type can have a numeric consumption or a foreign key to a consumption table
- Change example trips.csv, so rotations can share a single vehicle
- Give example vehicles a file reference to a consumption file
- Input form: Rename vehicle_types to vehicle_types_path
- Input form: Add consumption_path. For now only a single file is possible in the frontend, but no backend limitation
  exists

- [(#58)](https://github.com/rl-institut/django-simba/pull/58)
- Upgrade to maplibre 4.0.2
- Upgrade to django-mapengine 0.18

- [(#55)](https://github.com/rl-institut/django-simba/pull/55)
- Add deepcopy function to 'core'
- Test deepcopy
- Add simba and toolchain call with a scenario as input.
  - Scenarios are not purged of their events. This can lead to side effects
  - Toolchain contains a depot generation which fails if the scenario has a depot associated with it already
- Add a basic filter to run trip data which contains false data
  - duplicate departure or arrival times are handled by removing trips
  - rotations which do not start at the depot are removed
- Add some timing prints of function blocks. Should be moved to a logger at some point
- Add vehicle counting to make db schedule consistent

- [(#54)](https://github.com/rl-institut/django-simba/pull/54)
- Updates project dependencies to eflips-depot 2.0
- Closes [(#53)](https://github.com/rl-institut/django-simba/issues/53). Fix SoC plotting

- [(#48)](https://github.com/rl-institut/django-simba/pull/48)
-  Update poetry.lock to import fixed eflips-depot.
- use the new generate_depot_layout() method.
- Update Simba Version
- SimBA does not create 0 Duration Events anymore
- SimBA does not create events past the last trip. The last event is always a driving event.
- SimBA reads Rotation Start SOCs from the Database-Event before rotation departure.
  - In some cases there is no event.
  - Vehicle renaming is handled in django. Might need an overhaul later.
  - Unused vehicles get deleted by django
- Fix tests by using other TestCase
  - Add temperatures to testing if db and "normal" version are the same

- [(#45)](https://github.com/rl-institut/django-simba/pull/45)
- Eflips-depot is currently turned off in the toolchain, since it breaks testing
- Changed the type of datetimes and data to arrayfields
- Temperatures or other values can now be easily interpolated or looked up
- Test file for temperatures uploaded
- Add bootstrap4 to requirements
- Generate vehicles with the same name as SimBA
- Fix Popups of map stations
- Make dash_app a separate app
- Implement a few basic filterable plots.
- Use "Agg" as matplotlib backend. If problems arise on your machine, the backend will be moved to the env variables.
- Make geom for Stations nullable
- Use PyPi install of eflips-depot
- remove vehicle properties as deprecated
- Write VehicleEvents from SimBA
- Add a dockerfile and a docker compose to run project as docker locally

- [(#40)](https://github.com/rl-institut/django-simba/pull/40)
- Expands the database according to discussed specifications.
- Add VehicleClass for Eflips Depot Simulations.
- Update task functions with new database models
- Add Temperatures Model to store temperatures
  - Add tests
- Add functionality to interpolate between datetimes
- Expand tests, so version with and without eflips are included
- SimBA only calculates the consumption of a single vehicle type, i.e. one type per rotation.

- [(#38)](https://github.com/rl-institut/django-simba/pull/38)
- Expands the database according to discussed specifications.
- Removes VehicleClass from SIMBA algorithm, meaning, only a single VehicleType will be run per rotation.
- Adjusts tests accordingly
- Refactoring of the objects_digger used to compare complex objects using their primitive base / leaf types
- Added functionality to turn of eflips in the basic toolchain
  - .env file now makes use of an optional setting EFLIPS_USE=False. The default value for this setting is True, so the standard behavior does not change

- Added the feature to read the database and generate a SimBA Schedule from the database.
- Added tests to check that scenarios from the database are generating the same results as default simulations.
- Added function save_and_simulate() with default behavior of running a simulation.

- [(#29)](https://github.com/rl-institut/django-simba/pull/29)The generated input for eflips gets the database ID of the vehicle_type instead of the simba specific name of the vehicle type
- Output for eflips also generates consumption of opportunity chargers on depot rotations without opportunity charging.
- vehicle_class is read by django. Depot rotations are run with each vehicle type of the vehicle_class
- Adjusted vehicle_class to be many-to-many relation with vehicle_type
- simba.rotation now contains a vehicle_class, which can be read by defining a vehicle_type list instead of a single vehicle type with ";" as seperator, eg. ,AB;SB, instead of ,AB,. The default vehicle_type in case of a list is the first vehicle_type of this list

- [(#26)](https://github.com/rl-institut/django-simba/pull/26) Added dev tools for consistent formatting
- [(#35)](https://github.com/rl-institut/django-simba/pull/35) Basic toolchain implements eflips and SimBA communication
