from django.views.generic import TemplateView


# Create your views here.
class LandingPageView(TemplateView):
    template_name = "core/landing_page.html"
