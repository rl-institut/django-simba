from pathlib import Path

from django.conf import settings
from charset_normalizer import from_path


def get_static_file_path(package, path_from_static):
    p = Path(settings.STATIC_URL, package, path_from_static)
    if settings.DEBUG:
        # use app static folder
        if p.is_absolute():
            # remove first slash
            p = Path(str(p).lstrip("/"))
        p = Path(settings.BASE_DIR, package, p)
    return p


def get_file_encoding(filepath: str | Path) -> str | None:
    """
    Return the most likely encoding of a filepath or None.

    If no match is found, the function returns None
    :param filepath: str or Path of the file
    :return: string of encoding or None if no encoding was found
    """

    result = from_path(filepath).best()
    if result is None:
        return None
    if result.encoding == "utf_8":
        # utf-8 does not strip bom from file.
        # this breaks proper content reading.
        return "utf-8-sig"
    return result.encoding
