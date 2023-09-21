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
- [(#29)](https://github.com/rl-institut/django-simba/pull/29)The generated input for eflips gets the database ID of the vehicle_type instead of the simba specific name of the vehicle type
- Output for eflips also generates consumption of opportunity chargers on depot rotations without opportunity charging.
- vehicle_class is read by django. Depot rotations are run with each vehicle type of the vehicle_class
- Adjusted vehicle_class to be many-to-many relation with vehicle_type
- simba.rotation now contains a vehicle_class, which can be read by defining a vehicle_type list instead of a single vehicle type with ";" as seperator, eg. ,AB;SB, instead of ,AB,. The default vehicle_type in case of a list is the first vehicle_type of this list


### Added
- [(#26)](https://github.com/rl-institut/django-simba/pull/26) Added dev tools for consistent formatting
### Changed
- [(#)]()
### Removed
- [(#)]()