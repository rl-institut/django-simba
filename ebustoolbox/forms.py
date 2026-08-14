from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

# Imported under its own name rather than aliased. Field labels, units and tooltips are
# evaluated when this module is imported, once per process and long before a request has
# picked a language, so they have to be lazy. And "makemessages" passes a fixed list of
# function names to xgettext (--keyword=gettext_lazy among them); a local alias is not on
# that list, so aliasing would leave every string below unextractable -- which is the bug
# this replaced in the first place.
from django.utils.translation import gettext_lazy

from . import models
from .models import (
    AreaType,
    EnumChargeType,
    EnumVoltageLevel,
    Line,
    Station,
    VehicleType,
    SimulationTemperatures,
    Scenario,
)


class UploadFileForm(forms.Form):
    # basics
    title = forms.CharField(max_length=50, initial="SimBA")
    # task_id = forms.CharField(widget=forms.HiddenInput, required=False)
    preferred_charging_type = forms.CharField(
        widget=forms.RadioSelect(choices=EnumChargeType.choices),
        initial=EnumChargeType.choices[0][0],
    )
    modes = forms.CharField(widget=forms.HiddenInput, initial="sim,station_optimization,report")

    # charging infrastructure
    gc_power_opps = forms.DecimalField(max_digits=10, decimal_places=2, initial=1e5)
    gc_power_deps = forms.DecimalField(max_digits=10, decimal_places=2, initial=1e5)
    cs_power_opps = forms.DecimalField(max_digits=10, decimal_places=2, initial=300)
    cs_power_deps_depb = forms.DecimalField(max_digits=10, decimal_places=2, initial=150)
    cs_power_deps_oppb = forms.DecimalField(max_digits=10, decimal_places=2, initial=150)
    default_voltage_level = forms.CharField(
        widget=forms.RadioSelect(choices=EnumVoltageLevel.choices),
        initial="MV",
    )

    # charging settings
    desired_soc_deps = forms.DecimalField(min_value=0, max_value=1, initial=1)
    desired_soc_opps = forms.DecimalField(min_value=0, max_value=1, initial=1)
    min_recharge_deps_oppb = forms.DecimalField(min_value=0, max_value=1, initial=1)
    min_recharge_deps_depb = forms.DecimalField(min_value=0, max_value=1, initial=1)
    min_charging_time = forms.DecimalField(min_value=0, initial=0)
    default_buffer_time_opps = forms.DecimalField(min_value=0, initial=0)

    # files
    schedule_path = forms.FileField(required=False)
    electrified_stations_path = forms.FileField(required=False)
    vehicle_types_path = forms.FileField(required=False)
    station_data_path = forms.FileField(required=False)
    outside_temperature_over_day_path = forms.FileField(required=False)
    temperature_time_series_path = forms.FileField(
        required=False, help_text=_("Verknüpft SimBA-Trips mit Temperaturen")
    )
    consumption_path = forms.FileField(
        required=False, help_text=_("Zur Interpolation von Verbräuchen verwendet")
    )

    level_of_loading_over_day_path = forms.FileField(required=False)
    cost_parameters_path = forms.FileField(required=False)
    optimizer_config_path = forms.CharField(required=False)

    # extended options
    strategy = forms.CharField(
        widget=forms.Select(choices=[("distributed", "distributed")]), initial="distributed"
    )
    interval = forms.DecimalField(initial=1)
    signal_time_dif = forms.DecimalField(initial=10)
    days = forms.IntegerField(required=False)
    include_price_csv = forms.FileField(required=False)
    seed = forms.CharField(required=False)
    cost_calculation = forms.BooleanField(initial=False, required=False)
    # Should the temperature time series use date specific data or use data of a single day
    # reduced to the time?
    use_only_time = forms.BooleanField(initial=True, required=False)


class SimulationFilterForm(forms.Form):
    start = forms.DateTimeField()
    end = forms.DateTimeField()
    depot_select = forms.ModelMultipleChoiceField(
        queryset=Station.objects.none(),
        label="Depotauswahl",
        required=False,
    )
    line_select = forms.ModelMultipleChoiceField(
        queryset=Line.objects.none(),
        label="Linienauswahl",
        required=False,
    )
    scenario: Scenario | None = None

    def __init__(self, scenario, *args, **kwargs):
        self.scenario = scenario
        super().__init__(*args, **kwargs)
        qs = Station.objects.filter(scenario=scenario.parent, charge_type=EnumChargeType.DEPOT)

        self.fields["depot_select"].queryset = Station.objects.filter(
            id__in=list(qs.values_list("id", flat=True))
        )
        self.fields["depot_select"].label_from_instance = lambda obj: f"{obj.name}"
        qs = Line.objects.filter(scenario=scenario.parent)
        self.fields["line_select"].queryset = Line.objects.filter(
            id__in=list(qs.values_list("id", flat=True))
        )
        self.fields["line_select"].label_from_instance = lambda obj: f"{obj.name}"


class SimulationTemperaturesForm(forms.ModelForm):
    temperature_average = forms.IntegerField(min_value=-5, max_value=30)
    temperature_extreme = forms.IntegerField(min_value=-5, max_value=30)

    class Meta:
        model = SimulationTemperatures
        exclude = ("scenario",)


