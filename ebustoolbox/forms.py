from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from . import models, tasks
from .models import (
    EnumChargeType,
    EnumVoltageLevel,
    ElectrificationOptions,
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
        required=False, help_text="Verknüpft SimBA-Trips mit Temperaturen"
    )
    consumption_path = forms.FileField(
        required=False, help_text="Zur Interpolation von Verbräuchen verwendet"
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
    temperature = forms.IntegerField(min_value=-20, max_value=40)

    class Meta:
        model = SimulationRange
        exclude = ("scenario",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if not (cleaned_data.get("start") and cleaned_data.get("end")):
            raise ValidationError("Gib ein Start- und Endzeitpunkt an.")
        if (
            tasks.get_rotations_by_start_end(
                self.instance.scenario.parent, cleaned_data["start"], cleaned_data["end"]
            ).count()
            == 0
        ):
            raise ValidationError("In dieser Zeitspanne starten keine Umläufe.")
        return cleaned_data


class ElectrificationOptionsForm(forms.ModelForm):
    class Meta:
        model = ElectrificationOptions
        exclude = ("scenario", "electrified_stations")
        help_texts = {
            "gc_power_opps": "Grid connector power in kVA",
            "cs_power_opps": "Charging point power in kW",
            "amount_charging_places": "Number of charging points per electrified station",
        }


class TripsForm(forms.Form):
    data_file = forms.FileField(required=False)
    existing_scenario = forms.UUIDField(required=False)
    scenario_name = forms.CharField(max_length=100)
    description = forms.CharField(max_length=100, required=False)

    # TODO use clean method instead
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
        exclude = ["vehicle_type"]
        model = models.VehicleTypeSelection

    def __init__(self, *args, vehicle_type=None, choices_queryset=None, **kwargs):
        super(VehicleTypeSelectionForm, self).__init__(*args, **kwargs)
        self.fields["default_vehicle_type"].queryset = choices_queryset


class VehicleTypeForm(forms.ModelForm):
    # Consumption must be turned on in front end -> todo discuss
    class Meta:
        model = VehicleType
        fields = ["battery_capacity", "consumption"]

        help_texts = {
            "battery_capacity": "Hier können Sie die gewünschte Batteriekapazität des Fahrzeugtyps anpassen.",
            "consumption": "Welchen Verbrauch in kWh/km hat dieses Fahrzeug?",
        }
        labels = {
            "battery_capacity": "Batteriekapazität [kWh]",
            "consumption": "Verbrauch [kWh/km]",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["battery_capacity"].widget.attrs.update({"min": 1.0})
        self.fields["consumption"].required = False


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
    co2_emissions = forms.FloatField(required=True)
    energy_consumption = forms.FloatField(required=True)
    resources_consumption = forms.FloatField(required=True)
    years = forms.IntegerField(required=True)


class ManualLcaForm(forms.Form):
    co2_emissions = forms.FloatField(required=True)
    energy_consumption = forms.FloatField(required=True)
    resources_consumption = forms.FloatField(required=True)
    years = forms.IntegerField(required=True)


class DepotInfoForm(forms.ModelForm):
    """All inputs which are given once per depot"""

    class Meta:
        model = models.Station
        fields = ["power_total"]


class DepotChargingAreaForm(forms.Form):
    """All inputs which can be given multiple times per depot, eg, multiple
    charging areas with various numbers of chargers and charging powers"""

    amount_charging_places = forms.IntegerField(required=True, initial=20)
    power_per_charger = forms.IntegerField(required=True, initial=150)
