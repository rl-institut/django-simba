from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core import signing, mail
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect
from django.urls import reverse

from .forms import SignUpForm


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
        if not form.is_valid():
            return render(request, "core/registration/signup.html", {"form": form})
        user = form.save()  # read necessary info from form
        user.refresh_from_db()
        user.username = user.email.lower()  # force lowercase for username
        user.is_active = True
        user.save()
        login(request, user)
        return redirect(reverse("core:home"))
    elif request.GET.get("token"):
        # GET: present registration form, fill in email from token
        try:
            email = signing.loads(request.GET["token"])
        except signing.BadSignature:
            return HttpResponse("Wrong signature", status=400)
        if User.objects.filter(username=email).exists():
            return redirect(reverse("login"))
        form = SignUpForm(initial={"email": email})
        return render(request, "core/registration/signup.html", {"form": form})
    raise Http404()


def set_lang(request, lang: str):
    """Set a cookie for the prefered language of the user

    Middleware will activate the language on a per response basis
    """
    # TODO: placeholder for proper implementation
    from django.utils.translation import activate
    from django.conf import settings

    activate(lang)
    response = HttpResponse(f"Switched to {lang}")
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang)
    return response


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
    return render(request, "core/registration/password_change.html", {"form": form})


@login_required(login_url="/login/")
def test_email(request):
    if request.user.is_staff:
        mail.send_mail(
            subject="TEST",
            message="Wenn Sie das lesen können, ist die Email angekommen.",
            from_email=None,
            recipient_list=[request.user.email],
            fail_silently=False,
        )
    return redirect(request.GET.get("path", "/"))
