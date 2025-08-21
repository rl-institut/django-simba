import zoneinfo
from django.utils import timezone
from django.utils.translation import activate


class LanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        activate(request.LANGUAGE_CODE)
        return self.get_response(request)


class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            # get django_timezone from cookie
            tzname = request.COOKIES.get("django_timezone")
            if tzname:
                timezone.activate(zoneinfo.ZoneInfo(tzname))
            else:
                timezone.deactivate()
        except:  # noqa
            timezone.deactivate()
        return self.get_response(request)
