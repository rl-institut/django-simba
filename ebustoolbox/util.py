import base64
import logging
from pathlib import Path
import traceback
import zipfile as zf

import django
from celery import uuid
from io import BytesIO
from django import conf
import matplotlib
import pandas as pd
import sys

from django.db.models import Max
from django.utils.translation import gettext as _

from .models import Scenario
from ebustoolbox.data import get_powerdraw_as_dataframe

logger = logging.getLogger("custom")

if not any(["selenium" in str(x) for x in sys.modules.values()]):
    # do not use tkagg during testing since it does not work with headless selenium
    # Explicitly call backend. Put into env? Without simba does not always properly generate plots
    matplotlib.use("Agg")

# Imported after the backend is chosen above, since pyplot binds it on import
import matplotlib.pyplot as plt  # noqa: E402


# TODO: remove since checking uuid is not common since duplicates are too rare
def get_unique_task_id() -> str:
    task_id_not_unique = True
    task_id = None
    # Create unique ids for as long as needed, so no duplicate ids exist
    while task_id_not_unique:
        try:
            task_id = uuid()
            Scenario.objects.get(task_id=task_id)
        except Scenario.DoesNotExist:
            task_id_not_unique = False
    return task_id


def get_charge_chart(station):
    """
    Get charge plot for specific station, ready for HTML display
    """
    # get power at this station
    power_df = get_powerdraw_as_dataframe(station.scenario.id)
    power_df = power_df[power_df["Station_id"] == station.id]
    power_df = power_df[power_df["Power"] > 0]
    if power_df.empty:
        return None

    # Every row is one vehicle charging at a constant power between time_start and time_end. The
    # station's draw at any moment is the sum of the rows covering it, so add each row's power at
    # its start and take it away again at its end, then accumulate.
    deltas = pd.concat(
        [
            power_df[["time_start", "Power"]].rename(columns={"time_start": "time"}),
            power_df[["time_end", "Power"]]
            .rename(columns={"time_end": "time"})
            .assign(Power=lambda frame: -frame["Power"]),
        ]
    )
    power_over_time = deltas.groupby("time")["Power"].sum().sort_index().cumsum()

    figure, ax = plt.subplots(figsize=(5, 2.5))
    try:
        # The power holds until the next change, so step between the points rather than
        # interpolating a slope that never happened
        power_over_time.plot(ax=ax, drawstyle="steps-post", legend=False)
        ax.set_xlabel(_("Zeit"))
        ax.set_ylabel(_("Leistung [kW]"))
        ax.set_ylim(bottom=0)
        figure.tight_layout()

        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=100)
        buffer.seek(0)
        image_png = buffer.getvalue()
        buffer.close()
    finally:
        # Every popup renders one of these, and pyplot keeps figures alive until they are closed
        plt.close(figure)

    return base64.b64encode(image_png).decode("utf-8")


def get_next_id(model: django.db.models.Model) -> int:
    if model.objects.exists():
        return model.objects.aggregate(Max("id"))["id__max"] + 1
    return 1


class ZipFileException(Exception):
    pass


def to_zip(file_names: list[str] | str, write_data=list[str] | str) -> BytesIO:
    """Returns a zipped BytesIO Buffer objects from a list of file_names, and write data"""
    if not isinstance(file_names, list):
        file_names = [file_names]

    if not isinstance(write_data, list):
        write_data = [write_data]

    assert len(write_data) == len(file_names), "Same number of write_data as filenames needed"
    zip_buffer = BytesIO()
    with zf.ZipFile(zip_buffer, "w", zf.ZIP_DEFLATED) as zip_file:
        for file_name, content in zip(file_names, write_data):
            zip_file.writestr(file_name, content)
    return zip_buffer


def validate_zip(
    zip_file: zf.ZipFile,
    max_files: int,
    max_total_size: int,
    max_depth: int,
    current_file_nr: int = 0,
    current_total_size: int = 0,
    current_depth: int = 0,
) -> tuple[int, int]:
    """
    Get the uncompressed size and file number of a Zip file path.

    Throws a ZipFileException if number of files exceeds the max_files, or max_total_size attributes.

    :param zip_file: ZipFile to be validated
    :param max_files: max allowed number of files
    :param max_total_size: max allowed size in bytes for the uncompressed file
    :param max_depth: max allowed nesting, e.g. zip files inside zipfiles
    :param current_file_nr: initialization for counting files
    :param current_total_size: initialization for total_size
    :param current_depth: initialization for depth
    :return: total number of files, total size of uncompressed zip
    :raises ZipFileException: If the ZipFile is to nested, has to many files or the size is to large
    """

    if current_depth > max_depth:
        raise ZipFileException("Zipfile is to deeply nested")
    try:
        if True:
            pass
            total_files = 0
            total_size = 0
        for file in zip_file.infolist():
            if Path(file.filename).suffix == ".zip":
                inner_zip = zf.ZipFile(BytesIO(zip_file.read(file)))
                files, size = validate_zip(
                    inner_zip,
                    max_files,
                    max_total_size,
                    max_depth,
                    current_file_nr=total_files,
                    current_total_size=total_size,
                    current_depth=current_depth + 1,
                )
                total_files += files
                total_size += size
            else:
                total_files += 1
                if total_files > max_files:
                    raise ZipFileException(
                        f"More than the allowed number of {max_files} files per Zip file "
                    )
                total_size += file.file_size
                if total_size > max_total_size:
                    raise ZipFileException(
                        f"Uncompressed Zip file is larger than the allowed {max_total_size>>20} MB"
                    )
                # Validate file path to prevent path traversal
                extraction_dir = conf.settings.MEDIA_ROOT
                # Validate file path to prevent path traversal
                extracted_path = Path(extraction_dir, file.filename)
                if not extracted_path.resolve().is_relative_to(Path(extraction_dir).resolve()):
                    raise ZipFileException(f"Zipfile name is not allowed ({file.filename})")
    except ZipFileException:
        raise
    except Exception as e:
        logger.warning(traceback.format_exception(e))
        raise ZipFileException("Validating zipfile failed due to an unexpected Exception (see log)")
    return total_files, total_size
