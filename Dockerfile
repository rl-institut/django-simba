FROM python:3.11

# Configure Poetry
ENV POETRY_VERSION=1.4.2
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VENV=/opt/poetry-venv
ENV POETRY_CACHE_DIR=/opt/.cache

# Set environment variables
# turns off an automatic check for pip updates each time
ENV PIP_DISABLE_PIP_VERSION_CHECK 1
# means Python will not try to write .pyc files
ENV PYTHONDONTWRITEBYTECODE 1


# Install poetry separated from system interpreter
RUN python3 -m venv $POETRY_VENV \
	&& $POETRY_VENV/bin/pip install -U pip setuptools \
	&& $POETRY_VENV/bin/pip install poetry==${POETRY_VERSION}

# Install GDAL
RUN apt-get update &&\
    apt-get install -y binutils libproj-dev gdal-bin

# Add `poetry` to PATH
ENV PATH="${PATH}:${POETRY_VENV}/bin"

WORKDIR /app
# Install dependencies
# Since ebustoolbox and mapengine are installed as well, copy whole directory first
COPY . /app
RUN poetry install
#startup_command=poetry run python -c 'print(\"Started\")' && poetry run python manage.py makemigrations && poetry run python manage.py migrate && poetry run python manage.py runserver 0.0.0.0:8000
CMD ["sh", "-c", ${STARTUP_CMD}]
