import logging
import math
import traceback

import numpy as np
import pandas as pd
import pyproj
from typing import List, Iterable, Any
from pathlib import Path
import requests
import zipfile
from io import BytesIO

from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import cKDTree, QhullError

logger = logging.getLogger("custom")


class NanValueException(Exception):
    pass


def get_and_set_sources():
    """Download elevation data files"""
    # Get the directory of the script
    # Path to your PRJ file
    local_path = Path(__file__).parent
    # Create the full path for the extraction location
    extract_path = local_path / Path("static/elevation_api")
    # Ensure the extraction directory exists
    extract_path.mkdir(exist_ok=True)
    needed_files = ["dgm200_utm32s.prj", "dgm200_utm32s.xyz"]

    if all([(extract_path / nfile).is_file() for nfile in needed_files]):
        logger.info("Elevation files exist and downloading is skipped.")
    else:
        # Download the files
        url = "https://daten.gdz.bkg.bund.de/produkte/dgm/dgm200/aktuell/dgm200.utm32s.xyzascii.zip"
        logger.info(f"First time getting sources for elevation data from {url}")
        try:
            response = requests.get(url)
            response.raise_for_status()  # Check that the request was successful

            # Unzip the file
            with zipfile.ZipFile(BytesIO(response.content)) as thezip:

                for source_file in needed_files:
                    # Check if the target file is in the zip
                    source_path = "dgm200.utm32s.xyzascii/dgm200/" + source_file
                    # Extract the specific file
                    with thezip.open(source_path) as source, open(
                        extract_path / source_file, "wb"
                    ) as target:
                        target.write(source.read())
                    logger.info(f"File '{source_file}' has been moved to: {extract_path}")
        except Exception as e:
            logger.error("Downloading and moving elevation data failed")
            logger.error(traceback.format_exc())
            raise e

    prj_file_path = extract_path / "dgm200_utm32s.prj"
    data_file = extract_path / "dgm200_utm32s.xyz"
    global DF
    DF = pd.read_csv(data_file, sep=" ", header=None)
    DF.columns = ["x", "y", "z"]

    data = np.array((DF.x, DF.y)).T
    global CKDTREE
    CKDTREE = cKDTree(data)

    global TRANSFORMER
    TRANSFORMER = get_transformer(prj_file_path)


def get_elevation(lats: List[float], lons: List[float]) -> tuple[list[float], list[Any]]:
    if TRANSFORMER is None:
        get_and_set_sources()
    if TRANSFORMER is None:
        raise Exception("Transformer not available")
    if not isinstance(lats, Iterable):
        assert not isinstance(lons, Iterable)
        lats = [lats]
        lons = [lons]
    try:
        elevations = _get_elevation_interpolated(
            lats, lons, ckdtree=CKDTREE, df=DF, transformer=TRANSFORMER
        )
        errors = [None for _ in elevations]
        if any((math.isnan(ele) for ele in elevations)):
            raise NanValueException
    except (NanValueException, QhullError):
        elevations = []
        errors = []
        for lat, lon in zip(lats, lons):
            try:
                elevation = _get_elevation_interpolated(
                    [lat], [lon], ckdtree=CKDTREE, df=DF, transformer=TRANSFORMER
                )
                if any((math.isnan(ele) for ele in elevation)):
                    raise NanValueException
                errors.append(None)
            except (NanValueException, QhullError):
                elevation = _get_elevation_closest(
                    [lat], [lon], ckdtree=CKDTREE, df=DF, transformer=TRANSFORMER
                )
                errors.append(
                    "Could not interpolate elevation for this coordinate. Returned "
                    "elevation of the closest known coordinate in Germany."
                )
            elevations.extend(elevation)
    return elevations, errors


def _get_elevation_interpolated(lats, lons, ckdtree, df, transformer):
    xs, ys = transformer.transform(lons, lats)
    # find the 4 closest neighbors for interpolation eg=k=[1,2,3,4]
    distance, indicies = ckdtree.query((list(zip(xs, ys))), k=[1, 2, 3, 4])
    indicies = np.unique(np.ravel(indicies))
    relevant_df = df.loc[indicies]
    interp = LinearNDInterpolator(list(zip(relevant_df.x, relevant_df.y)), relevant_df.z)
    vec_interp = np.vectorize(interp)
    elevations = vec_interp(xs, ys)
    return elevations


def _get_elevation_closest(lats, lons, ckdtree, df, transformer):
    xs, ys = transformer.transform(lons, lats)
    # find the 1 closest neighbors for interpolation eg=k=[1]
    distance, indicies = ckdtree.query((list(zip(xs, ys))), k=[1])
    indicies = np.unique(np.ravel(indicies))
    elevations = df.loc[indicies].z
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
DF = None
