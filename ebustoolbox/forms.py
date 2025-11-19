from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from . import models, tasks
from .models import (
    AreaType,
    EnumChargeType,
    EnumVoltageLevel,
    VehicleType,
    SimulationRange,
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


class DateRangeField(forms.DateField):
    def to_python(self, value):
        values = value.split(" - ")
        from_date = super(DateRangeField, self).to_python(values[0])
        to_date = super(DateRangeField, self).to_python(values[1])
        return from_date, to_date


class SimulationParameters(forms.ModelForm):
    temperature_average = forms.IntegerField(min_value=-20, max_value=40)
    temperature_extreme = forms.IntegerField(min_value=-20, max_value=40)

    class Meta:
        model = SimulationRange
        exclude = ("scenario",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if not (cleaned_data.get("start") and cleaned_data.get("end")):
            raise ValidationError(_("Gib ein Start- und Endzeitpunkt an."))
        if (
            tasks.get_rotations_by_start_end(
                self.instance.scenario.parent, cleaned_data["start"], cleaned_data["end"]
            ).count()
            == 0
        ):
            raise ValidationError(_("In dieser Zeitspanne starten keine Umläufe."))
        return cleaned_data


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
        ("file_upload", "Datei hochladen"),
        ("reference_scenario", "Werte aus anderem Szenario übernehmen"),
        ("manual", "Manuelle Eingabe"),
    ]
    input_mode = forms.ChoiceField(
        widget=forms.RadioSelect,
        choices=CHOICES,
    )


class FileUploadForm(forms.Form):
    file = forms.FileField(required=True)


class ScenarioSelection(forms.Form):
    scenario = forms.ModelChoiceField(queryset=Scenario.objects.all())

    def __init__(self, *args, queryset, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scenario"].queryset = queryset


class ManualTcoForm(forms.Form):
    project_duration = forms.IntegerField(initial=20, min_value=1)  # years
    staff_cost = forms.FloatField(initial=30, min_value=0)  # €/h
    energy_cost = forms.FloatField(initial=0.18, min_value=0)  # €/kWh
    maint_cost = forms.FloatField(initial=0.07, min_value=0)  # €/km
    maint_inf_cost = forms.FloatField(initial=1000, min_value=0)  # €/a
    useful_life_bus = forms.IntegerField(initial=14, min_value=0)  # years
    procurement_cost_bus = forms.IntegerField(initial=550000, min_value=0)  # €
    useful_life_battery = forms.IntegerField(initial=7, min_value=0)  # years
    procurement_cost_battery = forms.FloatField(initial=0, min_value=0)  # €/kWh
    useful_life_chargepoint_depot = forms.IntegerField(initial=7, min_value=0)  # years
    procurement_cost_chargepoint_depot = forms.FloatField(initial=0, min_value=0)  # €
    useful_life_chargepoint_opp = forms.IntegerField(initial=7, min_value=0)  # years
    procurement_cost_chargepoint_opp = forms.FloatField(initial=0, min_value=0)  # €

    # diesel comparison: non-changeable
    fuel_cost = forms.FloatField(initial=1.5, disabled=True)  # €/l
    maint_cost_diesel = forms.FloatField(initial=0.14, disabled=True)  # €/km
    procurement_cost_diesel = forms.IntegerField(initial=250000, disabled=True)  # €

    # expert options (optional)
    interest_rate = forms.FloatField(initial=4, min_value=0, required=False)
    inflation_rate = forms.FloatField(initial=2, min_value=0, required=False)
    taxes = forms.FloatField(initial=0, min_value=0, required=False)  # € (vehicle tax)
    insurance = forms.FloatField(initial=2000, min_value=0, required=False)  # €/a
    pef_general = forms.FloatField(initial=2, min_value=0, required=False)
    pef_staff_cost = forms.FloatField(initial=2, min_value=0, required=False)
    pef_energy_cost = forms.FloatField(initial=2, min_value=0, required=False)
    pef_insurance = forms.FloatField(initial=2, min_value=0, required=False)
    cost_escalation_bus = forms.FloatField(initial=2, min_value=0, required=False)
    cost_escalation_battery = forms.FloatField(initial=1, min_value=0, required=False)
    cost_escalation_chargepoint = forms.FloatField(initial=2, min_value=0, required=False)


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
            "standard_block_length": _("Charging point power in kW"),
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
            "capacity": _("Anzahl der Ladeplätze für diesen Fahrzeugtyp"),
            "power": _("max. Ladeleistung der Ladesäule"),
            "block_length": _("Anzahl hintereinanderliegender Parkplätze"),
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
        self.fields["capacity"].widget.attrs.update({"min": 1.0, "max": 10_000, "required": True})
        self.fields["power"].widget.attrs.update({"min": 1.0, "required": True})
        self.fields["block_length"].widget.attrs.update({"min": 1.0, "required": True})

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("area_type") == AreaType.LINEAR:
            if cleaned_data.get("capacity") is None:
                self.errors["capacity"].append(
                    _("Für diesen Flächentyp muss ein Kapazität angegeben werden")
                )
                raise ValidationError("Block length cant be None")

            if cleaned_data.get("capacity") % cleaned_data.get("block_length") != 0:
                self.errors["block_length"].append(
                    _("Die Anzahl muss ein ganzahliger Teiler der Ladeplätze Anzahl sein.")
                )
                raise ValidationError("Block length must be an integer divider of Capacity")
        return cleaned_data
