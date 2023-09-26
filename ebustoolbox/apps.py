from django.apps import AppConfig


class EbustoolboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ebustoolbox"

    def ready(self) -> None:
        """Content in here is run when app is ready."""
        pass
