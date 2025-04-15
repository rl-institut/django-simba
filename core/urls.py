from django.urls import path
from django.contrib.auth.views import LoginView, PasswordResetView, LogoutView
from django.views.generic.base import TemplateView
from . import views, forms

app_name = "core"

urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            authentication_form=forms.AuthForm,
            template_name="core/registration/login.html",
        ),
        name="login",
    ),
    path(
        "logout/",
        LogoutView.as_view(template_name="core/registration/logged_out.html",),
        name="logout",
    ),
    path(
        "password_reset/",
        PasswordResetView.as_view(form_class=forms.PWReset),
        name="password_reset",
    ),
    path("password_change/", views.changePassword, name="password_change"),
    path("register/", views.signup, name="signup"),
    path(
        "profile/",
        TemplateView.as_view(template_name="core/profile.html"),
        name="profile"
    ),
    path(
        "help/",
        TemplateView.as_view(template_name="core/help.html"),
        name="help"
    ),
    path("test_email/", views.test_email, name="test_email"),
    path("impressum/", TemplateView.as_view(template_name="core/legal.html"), name="legal"),
    path("datenschutz/", TemplateView.as_view(template_name="core/privacy.html"), name="privacy"),
    path("", TemplateView.as_view(template_name="core/index.html"), name="home"),
]
