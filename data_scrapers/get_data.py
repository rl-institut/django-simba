"""Script to generate bus_stop data via an osm.pbf file. Meant to be run once locally to generate
 a file to import into the database of webus. First use osmium-tool to generate
filtered files of admin areas and bus_stops in the terminal via:

osmium tags-filter "germany-latest.osm.pbf" n/highway=bus_stop -o bus_stops.osm.xml
osmium tags-filter "germany-latest.osm.pbf"  wr/boundary=administrative -o admin.osm.xml

osmium-tool can be downloaded from:
https://osmcode.org/osmium-tool/

These xml files are readable by geopandas and analyzed to generate a hierarchy of admin areas.
The hierarchy lets each admin area know which next higher level admin area they are contained in.

Bus stops are annotated with the lowest admin area they are contained in.
This data can be used to properly locate bus_stops with non unique names, but with extra data,
e.g. while the Bus Stop "Mitte" is a bus stop name of multiple bus stops in germany, using
"Berlin, Mitte" might be able to locate this bus stop, since there is a unique bus stop in Berlin,
called "Mitte"."""

import geopandas as gpd
from shapely import STRtree
import tqdm

layers = ["points", "lines", "multilinestrings", "multipolygons", "other_relations", "polygons"]
print("Reading osm files")
df_bus = gpd.read_file("bus_stops.osm.xml", layer=layers[0])
df_admin = gpd.read_file("admin.osm.xml", layer=layers[3])


df_admin.loc[df_admin.admin_level.isna(), "admin_level"] = 0
df_admin.admin_level = df_admin.admin_level.astype(int)

# We only care about Bundesländer and bigger up to level 9 which is close to Gemeinden or Bezirke
df_admin = df_admin[df_admin.admin_level >= 4]
df_admin = df_admin[df_admin.admin_level <= 9]
df_admin.reset_index(drop=True, inplace=True)

print("Finding admin areas contained within each other using STRtree")
# Find the admin areas which contain the centroid of each admin area
all_centers = [a.centroid for a in df_admin.geometry]
stree = STRtree(df_admin.geometry)
results = stree.query(all_centers, predicate="intersects")
#  Return the integer indices of all combinations of each input geometry and tree geometries where
#  the bounding box of each input geometry intersects the bounding box of a tree geometry. This
# does not gurantee that the point is contained in the geometry.
# Therefore the predicate kwarg is given. predicate="intersects" further filters those geometries
# that meet the predicate("interesect") when comparing the input geometry to the tree geometry.
# This differs from comparison to the bounding box and guarantees true containment.

# instantiate new column, which is filled via the result of the STRtree
df_admin.upper_admin_area = None
print("Annotating admin area data with parent admin area")
for row in tqdm.tqdm(df_admin.itertuples(), total=len(df_admin)):
    index = row.Index
    mask = results[0] == index
    result = results[1][mask]
    # centroid is contained in itself -> remove it
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

print("Finding admin areas which contain each bus stop using STRtree")
results_bus_stops = stree.query(df_bus.geometry, predicate="intersects")

df_bus.admin_area = None
df_bus.geom_x = None
df_bus.geom_y = None
df_bus.geom_z = None
print("Annotating bus stop data with parent admin areas")
left = 0
for row in tqdm.tqdm(df_bus.itertuples(), total=len(df_bus)):
    # get the admin areas of this index. equivalent to
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
