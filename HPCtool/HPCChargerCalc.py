import geopandas as gpd
import pandas as pd
import numpy as np

import pyproj

from .models import BusOutline, Tree, Flurstueck, Cyclepath, ResidentialArea
from django.contrib.gis.geos import MultiPolygon, GEOSGeometry
from django.contrib.gis.geos import Polygon as djangoPolygon
from django.contrib.gis.geos import Point as djangoPoint

from shapely.geometry import Point, Polygon, LineString
import shapely

import osmnx as ox
from requests import Request

import math
import requests
import networkx as nx
from sklearn.metrics.pairwise import euclidean_distances

# Bounding box or "search radius" that will be added around a busstop's location. If selected large, miltiple parcels (Flurstücke) might be taken into consideration
bb_size = 0  # meter

### Bus Parameters ##
bus_laenge = 18
bus_breite = 3
# Pantograph location, distance from front of the bus (centered laterally)
panto_loc = 0.25 * bus_laenge
#####################

park_abstand = 5

dist_tree_red = 5
dist_tree_green = 10

dist_wohn_red = 30
dist_wohn_green = 60

dist_radweg_red = 5
dist_radweg_green = 10

Layers = {
    "Bäume": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_wfs_baumbestand""",
        "typeNames": "fis:s_wfs_baumbestand"
    },
    "ATKIS Wohnbaufläche offen": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_atkis_AX_wohnbauflaeche_offen_f""",
        "typeNames": "fis:s_atkis_AX_wohnbauflaeche_offen_f"
    },
    "Bauwerke (Linien)": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_wfs_alkis_bauwerkelinien""",
        "typeNames": "fis:s_wfs_alkis_bauwerkelinien"
    },
    "Tatsächliche Nutzung": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_wfs_alkis_tatsaechlichenutzungflaechen""",
        "typeNames": "fis:s_wfs_alkis_tatsaechlichenutzungflaechen"
    },
    "Denkmalschutzrecht": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_atkis_AX_denkmalschutzrecht_f""",
        "typeNames": "fis:s_atkis_AX_denkmalschutzrecht_f"
    },
    "Denkmalschutzrecht2": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_atkis_AX_denkmalschutzrecht_p""",
        "typeNames": "fis:s_atkis_AX_denkmalschutzrecht_p"
    },
    "Radweg": {
        "url": """https://fbinter.stadt-berlin.de/fb/wfs/data/senstadt/s_Radweg""",
        "typeNames": "fis:s_Radweg"
    },
}


def extractWohngebiet(local_layerdict):
    try:
        wohngeb_gdf = local_layerdict["Tatsächliche Nutzung"]
        rel = wohngeb_gdf[wohngeb_gdf["bezeich"] == "AX_Wohnbauflaeche"]
    except:
        rel = gpd.GeoDataFrame()
    return rel


criteria = {
    "Bäume": {
        "kind": "point",
        "layer_name": "Bäume",
        "dist_red": dist_tree_red,
        "dist_green": dist_tree_green
    },
    "Wohngebäude": {
        "kind": "poly",
        "layer_name": extractWohngebiet,
        "dist_red": dist_wohn_red,
        "dist_green": dist_wohn_green
    },
    "Radweg": {
        "kind": "point",
        "layer_name": "Radweg",
        "dist_red": dist_radweg_red,
        "dist_green": dist_radweg_green
    },

}


