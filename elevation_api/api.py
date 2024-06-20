import logging
import traceback
import math
import numpy as np
from django.contrib.gis.gdal import GDALRaster
import pyproj
from typing import List, Iterable, Any, Tuple
import requests
import zipfile
from io import BytesIO

from django.conf import settings
from django.http import Http404
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import QhullError
from elevation_api.models import Elevation

logger = logging.getLogger("custom")
INITIALIZED = False


class NanValueException(Exception):
    pass


def get_and_set_sources():
    """Download elevation data files"""

    ele, created = Elevation.objects.get_or_create(name="Germany")
    if not created:
        return ele.raster

    # Get the directory of the script
    # Create the full path for the extraction location
    extract_path = settings.BASE_DIR / settings.MEDIA_ROOT
    # Ensure the extraction directory exists
    extract_path.mkdir(exist_ok=True)
    needed_files = ["dgm200_utm32s.prj", "dgm200_utm32s.asc"]

    if all([(extract_path / nfile).is_file() for nfile in needed_files]):
        logger.info("Elevation files exist and downloading is skipped.")
    else:
        # Download the files

        url = settings.ELEVATION_SOURCE_URL
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

    data_file = extract_path / "dgm200_utm32s.asc"
    logger.info("Reading data_file")
    logger.info("Creating Transformer")

    # transforms which ever source into WGS84
    # dest_code = 4326
    # projection_transformer = get_transformer(prj_file_path, dest_code)
    rast = GDALRaster(data_file, write=True)
    ele.raster = rast
    ele.save()

    return rast


def pixelToMap(gt, pos) -> Tuple[float, float]:
    return (gt[0] + pos[0] * gt[1] + pos[1] * gt[2], gt[3] + pos[0] * gt[4] + pos[1] * gt[5])


# Reverses the operation of pixelToMap(), according to:
# https://en.wikipedia.org/wiki/World_file because GDAL's Affine GeoTransform
# uses the same values in the same order as an ESRI world file.
# See: http://www.gdal.org/gdal_datamodel.html
def mapToPixel(gt, pos):
    x_map, y_map = pos
    c, a, b, f, d, e = gt
    s = a * e - d * b
    x_pixel = (e * x_map - b * y_map + b * f - e * c) / s
    y_pixel = (-d * x_map + a * y_map + d * c - a * f) / s
    return (x_pixel, y_pixel)


def valueAtMapPos(image, gt, pos):
    pp = mapToPixel(gt, pos)
    x = int(pp[0])
    y = int(pp[1])

    if x < 0 or y < 0 or x >= image.shape[1] or y >= image.shape[0]:
        raise Exception()

    # Note how we reference the y column first. This is the way numpy arrays
    # work by default. But GDAL assumes x first.
    return image[y, x]


def get_elevation(lats: List[float], lons: List[float]) -> tuple[list[float], list[Any]]:
    if not isinstance(lats, Iterable):
        if not isinstance(lons, Iterable):
            raise Http404("Latitude and longitude must be iterable")
        lats = [lats]
        lons = [lons]

    rast = get_and_set_sources()
    # Load the entire dataset into one numpy array.
    image = np.array(rast.bands[0].data()).astype(np.int16)

    # Initialize errors expecting no errors
    errors = [None for _ in lats]
    try:
        elevations = _get_elevation_interpolated(lats, lons, image, rast)
        if any((math.isnan(ele) for ele in elevations)):
            raise NanValueException
    except (NanValueException, QhullError, ValueError):
        # Something went wrong. Check all single requests one by one
        elevations = []
        errors = []
        for lat, lon in zip(lats, lons):
            try:
                elevation = _get_elevation_interpolated([lat], [lon], image, rast)
                if any((math.isnan(ele) for ele in elevation)):
                    raise NanValueException
                errors.append(None)
            except (NanValueException, QhullError):
                elevation = [0]
                errors.append("Could not interpolate elevation for this coordinate. Returned 0.")
            except ValueError:
                elevation = [0]
                errors.append(
                    "Values for latitude and longitude must be float values between -90 and +90."
                    "Returned 0."
                )
            elevations.extend(elevation)
    return elevations, errors


def _get_elevation_interpolated(lats, lons, image, raster):
    nodata, relevant_x, relevant_y, xs, ys = get_relevant_pixels(lats, lons, raster)

    try:
        relevant_z = image[(relevant_y, relevant_x)]
    except IndexError:
        raise NanValueException
    if nodata in relevant_z:
        raise NanValueException
    interp = LinearNDInterpolator(list(zip(relevant_x, relevant_y)), relevant_z)
    vec_interp = np.vectorize(interp)
    elevations = vec_interp(xs, ys)
    return elevations


def get_relevant_pixels(lats, lons, raster):
    geotransform = raster.geotransform
    # We need to nodata value for our MaskedArray later.
    nodata = raster.bands[0].nodata_value
    xs = []
    relevant_x = []
    ys = []
    relevant_y = []
    for lat, lon in zip(lats, lons):
        pp = mapToPixel(geotransform, (lon, lat))
        x = pp[0]
        y = pp[1]
        xs.append(x)
        ys.append(y)
        relevant_x.extend([math.floor(x), math.floor(x), math.ceil(x), math.ceil(x)])
        relevant_y.extend([math.floor(y), math.ceil(y), math.floor(y), math.ceil(y)])
    return nodata, relevant_x, relevant_y, xs, ys


def get_transformer(prj_file_path, dest_code=4326):
    # Read PRJ file and create coordinate transformation
    try:
        with open(prj_file_path, "r") as prj_file:
            prj_contents = prj_file.read()
            src_crs = pyproj.CRS.from_string(prj_contents)
        dst_crs = pyproj.CRS.from_epsg(dest_code)  # WGS84 CRS (latitude and longitude)
        return pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    except Exception:
        traceback.print_exc()
        return None
