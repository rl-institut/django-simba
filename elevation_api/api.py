import math
import traceback

import numpy as np
import pandas as pd
import pyproj
from typing import List, Iterable
from pathlib import Path


from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import cKDTree

# Path to your PRJ file
local_path = Path(__file__).parent
prj_file_path = local_path / Path("static/elevation_api/dgm200_utm32s.prj")
data_file = local_path / Path("static/elevation_api/dgm200_utm32s.xyz")


def get_elevation(lats: List[float], lons: List[float]) -> dict:
    transformer = TRANSFORMER
    if TRANSFORMER is None:
        raise Exception("Transformer not available")
    if not isinstance(lats, Iterable):
        assert not isinstance(lons, list)
        lats = [lats]
        lons = [lons]

    xs, ys = transformer.transform(lons, lats)
    # find the 4 closest neighbors for interpolation eg=k=[1,2,3,4]
    distance, indicies = CKDTREE.query((list(zip(xs, ys))), k=[1, 2, 3, 4])
    if not len(indicies) % 4 != 0:
        print(xs, ys)
    indicies = np.unique(np.ravel(indicies))
    relevant_df = df.loc[indicies]
    interp = LinearNDInterpolator(list(zip(relevant_df.x, relevant_df.y)), relevant_df.z)
    vec_interp = np.vectorize(interp)
    elevations = vec_interp(xs, ys)
    if any((math.isnan(ele) for ele in elevations)):
        raise Exception(
            "Elevation is only available for Germany: Roughly: Latitude[55,47], Longitude[5.8,15]"
        )
    return elevations


def get_transformer():
    # Read PRJ file and create coordinate transformation
    try:
        with open(prj_file_path, "r") as prj_file:
            prj_contents = prj_file.read()
            src_crs = pyproj.CRS.from_string(prj_contents)
        dst_crs = pyproj.CRS.from_epsg(4326)  # WGS84 CRS (latitude and longitude)
        return pyproj.Transformer.from_crs(dst_crs, src_crs, always_xy=True)
    except Exception:
        traceback.print_exc()
        return None


TRANSFORMER = get_transformer()
df = pd.read_csv(data_file, sep=" ", header=None)
df.columns = ["x", "y", "z"]
data = np.array((df.x, df.y)).T
CKDTREE = cKDTree(data)
