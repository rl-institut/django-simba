from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.generic import TemplateView

from .forms import SignUpForm


# Create your views here.
class LandingPageView(TemplateView):
    template_name = "core/landing_page.html"


# ******** User management ******** #
def signup(request):
    """
    Create new user model from form input.
    """
    if request.user.is_authenticated:
        return redirect(reverse("core:login"))
    if request.method == "POST":
        # posted data: create new user instance
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()  # read necessary info from form
            user.refresh_from_db()
            user.username = user.email.lower()  # force lowercase for username
            user.is_active = True
            user.save()
            return redirect(reverse("core:home"))
    else:
        # GET: present empty registration form
        form = SignUpForm()
    return render(request, "registration/signup.html", {"form": form})


@login_required(login_url="/login/")
def changePassword(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # auth user again
            messages.success(request, "Passwort erfolgreich geändert")
            return redirect(reverse("core:home"))
        else:
            messages.error(request, "Fehlerhafte Eingabe! Passwort nicht geändert.")
    else:
        form = PasswordChangeForm(request.user)
    # return view
    return render(request, "registration/password_change.html", {"form": form})
