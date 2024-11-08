from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.db import connection
        from django.conf import settings

        with connection.cursor() as cursor:
            db_name = settings.DATABASES["default"]["NAME"]
            cursor.execute(f"SELECT pg_size_pretty(pg_database_size('{db_name}'))")
            print(f"Default DB {db_name} has size of {cursor.fetchone()[0]}.")
