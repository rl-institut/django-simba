from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(label=_("Vorname (optional)"), max_length=64, required=False)
    last_name = forms.CharField(label=_("Nachname (optional)"), max_length=64, required=False)
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
        label=_("E-Mail*"),
        max_length=254,
        required=True,
    )
    password1 = forms.CharField(label=_("Passwort*"), required=True, widget=forms.PasswordInput)
    password2 = forms.CharField(
        label=_("Passwort wiederholen*"), required=True, widget=forms.PasswordInput
    )
    accepts_terms = forms.BooleanField(required=True)
    invited = forms.BooleanField(required=False, disabled=True, widget=forms.HiddenInput)

    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
            "password1",
            "password2",
            "accepts_terms",
        )

    def clean_accepts_terms(self):
        accepts_terms = self.cleaned_data["accepts_terms"]
        if not accepts_terms:
            raise forms.ValidationError(
                _("Für einen Zugang musst du der Datenschutzerklärung zustimmen.")
            )
        return True

    def clean_email(self):
        """
        Check that lowercase user email is unique (used as username)
        """
        email = self.cleaned_data["email"]
        if User.objects.filter(username=email.lower()).exists():
            raise forms.ValidationError(email.lower() + _(" existiert bereits."))
        return email


class AuthForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={"autofocus": True}),
        label=_("E-Mail"),
        max_length=254,
        required=True,
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={}), label=_("Passwort"), required=True
    )

    def clean_username(self):
        """
        force lowercase (used as username)
        """
        return self.cleaned_data["username"].lower()


class PWReset(PasswordResetForm):
    def clean_email(self):
        """
        Check given email address (case insensitive).
        """
        email = self.cleaned_data["email"]
        try:
            user = User.objects.filter(email__iexact=email).get()
            return user.email
        except User.DoesNotExist:
            return email
