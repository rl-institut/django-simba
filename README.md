## Installation

1. Clone this git repository (or [download a specific release](https://github.com/rl-institut/django-simba/releases))
    ```bash
    git clone git@github.com:rl-institut/django-simba.git
    ```
   2. Install prerequisites
       1. The currently suggested Python version is 3.11.*, it may work with other versions, but this is not tested.
       2. This software requires GDAL, which can be installed
            - on Linux via the system's package manager (e.g. `apt install gdal-bin` on Ubuntu)
            - on macOS via [Homebrew](https://brew.sh/) (`brew install gdal`)
            - on Windows via [OSGeo4W](https://trac.osgeo.org/osgeo4w/) (select the `gdal` package)
       3. The software requires a PostgreSQL database with the PostGIS package.
            - The software is found [here](https://www.postgresql.org/download/) and [here](https://postgis.net/documentation/getting_started/) or via your system's (or server's) package manager (e.g. `apt install postgis`)
            - The credentials for the database are set in `ebusdjango/settings.py` in the `DATABASES`variable. **SECURITY WARNING: Do not commit your passwords to GitHub!**
            - In order to set up PostGIS, the user you created needs to have 'superuser' privileges.
       4. The software can optionally use [backend for Celery](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html) to be available. Its address is set in the `CELERY_BROKER_URL` in the `.env` file.
            - [rabbitmq](https://www.rabbitmq.com/) can be installed on ubuntu using `apt install rabbitmq-server`
            - A user can be added using the following commands (or the guest user can bs used):
                1. `rabbitmqctl add_user $user $password`
                2. `rabbitmqctl set_permissions -p / $user ".*" ".*" ".*"`
       5. Install [poetry](https://python-poetry.org/docs/#installation) for environment and dependency management.
       6. The dependencies can be installed via
           ```bash
           poetry install
           ```
          For developers, use
           ```bash
           poetry install --with dev
           ```
       7. Django uses an .env file to read user specific data. This file has to be created by the user and is not shared through GitHub to make uploads of sensitive data impossible. Create a file named `.env` with the following input`
      ````text
    DJANGO_SECRET_KEY=INSERT_YOUR_KEY_HERE
    DJANGO_DEBUG=True
    # Replace with your own database info
    DATABASE_URL=postgis://YOUR_DB_USERNAME:YOUR_PASSWORD@localhost/YOUR_DB_NAME
    # Should celery be used? If so a CELERY_BROKER_URL has to be provided
    CELERY_USE=False
    # CELERY_BROKER_URL=pyamqp://guest@localhost//
    TILING_SERVICE_TOKEN=GET_YOUR_TOKEN_THROUGH_MAP_TILER
    TILING_SERVICE_STYLE_ID=basic-v2
    DJANGO_SETTINGS_MODULE=ebusdjango.settings
    ````

3. Set up django (inside the virtual environment)
    1. Set up the database: `python manage.py migrate`
    2. Create admin account: `python manage.py createsuperuser` **TODO: Is this necessary?**

## Running
1. Only if `.env`has a celery broker listed, start a celery worker (in another terminal): `celery -A ebusdjango worker -l info`
    - on macOS `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` before the command may be necessary 
2. Run the server: `python manage.py runserver`


## Development
For development dependencies can be installed via
```bash
poetry install --with dev
```
To run tests your PostgreSQL user needs SUPERUSER rights to be able to create test databases and delete them. Furthermore, for testing with selenium chromedriver is needed. Resources can be found here [Latest Google Chrome and Chromedriver](https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json). Chromedriver has to be added to PATH.