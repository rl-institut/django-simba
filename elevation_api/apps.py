from django.apps import AppConfig


class ElevationApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "elevation_api"

    def ready(self) -> None:
        """Content in here is run when app is ready."""
        pass
