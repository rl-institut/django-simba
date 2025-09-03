FROM python:3.11-slim

# Add django as user and group
RUN addgroup --system django \
    && adduser --system --ingroup django django

# Configure Poetry
ENV POETRY_VERSION=1.4.2
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VENV=/opt/poetry-venv
ENV POETRY_CACHE_DIR=/opt/.cache

# Set environment variables
# turns off an automatic check for pip updates each time
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
# means Python will not try to write .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
# Explicitly set Matplotlib's cache directory to a writable location
ENV MPLCONFIGDIR=/tmp

# Install GDAL as root
RUN apt-get update &&\
    apt-get install -y binutils libproj-dev gdal-bin

# Install poetry separated from system interpreter
RUN python3 -m venv $POETRY_VENV \
	&& $POETRY_VENV/bin/pip install -U pip setuptools \
	&& $POETRY_VENV/bin/pip install poetry==${POETRY_VERSION}

# Add `poetry` to PATH
ENV PATH="${PATH}:${POETRY_VENV}/bin"

# The django user does not get write access to the starscripts, but they are made executable
COPY ./start /start
COPY ./start_celery /start_celery
RUN sed -i 's/\r$//g' /start /start_celery && chmod +x /start /start_celery

WORKDIR /app

# Since ebustoolbox and mapengine are installed as well, copy whole directory first
COPY . /app

# The django user needs write access to these directories (for logging, fileupload and simulation)
RUN mkdir -p /var/log/app /app/media && chown django:django /var/log/app /app/media
RUN chown -R django:django /app/data

# Install dependencies
RUN poetry install --no-root

# switch to non-root User
USER django
#startup_command=poetry run python -c 'print(\"Started\")' && poetry run python manage.py makemigrations && poetry run python manage.py migrate && poetry run python manage.py runserver 0.0.0.0:8000
CMD ${STARTUP_COMMAND}
