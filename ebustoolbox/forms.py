from django import forms
from .models import Vehicle, EnumChargeType, EnumVoltageLevel


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
    input_schedule = forms.FileField(required=False)
    electrified_stations = forms.FileField(required=False)
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
    cost_parameters_file = forms.FileField(required=False)
    optimizer_config = forms.CharField(required=False)

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


class EbusToolboxForm(forms.Form):
    title = forms.CharField(max_length=50)
    file = forms.FileField()


class ChartForm(forms.Form):
    vehicles = forms.ModelMultipleChoiceField(queryset=Vehicle.objects.all())

    def __init__(self, *args, **kwargs):
        scenario = kwargs.pop("scenario", None)
        super().__init__(*args, **kwargs)
        if scenario:
            self.fields["vehicles"].queryset = Vehicle.objects.filter(
                vehicle_type__scenario=scenario
            )


class ChargingStationDefaultsForm(forms.Form):
    gc_power_opps = forms.IntegerField(
        min_value=0, max_value=1000000, initial=5000, label="Grid connector power in kVA"
    )
    cs_power_opps = forms.IntegerField(
        min_value=0, max_value=10000, initial=300, label="Charging point power in kW"
    )
    amount_charging_places = forms.IntegerField(
        min_value=0,
        max_value=1000,
        initial=2,
        label="Number of charging points per electrified station",
    )
    station_optimization = forms.BooleanField(initial=False, required=False)