def calculate_HPC(poly_list):
    polygon_geom = Polygon(poly_list)
    alkis = gpd.GeoDataFrame(index=[0], crs=4326, geometry=[polygon_geom])

    listbusses, listtrees = [], []

    layerdict = {}
    local_layerdict = {}
    for layer in Layers:
        layerdict[layer] = gpd.GeoDataFrame()

    straßen = getStreetsfromOSM(alkis.to_crs("WGS84").loc[0, 'geometry'], tags={"highway": True})

    flurstück_bounds = (
        alkis.geometry.bounds['minx'].min(), alkis.geometry.bounds['miny'].min(), alkis.geometry.bounds['maxx'].max(),
        alkis.geometry.bounds['maxy'].max()
    )

    alkis_4326 = alkis.to_crs(4326)
    cdr = list(zip(*alkis_4326.geometry.values[0].exterior.coords.xy))
    p2 = djangoPolygon(cdr)
    mp = MultiPolygon(p2)
    flurstueck = Flurstueck.objects.create(geom=mp, name="Herzallee", scenario_ID="neu")

    gdf = straßen.to_crs(25833)

    n2g = makeConnections(gdf)

    for layer in Layers:
        success, lgdf = generalizedLayerLoader(Layers[layer]['url'], Layers[layer]['typeNames'], flurstück_bounds)
        if success > 0:
            layerdict[layer] = pd.concat([layerdict[layer], lgdf], axis=0, ignore_index=True)
            local_layerdict[layer] = pd.concat([layerdict[layer], lgdf], axis=0, ignore_index=True)
            print(layer)
            if layer == "Bäume":
                gdf_4326 = lgdf.to_crs(4326)
                for row in gdf_4326.geometry:
                    p3 = djangoPoint(*list(zip(*row.xy)))
                    tree = Tree.objects.create(geom=p3, name="Baum", scenario_ID="neu")
                    listtrees.append(tree)

            if layer == "Radweg":
                gdf_4326 = lgdf.to_crs(4326)
                for multipoly in gdf_4326.geometry.values:
                    django_multipolygon = GEOSGeometry(multipoly.wkt)
                    Cyclepath.objects.create(geom=django_multipolygon, name="Radweg", scenario_ID="neu")

            if layer == "Tatsächliche Nutzung":
                WohnGDF = extractWohngebiet(lgdf)
                if not WohnGDF.empty:
                    gdf_4326 = WohnGDF.to_crs(4326)
                    for multipoly in gdf_4326.geometry.values:
                        django_multipolygon = GEOSGeometry(multipoly.wkt)
                        ResidentialArea.objects.create(geom=django_multipolygon, name="Herzallee", scenario_ID="neu")
                else:
                    print("KEIN WOHNGEBIET")

    charger_good, charger_medium, charger_bad = 0, 0, 0

    G = makeGraph(n2g)
    visited_nodes = set()

    for index, row in n2g[n2g.color == 'yellow'].iterrows():

        red_segments, visited_nodes = findConnectedSegments(G, index, visited_nodes)

        for segment in red_segments:
            node_list = []
            for id in segment['nodes']:
                node_list.append((n2g.loc[id]['x'], n2g.loc[id]['y']))
            ids_list = segment['nodes']
            curvatures = calculate_curvature(node_list)

            angles = calculate_path_angles(node_list)
            angles.insert(0, 0)
            angles.append(0)

            threshold = 0.002

            # initialize list of coordinate sublists
            coord_sublists = [[]]
            sublist_elements = 0
            lengths = [0]

            laengen = []

            segment['lengths'].append(0)

            for idx, node in enumerate(node_list):

                coord_sublists[sublist_elements].append(node_list[idx])

                if np.abs(angles[idx] - 180) > 5 and np.abs(angles[idx]) > 5:
                    if len(coord_sublists[sublist_elements]) == 1:
                        coord_sublists[sublist_elements].append(node_list[idx + 1])

                    else:
                        pass
                    lengths.append(0)
                    coord_sublists.append([])
                    sublist_elements += 1
                    coord_sublists[sublist_elements].append(node_list[idx])

                lengths[sublist_elements] += segment['lengths'][idx]

            for idx, seg in enumerate(coord_sublists):
                if len(seg) > 1:

                    buspolylist, pantographs_list = place_bus_along(seg)

                    for (polygon, pnt) in zip(buspolylist, pantographs_list):

                        #to remove busses placed outside of the designated area
                        # Create a GeoDataFrame with the point
                        point_gdf = gpd.GeoDataFrame(geometry=[Point(pnt)])

                        # Perform spatial join to check if point is inside polygon
                        intersection = gpd.sjoin(point_gdf, alkis, how="inner", op="within")
                        print(intersection)
                        if not intersection.empty:

                            c_good, c_mid, c_bad = placeColoredPanthograph(pnt, local_layerdict, criteria)

                            if c_good > 0:
                                new_instance = BusOutline.objects.create(geom=polygon, name="buzz", scenario_ID="neu", quality=2)
                            elif c_mid > 0:
                                new_instance = BusOutline.objects.create(geom=polygon, name="buzz", scenario_ID="neu", quality=1)
                            else:
                                new_instance = BusOutline.objects.create(geom=polygon, name="buzz", scenario_ID="neu", quality=0)

                            charger_good += c_good
                            charger_medium += c_mid
                            charger_bad += c_bad
                            listbusses.append(new_instance)
    return str(charger_bad) + " " + str(charger_medium) + " " + str(charger_good)


