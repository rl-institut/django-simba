from django.urls import path
from django.contrib.auth.views import LoginView, PasswordResetView
from . import views, forms

app_name = "core"

urlpatterns = [
    path('login/', LoginView.as_view(authentication_form=forms.AuthForm), name='login'),
    path('password_reset/', PasswordResetView.as_view(form_class=forms.PWReset), name='password_reset'),
    path('password_change/', views.changePassword, name='password_change'),
    path('register/', views.signup, name='signup'),
    path("", views.LandingPageView.as_view(), name="home"),
]
