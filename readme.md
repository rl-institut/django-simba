## Installation

1. Clone this git repository (or [download a specific release](https://github.com/rl-institut/django-simba/releases)
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
    4. The software requires a [backend for Celery](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html) to be available. Its address is set in the `CELERY_BROKER_URL` in the `ebusdjango/settings.py` file.
         - [rabbitmq](https://www.rabbitmq.com/) can be installed on ubuntu using `apt install rabbitmq-server`
         - A user can be added using the following commands (or the guest user can bs used):
             1. `rabbitmqctl add_user $user $password`
             2. `rabbitmqctl set_permissions -p / $user ".*" ".*" ".*"`
    5. Using a [virtual environment](https://docs.python.org/3/library/venv.html) is recommended.
    6. The dependencies are listed in the `requirements.txt` file. They can be installed via
        ```bash
        pip install -r requirements.txt
        ```
       - [eBusToolBox](https://github.com/rl-institut/eBus-Toolbox) is listed in the `requirements.txt`as a `git+ssh` URL to be used with [public key authentication](https://docs.github.com/en/authentication/connecting-to-github-with-ssh), as a password-based login is [no longer supported](https://github.blog/changelog/2021-08-12-git-password-authentication-is-shutting-down/). You may remove the `ebus_toolbox` line from `requirements.txt` and install it another way if it doesn't work for you.     

3. Set up django (inside the virtual environment)
    1. Set up the database: `python manage.py migrate`
    2. Create admin account: `python manage.py createsuperuser` **TODO: Is this necessary?**

## Running
1. Start a celery worker (in another terminal): `celery -A ebusdjango worker -l info`
    - on macOS `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` before the command may be necessary 
2. Run the server: `python manage.py runserver`
