# HPC Tool

## Overview

This software is designed to analyze urban infrastructure around bus stops, evaluating the feasibility of placing electric bus charging stations based on proximity to various urban features such as trees, residential areas, and cycle paths. It uses geospatial data from multiple sources (e.g., WFS services, OSM) and processes it using Python libraries like GeoPandas, Shapely, and NetworkX. The output provides a classification of potential charging station locations as "good," "medium," or "bad" based on predefined criteria.

---
## Key features

- Automatic placement of buses based on land parcels
- Drawing a station area where buses are automatically placed
- Evaluation of the quality of individual bus locations
- Trees, bike paths, monument protection, and residential areas are considered in the evaluation
- Deleting individual buses


## How It Works

### 1. **Input Data**
- The script takes a list of polygon coordinates (`poly_list`) representing the area of interest.
- It also accepts parameters like **bus length, parking distance, bounding box size (`bb_size`)**, and **subsampling resolution** (default: **20 cm**).

### 2. **Data Loading**
- Geospatial layers (e.g., **trees, residential areas, cycle paths**) are loaded dynamically using **WFS services**.
- Streets are extracted from **OpenStreetMap (OSM)** via the **"ox"** package, filtering out footpaths, tunnels, etc.

### 3. **Graph Construction**
- A **graph** is constructed from the street network:
  - **Nodes** represent intersections or endpoints.
  - **Edges** represent street segments.
  - Nodes are **labeled** based on their connectivity (e.g., **intersections, straight segments, isolated points**).
- A **Depth First Search (DFS)** is performed to identify **long, connected street segments** without intersections.
  - A list of connected nodes that **fulfill a curvature constraint** is aggregated, and their total length is calculated.

### 4. **Bus Placement**
- The list of connected nodes is **subsampled** according to the specified **resolution parameter** (default: **20 cm**).
- **Bus polygons** are placed along the street segments based on:
  - **Bus dimensions**
  - **Parking distance**
  - **Street geometry**
- Buses are **rotated** to match the **street’s angle** in the global coordinate system.

### 5. **Pantograph Location Evaluation**
- **Pantograph locations** (charging points) are evaluated based on their **proximity to urban features** (e.g., trees, cycle paths, residential areas).
- A **traffic light scale (green, orange, red)** is used to classify their suitability.

### 6. **Output**
- The final output is a list of **bus placements** along the identified street segments.

---

## Function Descriptions

### `extractWohngebiet(local_layerdict)`
- **Purpose**: Extracts residential areas from the "Tatsächliche Nutzung" layer.
- **Input**: A dictionary of geospatial layers.
- **Output**: A GeoDataFrame containing residential areas.

### `calculate_HPC(poly_list, buslength=18, parkingdistance=5)`
- **Purpose**: Main function to calculate the feasibility of placing charging stations.
- **Input**:
  - `poly_list`: List of polygon coordinates.
  - `buslength`: Length of the bus (default: 18 meters).
  - `parkingdistance`: Distance between parked buses (default: 5 meters).
- **Output**: A string with counts of "good," "medium," and "bad" charging stations, and a list of their IDs.

### `generalizedLayerLoader(wfs_url, type_names, sbbox, version='2.0.0', retries=3, timeout=30)`
- **Purpose**: Loads geospatial data from a WFS service.
- **Input**:
  - `wfs_url`: URL of the WFS service.
  - `type_names`: Name of the layer to query.
  - `sbbox`: Bounding box coordinates.
- **Output**: A tuple indicating success and the loaded GeoDataFrame.

### `calculate_curvature(nodes)`
- **Purpose**: Calculates the curvature of a street segment.
- **Input**: List of nodes (coordinates).
- **Output**: Array of curvature values.

### `subsample_line(start_point, end_point, step_size)`
- **Purpose**: Subsamples a line segment at regular intervals.
- **Input**:
  - `start_point`, `end_point`: Start and end points of the line.
  - `step_size`: Distance between subsampled points.