def generalizedLayerLoader(wfs_url, type_names, sbbox, version='2.0.0', retries=3, timeout=30):
    """
    Retrieves data from a WFS service using the specified URL and parameters.

    TODO: sBBOX is in 25833??

    Parameters:
    wfs_url (str): The URL of the WFS service.
    type_names (str): The name of the layer to query.
    version (str): The version of the WFS protocol to use (default is '2.0.0').
    retries (int): The number of times to retry the request if it fails (default is 3).
    timeout (int): The number of seconds to wait for a response before timing out (default is 30).

    Returns:
            Tuple, signifying the success of the request and the returned Geodataframe
    """

    # Define the source and target coordinate systems
    source_crs = 'EPSG:4326'
    target_crs = 'EPSG:25833'

    points_wgs84 = [
        Point(sbbox[0], sbbox[1]),
        Point(sbbox[2], sbbox[3]),
    ]

    # Create a transformer
    transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy=True)

    # Transform the coordinates using the transformer
    sbbox_25833 = [transformer.transform(point.x, point.y) for point in points_wgs84]

    gdf = gpd.GeoDataFrame()
    params = {
        "service": "WFS",
        "version": version,
        "request": "GetFeature",
        "typeNames": type_names,
        "bbox": f'{sbbox_25833[0][0]-bb_size},{sbbox_25833[0][1]-bb_size},{sbbox_25833[1][0]+bb_size},{sbbox_25833[1][1]+bb_size}'
    }

    wfs_request_url = Request('GET', wfs_url, params=params).prepare().url

    for i in range(retries):
        try:
            response = requests.get(wfs_request_url, timeout=timeout)
            if response.status_code == 200:
                try:
                    gdf = gpd.read_file(wfs_request_url)
                    gdf.crs = 25833
                    return 1, gdf
                except:
                    return 0, gdf
        except requests.exceptions.RequestException as e:
            print("Error: Request failed with exception", e)
            return 0, gdf

    print("Error: Request failed after", retries, "retries")
    return 0, gdf


def calculate_curvature(nodes):
    """
    Calculate the curvature of a street given a list of nodes.

    Parameters:
    - nodes (list): A list of tuples representing the x and y coordinates of the nodes.

    Returns:
    - curvature (numpy.ndarray): An array containing the curvature values for each node.

    """

    # Convert the list of nodes to a numpy array of coordinates
    coords = np.array([(node[0], node[1]) for node in nodes])
    x, y = coords.T

    # Calculate the derivatives of x and y
    dx_dt = np.gradient(x)
    dy_dt = np.gradient(y)

    # Calculate the second derivatives of x and y
    d2x_dt2 = np.gradient(dx_dt)
    d2y_dt2 = np.gradient(dy_dt)

    # Calculate the numerator and denominator of the curvature formula
    numerator = (dx_dt * d2y_dt2) - (dy_dt * d2x_dt2)
    denominator = (dx_dt ** 2 + dy_dt ** 2) ** 1.5

    # Calculate the curvature using the formula: R = (1 + (dy/dx)^2)^1.5 / |d2y/dx2|
    curvature = numerator / denominator

    # Return the curvature values
    return curvature


