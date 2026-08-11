import logging
from pathlib import Path
import traceback
import zipfile as zf

import django
from celery import uuid
from io import BytesIO
from django import conf
import matplotlib
import sys

from django.db.models import Max

from .models import Scenario

logger = logging.getLogger("custom")

if not any(["selenium" in str(x) for x in sys.modules.values()]):
    # do not use tkagg during testing since it does not work with headless selenium
    # Explicitly call backend. Put into env? Without simba does not always properly generate plots
    matplotlib.use("Agg")


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