- **Output**: List of subsampled points.

### `place_bus_along(nodes, bus_laenge, park_abstand, panto_loc)`
- **Purpose**: Places buses along a street segment.
- **Input**:
  - `nodes`: List of nodes defining the street segment.
  - `bus_laenge`: Length of the bus.
  - `park_abstand`: Parking distance between buses.
  - `panto_loc`: Pantograph location.
- **Output**: List of bus polygons and pantograph positions.

### `getStreetsfromOSM(geometry, tags)`
- **Purpose**: Extracts streets from OpenStreetMap.
- **Input**:
  - `geometry`: Polygon defining the area of interest.
  - `tags`: Tags to filter streets (e.g., highways).
- **Output**: GeoDataFrame of streets.

### `makeConnections(gdf)`
- **Purpose**: Creates a graph from a GeoDataFrame of streets.
- **Input**: GeoDataFrame of streets.
- **Output**: DataFrame of nodes with attributes.

### `makeGraph(nodes2geo)`
- **Purpose**: Constructs a directed graph from node attributes.
- **Input**: DataFrame of nodes.
- **Output**: Directed graph.

### `findConnectedSegments(G, start_node, visited=set())`
- **Purpose**: Finds connected street segments in the graph.
- **Input**:
  - `G`: Graph of street segments.
  - `start_node`: Starting node.
  - `visited`: Set of visited nodes.
- **Output**: List of connected segments and updated visited set.

### `angle_between(a, b, c)`
- **Purpose**: Calculates the angle between three points.
- **Input**: Coordinates of three points.
- **Output**: Angle in degrees.

### `calculate_path_angles(coords_list)`
- **Purpose**: Calculates angles between incoming and outgoing edges in a path.
- **Input**: List of coordinates.
- **Output**: List of angles.

### `distance(pt, points_df, searchradius=30)`
- **Purpose**: Calculates the minimum distance between a point and a set of points.
- **Input**:
  - `pt`: Point of interest.
  - `points_df`: GeoDataFrame of points.
  - `searchradius`: Maximum search radius.
- **Output**: Minimum distance.

### `distance_poly(pt, polygon, default=-1)`
- **Purpose**: Calculates the distance between a point and a polygon.
- **Input**:
  - `pt`: Point of interest.
  - `polygon`: GeoDataFrame of polygons.
  - `default`: Default distance if no intersection is found.
- **Output**: Distance.

### `placeColoredPanthograph(pnt, local_layerdict, criteria)`
- **Purpose**: Classifies a pantograph based on proximity to urban features.
- **Input**:
  - `pnt`: Pantograph location.
  - `local_layerdict`: Dictionary of geospatial layers.
  - `criteria`: Dictionary of evaluation criteria.
- **Output**: Counts of "good," "medium," and "bad" classifications.


## Bugs, Style Issues, and TODOs

- CRS mismatch arround L 254 intersection = gpd.sjoin(point_gdf, alkis, how="inner", op="within")
- hardcoded ALKIS File location ( HPCcharger.py line 133)
- Mix of german and english
- widen bounds of sBBOX (currently it is kept the size that is drawn, but should be expanded by the max. red distance) (Trees, Bikepaths, etc. are only considered, if within the drawn area)
- Put HPC stuff on own layer, rename "examplemultipolygon"
- Find a better way to store and change the Settings
- Empty Layers: If a WFS request fails or returns an empty layer, the script may not handle it gracefully.
- Coordinate Systems: Mismatched CRS transformations could lead to incorrect results.
- Edge Cases: Segments with zero length or invalid geometries may cause errors.
- Timeouts: WFS requests with large bounding boxes may time out.
- Hardcoded Values: Some parameters (e.g., `bb_size`, `dist_tree_red`) are hardcoded and should be configurable.
- Naming Conventions: Variable names like `lgdf`, `cdr`, and `n2g` are unclear and should be renamed for readability.
- Error Handling: Many functions lack robust error handling.