class TripsForm(forms.Form):
    data_file = forms.FileField(required=False)
    existing_scenario = forms.UUIDField(required=False)
    scenario_name = forms.CharField(max_length=100)
    description = forms.CharField(max_length=100, required=False)
    find_stations = forms.BooleanField(required=False)

    # TODO: use clean method instead
    def is_valid(self):
        if not super().is_valid():
            return False
        data_file = self.files.get("data_file")
        existing_scenario = self.cleaned_data.get("existing_scenario")
        # Use XOR to guarantee only one is given: data_file or existing_scenario
        if not (bool(data_file) ^ bool(existing_scenario)):
            error_text = "Lade eine Datei hoch oder wähle ein existierendes Szenario aus"
            self.errors["data_file"] = error_text
            self.errors["existing_scenario"] = error_text
            return False

        if existing_scenario:
            return True
        # File uploaded -> check size
        # check sum of file sizes
        if sum([f.size for f in self.files.values()]) > settings.MAX_FILE_SIZE_B:
            error_text = (
                "Datei ist zu groß. Laden sie eine Datei kleiner "
                f"als {settings.MAX_FILE_SIZE_B / 1e6} MB hoch."
            )
            self.errors["data_file"] = error_text
            return False

        file_suffix = data_file.name[-3:]
        if file_suffix not in ["csv", "zip"]:
            self.errors["data_file"] = f"Der Dateityp {file_suffix} wird nicht unterstützt"
            return False
        return True


class VehicleTypeSelectionForm(forms.ModelForm):
    class Meta:
        fields = ["default_vehicle_type"]
        model = models.VehicleTypeSelection

    def __init__(self, *args, vehicle_type=None, choices_queryset=None, **kwargs):
        super(VehicleTypeSelectionForm, self).__init__(*args, **kwargs)
        self.fields["default_vehicle_type"].queryset = choices_queryset


