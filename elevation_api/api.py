import math
import traceback

import numpy as np
import pandas as pd
import pyproj
from typing import List, Iterable
from pathlib import Path
import requests
import zipfile
from io import BytesIO

from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import cKDTree


def get_sources():
    print("First time getting sources for elevation data")
    # Get the directory of the script
    # Path to your PRJ file
    local_path = Path(__file__).parent
    # Create the full path for the extraction location
    extract_path = local_path / Path("static/elevation_api")
    # Ensure the extraction directory exists
    extract_path.mkdir(exist_ok=True)

    # Download the file
    url = "https://daten.gdz.bkg.bund.de/produkte/dgm/dgm200/aktuell/dgm200.utm32s.xyzascii.zip"
    response = requests.get(url)
    response.raise_for_status()  # Check that the request was successful

    # Unzip the file
    with zipfile.ZipFile(BytesIO(response.content)) as thezip:

        for source_file in ["dgm200_utm32s.prj", "dgm200_utm32s.xyz"]:
            # Check if the target file is in the zip
            source_path = "dgm200.utm32s.xyzascii/dgm200/" + source_file
            if source_path in thezip.namelist():
                # Extract the specific file
                with thezip.open(source_path) as source, open(
                    extract_path / source_file, "wb"
                ) as target:
                    target.write(source.read())
                print(f"File '{source_file}' has been moved to: {extract_path}")
        else:
            print(f"File '{source_file}' not found in the zip archive.")

    print(f"Files have been extracted to: {extract_path}")

    prj_file_path = local_path / Path("static/elevation_api/dgm200_utm32s.prj")
    data_file = local_path / Path("static/elevation_api/dgm200_utm32s.xyz")
    global TRANSFORMER
    TRANSFORMER = get_transformer(prj_file_path)
    global df
    df = pd.read_csv(data_file, sep=" ", header=None)
    df.columns = ["x", "y", "z"]
    data = np.array((df.x, df.y)).T
    global CKDTREE
    CKDTREE = cKDTree(data)


def get_elevation(lats: List[float], lons: List[float]) -> dict:
    if TRANSFORMER is None:
        get_sources()
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


def get_transformer(prj_file_path):
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


TRANSFORMER = None
CKDTREE = None
df = None
