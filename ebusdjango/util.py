from pathlib import Path

from django.conf import settings


def get_static_file_path(package, path_from_static):
    p = Path(settings.STATIC_URL, package, path_from_static)
    if settings.DEBUG:
        # use app static folder
        if p.is_absolute():
            # remove first slash
            p = Path(str(p).lstrip("/"))
        p = Path(settings.BASE_DIR, package, p)
    return p
