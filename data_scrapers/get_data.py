"""Script to generate bus_stop data via an osm.pbf file.
Meant to be run once locally to generate a file that can be imported into the database of WeBus.
First use osmium-tool to generate filtered files of admin areas and bus stops in the terminal via:

osmium tags-filter "germany-latest.osm.pbf" n/highway=bus_stop -o bus_stops.osm.xml
osmium tags-filter "germany-latest.osm.pbf"  wr/boundary=administrative -o admin.osm.xml

osmium-tool can be downloaded from:
https://osmcode.org/osmium-tool/

These xml files are readable by geopandas and analyzed to generate a hierarchy of admin areas.
The hierarchy lets each admin area know which next higher level admin area they are contained in.

Bus stops are annotated with the lowest admin area they are contained in.
This data can be used to properly locate bus stops with non unique names.
Example: "Mitte" is the name of multiple bus stops in Germany.
Using "Berlin, Mitte" it is possible to identify a unique bus stop in Berlin, called "Mitte"."""

import geopandas as gpd
from shapely import STRtree
import tqdm
import logging

layers = ["points", "lines", "multilinestrings", "multipolygons", "other_relations", "polygons"]
logging.info("Reading osm files")
df_bus = gpd.read_file("bus_stops.osm.xml", layer=layers[0])
df_admin = gpd.read_file("admin.osm.xml", layer=layers[3])


df_admin.loc[df_admin.admin_level.isna(), "admin_level"] = 0
df_admin.admin_level = df_admin.admin_level.astype(int)


# restrict administration levels between Bundesländer (level 4) and Gemeinden/Bezirke (level 9)
df_admin = df_admin[df_admin.admin_level >= 4]
df_admin = df_admin[df_admin.admin_level <= 9]
df_admin.reset_index(drop=True, inplace=True)

logging.info("Finding admin areas contained within each other using STRtree")
# Find the admin areas which contain the representative_point of each admin area

# Each geometry is cast to a representative point which is guranteed to be part of itself
all_centers = [a.representative_point() for a in df_admin.geometry]

# Create a STRtree
# A query - only R - tree spatial index created using the Sort-Tile-Recursive(STR) algorithm.
# This allows for spatial queries of other geometries.
stree = STRtree(df_admin.geometry)

# Find geometries which contain the points.
# stree.query(all_centers) returns all matches of input geometry and tree geometries,
# where the bounding box of each input geometry intersects the bounding box of the tree geometry.
# This does not guarantee that the point is contained in the geometry.
# Therefore the predicate kwarg is given. predicate="intersects" further filters those geometries
# that meet the predicate("intersect") when comparing the input geometry to the tree geometry.
# This differs from the comparison of the bounding boxes and guarantees true containment.

# results has content looking like this
# [0,0,  0,  1, 1,..]
# [5,0,322,1023,6,..]
# all_centers[0] is contained by the STRree elements 5,0 and 322.
# The matching indices 0-0 indicate that the point is contained by the geometry itself.
results = stree.query(all_centers, predicate="intersects")


# instantiate new column, which is filled via the result of the STRtree
df_admin.upper_admin_area = None
logging.info("Annotating admin area data with parent admin area")
for row in tqdm.tqdm(df_admin.itertuples(), total=len(df_admin)):
    index = row.Index
    mask = results[0] == index
    result = results[1][mask]
    # point is contained in itself -> remove it
    result = result[result != index]
    # filter out admin_areas with lower admin_levels than itself
    result = list(filter(lambda x: df_admin.loc[x, "admin_level"] < row.admin_level, result))
    # sort for the highest admin_area, e.g. the direct parent of the inside element
    result = list(sorted(result, key=lambda x: -int(df_admin.loc[x, "admin_level"])))
    name = df_admin.loc[index, "name"]
    upper_admin_area = None
    if result:
        upper_admin_area = result[0]
    df_admin.loc[row.Index, "upper_admin_area"] = upper_admin_area

df_admin.to_csv("admin_areas_complete.csv", index=False)
df_admin = df_admin.loc[:, ["upper_admin_area", "osm_id", "name", "admin_level"]]
df_admin["id"] = df_admin.index
df_admin.to_csv("admin_areas.csv", index=False)

logging.info("Finding admin areas which contain each bus stop using STRtree")
results_bus_stops = stree.query(df_bus.geometry, predicate="intersects")

df_bus.admin_area = None
df_bus.geom_x = None
df_bus.geom_y = None
df_bus.geom_z = None
logging.info("Annotating bus stop data with parent admin areas")
left = 0
for row in tqdm.tqdm(df_bus.itertuples(), total=len(df_bus)):
    # get the admin areas of this index. Equivalent to:
    # mask = results_bus_stops[0] == row.index
    # but much more performant, since sorting is leveraged
    mask = gpd.np.zeros(len(df_bus)).astype(bool)
    right_offset = results_bus_stops[0][left:].searchsorted(row.Index, side="right")
    mask[left : left + right_offset] = True
    left = left + right_offset
    result = results_bus_stops[1][mask]
    # sort for the highest admin_area, e.g. the direct parent of the inside element
    result = list(sorted(result, key=lambda x: -int(df_admin.loc[x, "admin_level"])))
    upper_admin_area = None
    if result:
        upper_admin_area = result[0]
    geom_x = row.geometry.x
    geom_y = row.geometry.y
    geom_z = 0
    df_bus.loc[row.Index, "admin_area"] = upper_admin_area
    df_bus.loc[row.Index, "geom_x"] = geom_x
    df_bus.loc[row.Index, "geom_y"] = geom_y
    df_bus.loc[row.Index, "geom_z"] = geom_z


df_bus.to_csv("bus_stops_complete.csv", index=False)
df_bus["id"] = df_bus.index
df_bus = df_bus.loc[:, ["id", "name", "osm_id", "admin_area", "geom_x", "geom_y", "geom_z"]]
df_bus.to_csv("bus_stops.csv", index=False)
