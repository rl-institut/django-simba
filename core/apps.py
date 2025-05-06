from django.apps import AppConfig
from django.conf import settings
from django.db import connection
import logging

logger = logging.getLogger("custom")


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):

        with connection.cursor() as cursor:
            db_name = settings.DATABASES["default"]["NAME"]
            cursor.execute(f"SELECT pg_size_pretty(pg_database_size('{db_name}'))")
            logger.info(f"Default DB {db_name} has size of {cursor.fetchone()[0]}.")
