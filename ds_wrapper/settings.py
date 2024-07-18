import os.path
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Application definition

INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "core", "ebustoolbox"]

# ebustoolbox settings
UPLOAD_PATH = "uploads/"

DATABASES = {"default": "THIS WILL BE REPLACED BY DjangoSimbaWrapper"}

STATIC_URL = os.path.abspath(Path(BASE_DIR).joinpath("static/"))