def subsample_line(start_point, end_point, step_size):
    """
    Subsamples a line segment defined by the start and end points.

    Parameters:
    - start_point (shapely.geometry.Point): The start point of the line segment.
    - end_point (shapely.geometry.Point): The end point of the line segment.
    - step_size (float): The desired distance between the subsampled points.

    Returns:
    - new_points (list): A list of subsampled points along the line segment.

    """

    # Create a LineString object from the start and end points
    line = LineString([start_point, end_point])

    # Calculate the length of the line
    line_length = line.length

    # Calculate the number of steps required
    num_steps = int(
        line_length / step_size)  # has almost guaranteed error, since distance is a root and most probably irrational

    # Create an empty list to store the new points
    new_points = []

    # Iterate over the number of steps and interpolate points along the line
    for i in range(num_steps + 1):  # Include the end point in the iteration
        # Calculate the distance along the line for the current step
        distance = i * step_size

        # Interpolate a point along the line at the given distance
        new_point = line.interpolate(distance)

        # Add the new point to the list
        new_points.append(new_point)

    return new_points


def place_bus_along(nodes):
    """
    Place buses along a street segment defined by a list of nodes.

    Parameters:
    - nodes (list): A list of Point objects representing the nodes of the street segment.
    - buslayer (folium.Map): The map layer to add the bus polygons to.

    Returns:
    - pantographs (list): A list of tuples containing the x and y coordinates of the pantograph positions.

    """

    subs_node_list = []

    # Create Bus outlines
    upper_left = (-bus_laenge / 2, bus_breite / 2)
    upper_right = (bus_laenge / 2, bus_breite / 2)
    lower_right = (bus_laenge / 2, -bus_breite / 2)
    lower_left = (-bus_laenge / 2, -bus_breite / 2)
    raw_buspoly = [upper_left, upper_right, lower_right, lower_left]
    raw_buspoly = Polygon(raw_buspoly)
    panto = (-bus_laenge / 2 + panto_loc, 0)
    panto = Point(panto)

    for idx, _ in enumerate(nodes[:-1]):
        step_size = 0.2  # 20 cm
        subs_node_list.extend(subsample_line(nodes[idx], nodes[idx + 1], step_size))

    start = 0
    end = 1
    pantographs = []
    buspolygonlist = []

    for idx, _ in enumerate(subs_node_list[:-1]):

        ang = angle_between((subs_node_list[end].x, subs_node_list[end].y),
                            (subs_node_list[start].x, subs_node_list[start].y),
                            (subs_node_list[end].x, subs_node_list[start].y))

        if ang < 0:
            ang += 180

        laenge = (end - start) * step_size

        for bus in range(0, int(laenge // (bus_laenge + park_abstand))):
            # bus = n-ter bus entlang des segments

            # rotate the busoutline and the Pantograph around the origin
            raw_buspoly_rot = shapely.affinity.rotate(raw_buspoly, -ang, origin=(0, 0))
            panto_rot = shapely.affinity.rotate(panto, -ang, origin=(0, 0))

            # Turn the rotated bus into a Geoseries
            xx, yy = raw_buspoly_rot.exterior.coords.xy
            bus_poly = gpd.GeoSeries(
                [Point(xx[0], yy[0]), Point(xx[1], yy[1]), Point(xx[2], yy[2]), Point(xx[3], yy[3])], crs=25833)
            panto_rot = gpd.GeoDataFrame(geometry=[panto_rot], crs=25833)

            # Move the rotated Busoutline and panthograph along the segment
            # Calculate the magnitude of the vector
            mag = math.sqrt((subs_node_list[end].x - subs_node_list[start].x) ** 2 + (
                    subs_node_list[end].y - subs_node_list[start].y) ** 2)

            # Calculate the unit vector in the direction of the vector
            unit_vec = ((subs_node_list[end].x - subs_node_list[start].x) / mag,
                        (subs_node_list[end].y - subs_node_list[start].y) / mag)

            # translate the rotated Bus to the beginning of the segment plus 0.5*the bus length (so the REAR of the Bus will be at the beginning of the segment) and additional half parking distance
            new_pos = (subs_node_list[start].x + (0.5 * bus_laenge + 0.5 * park_abstand) * unit_vec[0],
                       subs_node_list[start].y + (0.5 * bus_laenge + 0.5 * park_abstand) * unit_vec[1])

            bus_poly = bus_poly.translate(new_pos[0], new_pos[1])
            panto_rot_trans = panto_rot.translate(new_pos[0], new_pos[1])

            faktor = 1 / int(laenge // (bus_laenge + 0.5 * park_abstand))
            # faktor ist der Interpolationsfaktor entlang des Segments
            xxx = bus * faktor * (subs_node_list[end].x - subs_node_list[start].x)
            yyy = bus * faktor * (subs_node_list[end].y - subs_node_list[start].y)

            # translate along the street segment
            bus_poly = bus_poly.translate(xxx, yyy)
            panto_rot_trans2 = panto_rot_trans.translate(xxx, yyy)

            bus_poly = bus_poly.to_crs(4326)
            panto_rot_trans2 = panto_rot_trans2.to_crs(4326)

            bus_poly = [(float(bus_poly[0].x), float(bus_poly[0].y)),
                        (float(bus_poly[1].x), float(bus_poly[1].y)),
                        (float(bus_poly[2].x), float(bus_poly[2].y)),
                        (float(bus_poly[3].x), float(bus_poly[3].y)),
                        (float(bus_poly[0].x), float(bus_poly[0].y))]

            print(bus_poly)

            p1 = djangoPolygon(bus_poly)
            # print(p1)

            # p1 = Polygon(((21, 40), (0, 50), (50, 50), (21, 40)))
            mp = MultiPolygon(p1)
            # BusOutline.objects.create(geom=mp, name="buzz", scenario="neu", quality=0)

            buspolygonlist.append(mp)

            pantographs.append((panto_rot_trans2.geometry.x, panto_rot_trans2.geometry.y))

        if int(laenge // (bus_laenge + park_abstand)) >= 1:
            start = end

        end = end + 1

    return buspolygonlist, pantographs


def getStreetsfromOSM(geometry, tags):
    straßen = ox.geometries_from_polygon(geometry, tags=tags)
    straßen = straßen[straßen['highway'] != 'footway']
    straßen = straßen[straßen['highway'] != 'track']
    straßen = straßen[straßen['highway'] != 'pedestrian']
    straßen = straßen[straßen['highway'] != 'cycleway']
    straßen = straßen[straßen['highway'] != 'path']
    straßen = straßen[straßen['highway'] != 'corridor']
    straßen = straßen[straßen['highway'] != 'tunnel']
    straßen = straßen[straßen['highway'] != 'steps']
    straßen = straßen[straßen['highway'] != 'bus_stop']
    straßen = straßen[straßen['highway'] != 'crossing']
    straßen = straßen[straßen['highway'] != 'elevator']
    straßen = straßen[straßen['highway'] != 'street_lamp']
    straßen = straßen[straßen['highway'] != 'traffic_signals']

    # TODO: However it is done sucks... inclusive or exclusive :/
    # 'primary', 'residential', 'secondary', 'service'

    return straßen


def makeConnections(gdf):
    nodes = []

    nodes2geo = pd.DataFrame(columns=['ID', 'x', 'y', 'count', 'links_to', 'links_from', 'color'])

    # iterate over each row in the geodataframe
    for idx, row in gdf.iterrows():
        if isinstance(row.geometry, LineString):
            nodes.extend(row.nodes[:])
            # print(row.geometry.coords.xy, row.nodes)

            for idx, node in enumerate(row.nodes):
                if not (nodes2geo['ID'] == node).any():
                    # Add new element to the dataframe
                    x, y = row.geometry.coords.xy[:]
                    new_row = {'ID': node, 'x': x[idx], 'y': y[idx], 'count': -1, 'links_to': [[]], 'links_from': [[]],
                               'color': ''}
                    nodes2geo = pd.concat([nodes2geo, pd.DataFrame(new_row, index=[0])], ignore_index=True)
                if idx < len(row.nodes) - 1:
                    r = nodes2geo.loc[nodes2geo['ID'] == node]
                    if not r.empty:
                        nodes2geo.loc[nodes2geo['ID'] == node, 'links_to'].iloc[0].append(row.nodes[idx + 1])

    links_from = []

    # loop through each row of the DataFrame
    for index, row in nodes2geo.iterrows():

        # create an empty list for links_from for the current row
        current_links_from = []

        # loop through each row of the DataFrame again to find matching links_to
        for i, r in nodes2geo.iterrows():

            # check if the current ID is in the links_to of the current row
            if row['ID'] in r['links_to']:
                # if yes, add the current row's ID to the links_from of the current row
                current_links_from.append(r['ID'])

        # append the links_from list for the current row to the overall links_from list
        links_from.append(current_links_from)

    # add the links_from column to the DataFrame
    nodes2geo['links_from'] = links_from

    Stuff = np.asarray(nodes)

    unique, counts = np.unique(Stuff, return_counts=True)

    dictt = dict(zip(unique, counts))

    for node_id, count in dictt.items():
        nodes2geo.loc[nodes2geo['ID'] == node_id, 'count'] = count

    for index, row in nodes2geo.iterrows():

        count_now = len(row['links_from']) + len(row['links_to'])
        nodes2geo.loc[index, 'count'] = count_now

        if count_now == 2:
            nodes2geo.loc[index, 'color'] = 'red'
        else:
            nodes2geo.loc[index, 'color'] = 'blue'

        if nodes2geo.loc[index, 'links_to'] == []:
            nodes2geo.loc[index, 'color'] = 'green'

        if nodes2geo.loc[index, 'links_from'] == []:
            nodes2geo.loc[index, 'color'] = 'yellow'

    nodes2geo["indexID"] = nodes2geo["ID"]
    nodes2geo = nodes2geo.set_index('indexID')

    return nodes2geo


def makeGraph(nodes2geo):
    df = nodes2geo

    # create an empty graph
    G = nx.DiGraph()

    # add nodes to the graph
    for _, row in df.iterrows():
        G.add_node(row['ID'], pos=(row['x'], row['y']))

    # add edges to the graph
    for _, row in df.iterrows():
        for neighbor in row['links_to']:
            if nodes2geo.loc[neighbor]['count'] == 2 or row['count'] == 2:
                w = 2
                colo = 'red'
            else:
                w = 0
                colo = 'blue'
            G.add_edge(row['ID'], neighbor, weight=w, color=colo, length=
            euclidean_distances([(nodes2geo.loc[neighbor]['x'], nodes2geo.loc[neighbor]['y']),
                                 (row['x'], row['y'])])[0][1])

    return G


# DFS traversal function to sum up the length of disjoint red segments
def dfs_red_segments(graph, node, visited, red_segments):
    visited.add(node)
    for neighbor in graph.neighbors(node):
        edge_data = graph.get_edge_data(node, neighbor)
        color = edge_data['color']
        weight = edge_data['length']
        if color == 'red':
            if not red_segments or red_segments[-1]['end'] != node:
                # create a new red segment if there isn't one or if the last one doesn't end at this node
                red_segments.append({'start': node, 'end': neighbor, 'length_total': weight, 'nodes': [node, neighbor],
                                     'lengths': [weight]})
            else:
                # add to the current red segment if it continues from the previous node
                red_segments[-1]['end'] = neighbor
                red_segments[-1]['length_total'] += weight
                red_segments[-1]['nodes'].append(neighbor)
                red_segments[-1]['lengths'].append(weight)
        if neighbor not in visited:
            dfs_red_segments(graph, neighbor, visited, red_segments)
    return red_segments


def findConnectedSegments(G, start_node, visited=set()):
    # call the DFS function starting from the specfied node
    red_segments = []

    for neighbor in G.neighbors(start_node):
        edge_data = G.get_edge_data(start_node, neighbor)

        color = edge_data['color']
        weight = edge_data['length']
        if color == 'red' and (start_node, neighbor) not in visited:
            red_segments.append(
                {'start': start_node, 'end': neighbor, 'length_total': weight, 'nodes': [start_node, neighbor],
                 'lengths': [weight]})
            red_segments = dfs_red_segments(G, neighbor, visited, red_segments)

    return red_segments, visited


def angle_between(a, b, c):
    """Counterclockwise angle in degrees by turning from a to c around b"""

    a = np.asarray(a)
    b = np.asarray(b)
    c = np.asarray(c)
    ang = math.degrees(
        math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(a[1] - b[1], a[0] - b[0]))

    return ang


def calculate_path_angles(coords_list):
    """
    Calculates the angle between the incoming and outgoing edges at each node
    in a path given a list of (y,x) tuples representing the coordinates of the nodes
    and their incoming and outgoing edges.

    Args:
    coords_list: a list of (y,x) tuples

    Returns:
    A list of angles in degrees between the incoming and outgoing edges at each node
    in the path.
    """
    angles = []
    for i in range(1, len(coords_list) - 1):
        angle = angle_between(coords_list[i - 1], coords_list[i], coords_list[i + 1])

        angles.append(angle)
    return angles


def distance(pt, points_df, searchradius=30):
    # Define a maximum distance to limit the search
    max_distance = searchradius

    pt = gpd.GeoDataFrame(geometry=[pt], crs=4326).to_crs(25833)

    pt = Point(pt.geometry.x, pt.geometry.y)

    points_df = points_df.to_crs(25833)

    # Build a spatial index for the points
    sindex = points_df.sindex

    # Query the spatial index for nearby points
    possible_matches_index = list(sindex.intersection(pt.buffer(max_distance).bounds))

    if len(possible_matches_index) > 0:
        # Filter the nearby points based on their actual distance to the point of interest
        possible_matches = points_df.iloc[possible_matches_index]
        distances = possible_matches.distance(pt)
        min_distance = distances.min()
    else:
        min_distance = max_distance + 1

    return min_distance


def distance_poly(pt, polygon, default=-1):
    pt = gpd.GeoDataFrame(geometry=[pt], crs=4326).to_crs(25833)

    pt = Point(pt.geometry.x, pt.geometry.y)

    distance = default

    if len(polygon.geometry.is_empty) > 0:

        from shapely.ops import nearest_points

        poly = polygon
        point = pt

        # The points are returned in the same order as the input geometries:
        p1, _ = nearest_points(poly.geometry, point)

        ll = []

        for poi in p1:
            ll.append(poi.distance(pt))

        distance = min(ll)

    return distance


def placeColoredPanthograph(pnt, local_layerdict, criteria):
    col = "green"  # TODO: enum?

    charger_good, charger_medium, charger_bad = 0, 0, 0

    full_str = ""

    for crit in criteria:

        if criteria[crit]["kind"].lower() == "point":
            if crit in local_layerdict.keys():
                print(crit)
                print(criteria)
                print(local_layerdict.keys())
                dist = distance(Point(pnt), local_layerdict[criteria[crit]["layer_name"]],
                                criteria[crit]["dist_green"] * 1.5)
            else:
                dist = criteria[crit]["dist_green"] * 1.5

        elif isinstance(criteria[crit]["layer_name"], str):
            dist = distance_poly(Point(pnt), local_layerdict[criteria[crit]["layer_name"]],
                                 criteria[crit]["dist_green"] * 2)
        else:
            rel = criteria[crit]["layer_name"](local_layerdict)
            dist = distance_poly(Point(pnt), rel, criteria[crit]["dist_green"] * 2)

        if dist >= criteria[crit]['dist_green']:
            color = "green"
        elif dist >= criteria[crit]['dist_red']:
            color = "orange"
        else:
            color = "red"

        if col == "green" and color != "green":
            col = color
        elif color == "orange" and color != "green":
            col == color

        full_str += "Abstand " + crit + ": " + str(dist) + "\n"

    if col == "green":
        charger_good += 1
    elif col == "orange":
        charger_medium += 1
    else:
        charger_bad += 1

    return charger_good, charger_medium, charger_bad