class VehicleTypeForm(forms.ModelForm):
    has_diesel_heating = forms.BooleanField(
        required=False,
        initial=False,
        label=_("Dieselzusatzheizung"),
        help_text=_(
            "Dem Fahrzeugtyp eine Dieselzusatzheiung hinzufügen. "
            "Dies reduziert den Verbrauch bei niedrigen Temperaturen"
        ),
    )

    # Consumption must be turned on in front end -> todo discuss
    class Meta:
        model = VehicleType
        fields = ["battery_capacity", "consumption", "max_consumption"]

        help_texts = {
            "battery_capacity": _(
                "Hier können Sie die gewünschte Batteriekapazität des Fahrzeugtyps anpassen."
            ),
            "consumption": _("Welchen durchschnittlichen Verbrauch in kWh/km hat dieses Fahrzeug?"),
            "max_consumption": _("Welchen max. Verbrauch in kWh/km hat dieses Fahrzeug?"),
        }
        labels = {
            "battery_capacity": _("Batteriekapazität [kWh]"),
            "consumption": _("Verbrauch [kWh/km]"),
            "max_consumption": _("max. Verbrauch [kWh/km]"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["battery_capacity"].widget.attrs.update({"min": 1.0})
        self.fields["consumption"].required = False
        self.fields["max_consumption"].required = False


class DepotCalculationForm(forms.Form):
    CHOICES = [
        ("automatic", "Automatisch berechnen lassen"),
        ("manual", "Detail zu den Stationen angeben"),
    ]
    calculation_mode = forms.ChoiceField(choices=CHOICES, required=True)


class ChargingPowerForm(forms.Form):
    # General charging_power is required when radio button constant power is set.
    default_charge_power = forms.FloatField(required=True, min_value=0, step_size=1)


class StationForm(forms.ModelForm):
    class Meta:
        fields = [
            "is_electrified",
            "is_electrifiable",
            "amount_charging_places",
            "power_per_charger",
        ]
        model = models.Station

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount_charging_places"].widget.attrs.update({"min": 1.0})
        self.fields["power_per_charger"].widget.attrs.update({"min": 1.0})
        self.fields["amount_charging_places"].required = False
        self.fields["power_per_charger"].required = False

    def clean(self):
        cleaned_data = self.cleaned_data
        if not cleaned_data["is_electrifiable"] and cleaned_data["is_electrified"]:
            # an electrified station must be electrifiable
            raise ValidationError("A station which is not electrifiable can not be electrified")
        return cleaned_data


# Todo deprecated
class StationExcludedForm(forms.Form):
    is_excluded = forms.BooleanField(initial=False, required=False)


class CostInputModeForm(forms.Form):
    CHOICES = [
        ("no_input", "Keine Eingabe"),
        ("manual", "Manuelle Eingabe"),
    ]
    input_mode = forms.ChoiceField(
        widget=forms.RadioSelect,
        choices=CHOICES,
    )


# ---------------------------------------------------------------------------
# TCO parameter forms
#
# One form per row that owns a ``tco_parameters`` column, with field names matching
# the schema eflips-impact reads, so saving is a direct write with no translation
# layer. Defaults come from ``ebustoolbox/defaults/impact/tco.json`` via
# ``ebustoolbox.impact.ensure_fleet_topology``, which has already written them onto
# the rows by the time these forms are built — so the initial values shown are the
# stored values, and the JSON is the single source of truth for what "default" means.
# ---------------------------------------------------------------------------

TCO_INPUT_CLASS = (
    "block bg-white min-w-0 grow mr-2 px-3 py-1.5 border border-slate-400 rounded-md "
    "text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 "
    "focus:ring-emerald-400 focus:ring-offset-0 focus:border-emerald-400 sm:text-sm/6"
)


class TcoFieldMixin:
    """Adds the presentation metadata a TCO input needs beside its value.

    ``label`` and ``help_text`` are Django's own — the tooltip is the help text — so
    the only addition is :attr:`unit`, the symbol rendered after the input.

    Keeping all three on the field rather than in the template is what makes them
    translatable. Passing them through ``{% include %}`` reached a
    ``{% blocktrans %}{{ label }}{% endblocktrans %}``, which ``makemessages``
    extracts as the literal ``%(label)s``: no catalogue entry could ever match, so
    the English site rendered German no matter what was translated.

    :ivar unit: The unit shown after the input.
    """

    def __init__(self, *args, unit="", **kwargs):
        self.unit = unit
        super().__init__(*args, **kwargs)


class TcoIntegerField(TcoFieldMixin, forms.IntegerField):
    """A whole-number TCO parameter, such as a lifetime in years."""


class TcoFloatField(TcoFieldMixin, forms.FloatField):
    """A decimal TCO parameter, such as a price."""


class PercentField(TcoFieldMixin, forms.FloatField):
    """A rate stored as a fraction but entered as a percentage.

    Everything the user sees is a percentage: the value in the input, the ``min`` and
    ``max`` the browser enforces, and the limit quoted in an error message. So
    ``min_value`` and ``max_value`` are declared in percent too — 90 rather than 0.9 —
    and only the stored value is a fraction.

    Getting this wrong is not a cosmetic problem. Django copies the bounds straight
    into the HTML ``min`` / ``max`` attributes, so bounds in the wrong unit make a
    perfectly valid entry fail the browser's own validation; and because these inputs
    are hidden whenever the user picked the default values, the browser cannot focus
    the field to report it and silently abandons the submit instead.

    Only the incoming direction is converted here. The outgoing direction is done by
    :meth:`TcoFormBase.initial_from`, because a bound form redisplays the raw string
    the user typed — already a percentage — and converting that again would multiply
    the value by 100 on every failed submit.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("unit", "%")
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return None if value is None else value / 100

    def run_validators(self, value):
        # to_python has already divided by 100, but the validators are declared in
        # percent. Round to shake off the float noise of the round trip, so that
        # entering exactly the boundary value is not rejected by 1e-14.
        super().run_validators(value if value is None else round(value * 100, 9))


class TcoFormBase(forms.Form):
    """Shared behaviour for the TCO parameter forms.

    :cvar PERCENT_FIELDS: Fields displayed as a percentage and stored as a fraction.
    :cvar NESTED: Flat field name to path in the stored dict, for the nested blocks of
        ``ScenarioTCOParams``.
    """

    # Which fields are filled in depends on a radio the browser owns, and the unused
    # ones are hidden rather than removed. An input carrying the HTML "required"
    # attribute inside a hidden container cannot be focused, so the browser refuses to
    # report the error and silently abandons the submit — the click appears to do
    # nothing at all. Validation stays server-side, where it can be skipped when the
    # user asked for the default values.
    use_required_attribute = False

    PERCENT_FIELDS: frozenset = frozenset()
    NESTED: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", TCO_INPUT_CLASS)
            field.widget.attrs.setdefault("step", "any")

    @staticmethod
    def _at(parameters: dict | None, path: tuple):
        """Return the value at ``path`` in a nested dict, or ``None``."""
        value = parameters
        for part in path:
            value = (value or {}).get(part) if isinstance(value, dict) else None
        return value

    @classmethod
    def initial_from(cls, parameters: dict | None, defaults: dict | None = None) -> dict:
        """Build form initials from a stored ``tco_parameters`` dict.

        A key the stored dict does not carry falls back to ``defaults``, the matching
        block of ``defaults/impact/tco.json``. Without that fallback the widget
        renders blank, which is the missing-key problem wearing a different hat: once
        the user has saved the costs page ``ensure_fleet_topology`` stops re-seeding
        their rows, so a key added to ``tco.json`` afterwards would reach them as an
        empty input rather than as its default. Resolving here means the page always
        presents a complete set of values and the user always submits one.

        :param parameters: The stored column, which may be partial.
        :param defaults: The JSON block backing this row. ``None`` disables the
            fallback, which is only right when the values are already resolved.
        """
        initial = {}
        for name in cls.base_fields:
            path = cls.NESTED.get(name, (name,))
            value = cls._at(parameters, path)
            if value is None:
                value = cls._at(defaults, path)
            if value is None:
                continue
            initial[name] = value * 100 if name in cls.PERCENT_FIELDS else value
        return initial

    def mark_defaults(self, baseline: dict | None) -> "TcoFormBase":
        """Stamp each widget with the value a per-field revert restores.

        Written as a ``data-default`` attribute so the browser owns both the "changed"
        marker and the revert button: no round trip, and no second copy of the percent
        scaling, because the value is put through :meth:`initial_from` exactly like the
        one in the input beside it.

        The baseline is whatever the field would hold if the user had not touched it,
        which is not always ``tco.json``. A vehicle type that deviates is compared
        against the shared block instead, since that is what it would inherit.

        :param baseline: The parameters to compare against, in stored units.
        :returns: ``self``, so this can be chained onto the constructor.
        """
        for name, value in self.initial_from(baseline).items():
            self.fields[name].widget.attrs["data-default"] = value
        return self

    def to_tco_parameters(self) -> dict:
        """Return the cleaned data shaped the way eflips-impact reads it."""
        parameters: dict = {}
        for name, value in self.cleaned_data.items():
            path = self.NESTED.get(name, (name,))
            target = parameters
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
        return parameters


class ScenarioTcoForm(TcoFormBase):
    """Scenario-wide financial parameters — ``eflips.impact.tco.ScenarioTCOParams``."""

    PERCENT_FIELDS = frozenset(
        {
            "interest_rate",
            "inflation_rate",
            "eta_avail",
            "cost_escalation_rate_general",
            "cost_escalation_rate_staff",
            "cost_escalation_rate_electricity",
            "cost_escalation_rate_diesel",
            "cost_escalation_rate_insurance",
        }
    )
    NESTED = {
        "fuel_cost_electricity": ("fuel_cost", "electricity"),
        "fuel_cost_diesel": ("fuel_cost", "diesel"),
        "vehicle_maint_cost_electricity": ("vehicle_maint_cost", "electricity"),
        "vehicle_maint_cost_diesel": ("vehicle_maint_cost", "diesel"),
        "cost_escalation_rate_general": ("cost_escalation_rate", "general"),
        "cost_escalation_rate_staff": ("cost_escalation_rate", "staff"),
        "cost_escalation_rate_electricity": ("cost_escalation_rate", "electricity"),
        "cost_escalation_rate_diesel": ("cost_escalation_rate", "diesel"),
        "cost_escalation_rate_insurance": ("cost_escalation_rate", "insurance"),
    }

    project_duration = TcoIntegerField(
        min_value=1,
        unit=gettext_lazy("Jahre"),
        label=gettext_lazy("Zeithorizont"),
        help_text=gettext_lazy(
            "Berechnungsdauer für die TCO. Gesamtsystemkosten und jährliche Kosten "
            "werden innerhalb dieses Zeitraums berechnet"
        ),
    )
    staff_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/h"),
        label=gettext_lazy("Personalkosten"),
        help_text=gettext_lazy(
            "Personalkosten pro Betriebsstunde für ein Fahrzeug. Üblicherweise "
            "Arbeitgeberkosten für die Busfahrer:in, evtl. zzgl. Schaffner:in"
        ),
    )
    fuel_cost_electricity = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/kWh"),
        label=gettext_lazy("Stromkosten"),
        help_text=gettext_lazy("Stromkosten pro kWh inklusive aller Gebühren"),
    )
    fuel_cost_diesel = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/l"),
        label=gettext_lazy("Dieselkosten"),
        help_text=gettext_lazy("Nur relevant, wenn das Szenario Dieselfahrzeuge enthält"),
    )
    vehicle_maint_cost_electricity = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/km"),
        label=gettext_lazy("Wartungskosten E-Fahrzeug"),
        help_text=gettext_lazy("Wartungskosten für ein Elektrofahrzeug, pro Fahrzeugkilometer"),
    )
    vehicle_maint_cost_diesel = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/km"),
        label=gettext_lazy("Wartungskosten Diesel-Fahrzeug"),
        help_text=gettext_lazy("Wartungskosten für ein Dieselfahrzeug, pro Fahrzeugkilometer"),
    )
    infra_maint_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/a"),
        label=gettext_lazy("Wartungskosten Ladeinfrastruktur"),
        help_text=gettext_lazy(
            "Durchschnittliche Wartungskosten für einen Ladepunkt über ein Jahr"
        ),
    )
    insurance = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/a"),
        label=gettext_lazy("Versicherung für Fahrzeug"),
        help_text=gettext_lazy("Durchschnittliche jährliche Versicherungskosten, pro Fahrzeug"),
    )
    taxes = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/a"),
        label=gettext_lazy("Kfz-Steuer"),
        help_text=gettext_lazy("Durchschnittliche Kraftfahrzeugsteuer pro Jahr und Fahrzeug"),
    )
    interest_rate = PercentField(
        label=gettext_lazy("Zinssatz"),
        help_text=gettext_lazy("Zinssatz für Investitionen und Kredite"),
    )
    inflation_rate = PercentField(
        label=gettext_lazy("Inflation"),
        help_text=gettext_lazy("Inflationsrate, Teuerung aller Güter bei gleichbleibendem Wert"),
    )
    eta_avail = PercentField(
        min_value=1,
        max_value=100,
        label=gettext_lazy("Technische Verfügbarkeit"),
        help_text=gettext_lazy(
            "Anteil der Fahrzeuge, der einsatzbereit ist. Die benötigte Flottengröße "
            "wird entsprechend erhöht"
        ),
    )
    # Escalation rates may be negative: battery prices are expected to fall.
    cost_escalation_rate_general = PercentField(
        label=gettext_lazy("Preiseskalation – allgemein"),
        help_text=gettext_lazy(
            "Generelle Preissteigerung pro Jahr. Der Wert ist nicht relativ zur "
            "Inflation, d. h. bei einer Preiseskalation gleich der Inflation bleibt "
            "der Wert konstant"
        ),
    )
    cost_escalation_rate_staff = PercentField(
        label=gettext_lazy("Preiseskalation – Arbeitslohn"),
        help_text=gettext_lazy("Preissteigerung für Arbeitslohn pro Jahr"),
    )
    cost_escalation_rate_electricity = PercentField(
        label=gettext_lazy("Preiseskalation – Strom"),
        help_text=gettext_lazy("Preissteigerung für Stromkosten pro Jahr"),
    )
    cost_escalation_rate_diesel = PercentField(
        label=gettext_lazy("Preiseskalation – Diesel"),
        help_text=gettext_lazy("Preissteigerung für Dieselkosten pro Jahr"),
    )
    cost_escalation_rate_insurance = PercentField(
        label=gettext_lazy("Preiseskalation – Versicherung"),
        help_text=gettext_lazy("Preissteigerung für Versicherung pro Jahr"),
    )


class LifetimeForm(TcoFormBase):
    """The fleet-wide lifetimes, shared by the TCO and the LCA.

    The only place a lifetime is entered. Both calculations need one for a vehicle, a
    battery and a charging point, and a difference between them is never meaningful:
    it would write the same bus off over two different periods. They used to come from
    two separate files and had drifted apart, so these are deliberately not per-type
    fields and not repeated in the cost blocks below.

    Stored under ``ebustoolbox.impact.LIFETIMES_KEY``, not in a ``tco_parameters``
    column of its own — see :func:`ebustoolbox.impact.lifetime_parameters`.
    """

    vehicle = TcoIntegerField(
        min_value=1,
        unit=gettext_lazy("Jahre"),
        label=gettext_lazy("Fahrzeug"),
        help_text=gettext_lazy(
            "Nutzungsdauer eines Fahrzeugs. Bestimmt sowohl die Ersatzbeschaffung in "
            "der Kostenrechnung als auch die Amortisation der Herstellungsemissionen "
            "in der Ökobilanz"
        ),
    )
    battery = TcoIntegerField(
        min_value=1,
        unit=gettext_lazy("Jahre"),
        label=gettext_lazy("Batterie"),
        help_text=gettext_lazy(
            "Nutzungsdauer einer Batterie bis zum Austausch. Gilt für Kostenrechnung "
            "und Ökobilanz gleichermaßen"
        ),
    )
    charging_point = TcoIntegerField(
        min_value=1,
        unit=gettext_lazy("Jahre"),
        label=gettext_lazy("Ladepunkt"),
        help_text=gettext_lazy(
            "Nutzungsdauer eines Ladepunkts, im Depot wie an der Strecke. Gilt für "
            "Kostenrechnung und Ökobilanz gleichermaßen"
        ),
    )


class VehicleTypeLcaForm(TcoFormBase):
    """The editable Ökobilanz values of a vehicle type.

    The environmental counterpart of :class:`VehicleTypeTcoForm`, rendered in the same
    card and governed by the same "abweichen" checkbox, because a fleet is described
    once and then costed and assessed — not configured twice.

    Everything else the LCA needs is an openLCA emission factor from
    ``defaults/impact/lca.json`` and not something a user can sensibly type. The
    exception used to be the lifetimes, which are now on :class:`LifetimeForm`.

    Not a ``tco_parameters`` form despite the base class, which only supplies the
    percent handling and the ``data-default`` marking; see
    :func:`ebustoolbox.impact.vehicle_lca_parameters` for where the values are kept.
    """

    motor_rated_power_kw = TcoFloatField(
        min_value=1,
        unit=gettext_lazy("kW"),
        label=gettext_lazy("Motornennleistung"),
        help_text=gettext_lazy(
            "Nennleistung des Antriebsmotors. Daraus wird die Masse des Motors "
            "abgeleitet und damit dessen Herstellungsemissionen. Geht nicht in die "
            "Kostenrechnung ein"
        ),
    )


class BatteryTypeLcaForm(TcoFormBase):
    """The editable Ökobilanz values of a battery — ``BatteryType.specific_mass``.

    One field, and the only one on this page that is not stored in a JSON column but
    in a column of its own. It is written by
    :func:`ebustoolbox.impact.ensure_lca_parameters` all the same, so that the mass
    used in the calculation is the mass shown here.
    """

    specific_mass = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("kg/kWh"),
        label=gettext_lazy("Spezifische Batteriemasse"),
        help_text=gettext_lazy(
            "Masse der Batterie je kWh Bruttokapazität. Mal der Batteriekapazität "
            "ergibt sich die Masse der Batterie, aus der ihre Herstellungsemissionen "
            "berechnet werden. Geht nicht in die Kostenrechnung ein"
        ),
    )


class ElectricityLcaForm(TcoFormBase):
    """The emission factors of the electricity the fleet charges with.

    One impact vector over the eight EF 3.1 categories, per kWh drawn at the grid
    connection — the losses down to the battery are eflips-impact's, from the two
    charging efficiencies and the vehicle type's own. The only input on this page that
    describes the surroundings rather than the fleet, which is why it is scenario-wide
    and has no per-vehicle-type counterpart.

    All eight are editable rather than only the CO2 one. The result page plots every
    category from the same selector, so a scenario whose greenhouse gas figure came
    from the operator's own supply contract while the other seven still described the
    shipped German grid mix would be reporting two different power stations in two
    tabs of one chart.

    ``lca.json`` carries these per year, because the study they come from projected a
    grid mix to 2050. eflips-impact samples that series exactly once — see
    :func:`ebustoolbox.impact.analysis_year` — so what a scenario needs is the single
    vector, and no year is asked for here.
    """

    gwp = TcoFloatField(
        unit=gettext_lazy("kg CO₂-Äq/kWh"),
        label=gettext_lazy("Treibhauspotenzial (GWP)"),
        help_text=gettext_lazy(
            "Treibhausgasemissionen je Kilowattstunde am Netzanschluss, über 100 "
            "Jahre gewichtet. Der Standardwert ist der deutsche Strommix; ein "
            "Ökostromvertrag oder eine eigene Erzeugung liegt deutlich darunter"
        ),
    )
    pm = TcoFloatField(
        unit=gettext_lazy("kg PM2,5-Äq/kWh"),
        label=gettext_lazy("Feinstaub"),
        help_text=gettext_lazy("Feinstaubbildung je Kilowattstunde am Netzanschluss"),
    )
    pocp = TcoFloatField(
        unit=gettext_lazy("kg NOx-Äq/kWh"),
        label=gettext_lazy("Sommersmog (POCP)"),
        help_text=gettext_lazy("Bildung von bodennahem Ozon je Kilowattstunde am Netzanschluss"),
    )
    ap = TcoFloatField(
        unit=gettext_lazy("kg SO₂-Äq/kWh"),
        label=gettext_lazy("Versauerung"),
        help_text=gettext_lazy("Versauerungspotenzial je Kilowattstunde am Netzanschluss"),
    )
    ep_freshwater = TcoFloatField(
        unit=gettext_lazy("kg P-Äq/kWh"),
        label=gettext_lazy("Eutrophierung – Süßwasser"),
        help_text=gettext_lazy("Überdüngung von Süßgewässern je Kilowattstunde am Netzanschluss"),
    )
    ep_marine = TcoFloatField(
        unit=gettext_lazy("kg N-Äq/kWh"),
        label=gettext_lazy("Eutrophierung – Meer"),
        help_text=gettext_lazy("Überdüngung von Meeren je Kilowattstunde am Netzanschluss"),
    )
    fuel = TcoFloatField(
        unit=gettext_lazy("kg Öl-Äq/kWh"),
        label=gettext_lazy("Fossiler Ressourcenbedarf"),
        help_text=gettext_lazy(
            "Verbrauch fossiler Energieträger je Kilowattstunde am Netzanschluss"
        ),
    )
    water = TcoFloatField(
        unit=gettext_lazy("m³/kWh"),
        label=gettext_lazy("Wasserverbrauch"),
        help_text=gettext_lazy("Wasserverbrauch je Kilowattstunde am Netzanschluss"),
    )


class VehicleTypeTcoForm(TcoFormBase):
    """Per-vehicle-type parameters — ``VehicleType.tco_parameters``.

    ``average_electricity_consumption`` is deliberately not a field here even though
    eflips-impact reads one. It is a result rather than an input: the toolchain
    measures it on the DEFAULT scenario and
    :func:`ebustoolbox.impact._write_energy_consumption` writes it over whatever is
    stored, so an input would have been discarded on every run. Diesel keeps its
    field below, because nothing simulates diesel consumption.

    ``useful_life`` is not a field here either, for a different reason: it is
    fleet-wide and shared with the LCA, so it belongs to :class:`LifetimeForm`.
    """

    PERCENT_FIELDS = frozenset({"cost_escalation"})

    procurement_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€"),
        label=gettext_lazy("Kaufpreis"),
        help_text=gettext_lazy(
            "Durchschnittlicher Kaufpreis für ein Fahrzeug dieses Typs. Bitte geben "
            "Sie einen Wert zu Beginn des Zeithorizonts an, für Ersatzkäufe wird der "
            "Preis automatisch entsprechend der Preiseskalation angepasst"
        ),
    )
    cost_escalation = PercentField(
        label=gettext_lazy("Preiseskalation Fahrzeug"),
        help_text=gettext_lazy(
            "Preissteigerung für Fahrzeuge pro Jahr. Dient der Anpassung der Preise "
            "für Ersatzfahrzeuge im Betrachtungszeitraum"
        ),
    )


class DieselVehicleTypeTcoForm(VehicleTypeTcoForm):
    """Same as :class:`VehicleTypeTcoForm` but for diesel vehicle types."""

    average_diesel_consumption = TcoFloatField(
        min_value=0,
        required=False,
        unit=gettext_lazy("l/km"),
        label=gettext_lazy("Durchschnittsverbrauch"),
        help_text=gettext_lazy("Durchschnittlicher Dieselverbrauch pro Fahrzeugkilometer"),
    )


class BatteryTypeTcoForm(TcoFormBase):
    """Per-battery parameters — ``BatteryType.tco_parameters``.

    The lifetime is on :class:`LifetimeForm`, which the LCA reads too.
    """

    PERCENT_FIELDS = frozenset({"cost_escalation"})

    procurement_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€/kWh"),
        label=gettext_lazy("Kaufpreis Batterie"),
        help_text=gettext_lazy(
            "Durchschnittlicher Kaufpreis für eine Batterie dieses Typs, angegeben "
            "pro kWh. Wenn unbekannt, Fahrzeugkaufpreis inkl. Batterie angeben und "
            "hier 0 (dann kann ein Batterietausch aber nicht abgebildet werden)"
        ),
    )
    cost_escalation = PercentField(
        label=gettext_lazy("Preiseskalation Batterie"),
        help_text=gettext_lazy(
            "Preissteigerung für Batterien pro Jahr. Negative Werte bilden fallende "
            "Batteriepreise ab"
        ),
    )


class ChargingPointTypeTcoForm(TcoFormBase):
    """Per-charging-point parameters — ``ChargingPointType.tco_parameters``.

    The lifetime is on :class:`LifetimeForm`, which the LCA reads too.
    """

    PERCENT_FIELDS = frozenset({"cost_escalation"})

    procurement_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€"),
        label=gettext_lazy("Kaufpreis"),
        help_text=gettext_lazy(
            "Kaufpreis für einen Ladepunkt, inklusive anteiliger Leistungselektronik "
            "und anteiligem Netzanschluss. Bitte geben Sie einen Wert zu Beginn des "
            "Zeithorizonts an, für Ersatzkäufe wird der Preis automatisch "
            "entsprechend der Preiseskalation angepasst"
        ),
    )
    cost_escalation = PercentField(
        label=gettext_lazy("Preiseskalation"),
        help_text=gettext_lazy(
            "Preissteigerung für Ladepunkte pro Jahr, nicht relativ zur Inflation"
        ),
    )


class ChargingInfrastructureTcoForm(TcoFormBase):
    """Per-site build costs — ``Station.tco_parameters``.

    Abstract in practice: the depot and the on-route subclasses below describe
    different things and say so, so instantiate one of those rather than this.

    There is no lifetime field. The site's ``useful_life`` is still stored and still
    read by eflips-impact, but it is not editable: unlike the vehicle, battery and
    charging point lifetimes it has no LCA counterpart to stay consistent with, so it
    is neither part of :class:`LifetimeForm` nor duplicated here. It stays at the
    value in ``defaults/impact/tco.json``.
    """

    PERCENT_FIELDS = frozenset({"cost_escalation"})

    procurement_cost = TcoFloatField(
        min_value=0, unit=gettext_lazy("€"), label=gettext_lazy("Baukosten")
    )
    cost_escalation = PercentField(label=gettext_lazy("Preiseskalation"))


class DepotInfrastructureTcoForm(ChargingInfrastructureTcoForm):
    """Build costs of a depot site, without its charging points."""

    procurement_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€"),
        label=gettext_lazy("Baukosten"),
        help_text=gettext_lazy(
            "Kosten für den Bau eines Depotstandorts, ohne die einzelnen Ladepunkte"
        ),
    )
    cost_escalation = PercentField(
        label=gettext_lazy("Preiseskalation"),
        help_text=gettext_lazy("Preissteigerung für Depotstandorte pro Jahr"),
    )


class StationInfrastructureTcoForm(ChargingInfrastructureTcoForm):
    """Build costs of an on-route charging site, without its charging points."""

    procurement_cost = TcoFloatField(
        min_value=0,
        unit=gettext_lazy("€"),
        label=gettext_lazy("Baukosten"),
        help_text=gettext_lazy(
            "Kosten für den Bau eines Ladestandorts an der Strecke, ohne die "
            "einzelnen Ladepunkte"
        ),
    )
    cost_escalation = PercentField(
        label=gettext_lazy("Preiseskalation"),
        help_text=gettext_lazy("Preissteigerung für Ladestandorte pro Jahr"),
    )


# Which subclass backs which key of the scenario's charging_infrastructure block.
CHARGING_INFRASTRUCTURE_FORMS = {
    "depot": DepotInfrastructureTcoForm,
    "station": StationInfrastructureTcoForm,
}


class DepotConfigurationWishForm(forms.ModelForm):
    """All inputs which are given once per depot"""

    class Meta:
        model = models.DepotConfigurationWish
        exclude = ["scenario", "station"]
        help_texts = {
            "auto_generate": _(
                "Ein Algorithmus bestimmt die benötigte Größe des Depots, "
                "sowie technische Parameter, automatisch für Sie."
            ),
            "default_power": _("max. Ladeleistung pro Ladepunkt"),
            "standard_block_length": _("Länge des Blocks"),
            "cleaning_slots": _("Anzahl der Plätze für gleichzeitige Reinigung"),
            "shunting_slots": _("Anzahl an Rangierplätzen"),
            "cleaning_duration": _("Dauer der Reinigung in Minuten"),
            "shunting_duration": _("Dauer des Rangierens in Minuten"),
        }

        labels = {
            "auto_generate": _("Automatische Berechnung"),
            "default_power": _("Standard Ladeleistung"),
            "standard_block_length": _("Standard Blocklänge"),
            "cleaning_slots": _("Reinigungsplätze"),
            "shunting_slots": _("Rangierkapazität"),
            "cleaning_duration": _("Reinigungsdauer"),
            "shunting_duration": _("Rangierdauer"),
        }

    # Custom mapping used to add unit to field in __init__
    units = {
        "default_power": _("kW"),
        "standard_block_length": _("[-]"),
        "cleaning_slots": _("[-]"),
        "shunting_slots": _("[-]"),
        "cleaning_duration": _("Minuten"),
        "shunting_duration": _("Minuten"),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.unit = self.units.get(name)

        auto_generate = True
        if self.instance:
            auto_generate = self.instance.auto_generate

        if auto_generate:
            self.fields["default_power"].widget.attrs.update({"min": 1.0, "required": True})
            self.fields["standard_block_length"].widget.attrs.update({"min": 1.0, "required": True})
        else:
            self.fields["cleaning_slots"].widget.attrs.update({"min": 1.0, "required": True})
            self.fields["shunting_slots"].widget.attrs.update({"min": 1.0, "required": True})
            self.fields["cleaning_duration"].widget.attrs.update({"min": 1.0, "required": True})
            self.fields["shunting_duration"].widget.attrs.update({"min": 1.0, "required": True})

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("auto_generate"):
            if not cleaned_data.get("default_power"):
                raise ValidationError(
                    "If auto generate is true, the DepotConfigurationWish needs a power"
                )
            if (
                cleaned_data.get("cleaning_slots")
                or cleaned_data.get("shunting_slots")
                or cleaned_data.get("cleaning_duration")
                or cleaned_data.get("shunting_duration")
            ):
                raise ValidationError("More data then expected")

            cleaned_data["cleaning_slots"] = None
            cleaned_data["shunting_slots"] = None
            cleaned_data["cleaning_duration"] = None
            cleaned_data["shunting_duration"] = None

        else:
            if cleaned_data.get("default_power"):
                raise ValidationError(
                    "If auto generate is false, the DepotConfigurationWish must not have power"
                )
            if cleaned_data.get("standard_block_length"):
                raise ValidationError(
                    "If auto generate is false, "
                    "the DepotConfigurationWish must not have a standard block length"
                )
            if (
                not cleaned_data.get("cleaning_slots")
                or not cleaned_data.get("shunting_slots")
                or not cleaned_data.get("cleaning_duration")
                or not cleaned_data.get("shunting_duration")
            ):
                raise ValidationError("Missing Data")
            cleaned_data["default_power"] = None
            cleaned_data["standard_block_length"] = None
        return cleaned_data


class AreaInformationForm(forms.ModelForm):
    """All inputs which can be given multiple times per depot, e.g, multiple
    charging areas with various numbers of chargers and charging powers"""

    class Meta:
        model = models.AreaInformation
        exclude = ["scenario", "depot_configuration_wish", "vehicle_type"]
        help_texts = {
            "capacity": _("Anzahl der Ladeplätze für diesen Fahrzeugtyp. Mindestanzahl = 2"),
            "power": _("max. Ladeleistung der Ladesäule"),
            "block_length": _(
                "Anzahl hintereinanderliegender Parkplätze. Dies muss ein ganzzahliger Teiler der Kapazität sein."
            ),
            "area_type": _("Form in der die Ladeplätze angelegt sind"),
        }
        labels = {
            "capacity": _("Kapazität"),
            "power": _("Leistung"),
            "block_length": _("Blocklänge"),
            "area_type": _("Anordnung"),
        }
        units = {"capacity": _("-")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["capacity"].widget.attrs.update({"min": 2.0, "max": 10_000, "required": True})
        self.fields["power"].widget.attrs.update({"min": 1.0, "required": True})
        self.fields["area_type"].required = True
        self.fields["block_length"].widget.attrs.update({"min": 2.0, "required": True})

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("area_type") == AreaType.LINEAR:
            if cleaned_data.get("capacity") is None:
                self.add_error(
                    "capacity", _("Für diesen Flächentyp muss ein Kapazität >=2 angegeben werden")
                )
                raise ValidationError("Block length cant be None")

            if cleaned_data.get("capacity") % cleaned_data.get("block_length") != 0:
                self.add_error(
                    "block_length",
                    _(
                        "Die Anzahl muss ein ganzahliger Teiler der Ladeplätze Anzahl sein "
                        "und größer oder gleich 2 sein."
                    ),
                )
                raise ValidationError("Block length must be an integer divider of Capacity")
        return cleaned_data
