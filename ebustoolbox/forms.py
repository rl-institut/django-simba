from django import forms
from .models import Vehicle, BusStop

class UploadFileForm(forms.Form):
    # basics
    title = forms.CharField(max_length=50, initial="SimBA")
    # task_id = forms.CharField(widget=forms.HiddenInput, required=False)
    preferred_charging_type = forms.CharField(
        widget=forms.RadioSelect(choices=BusStop.CHARGE_TYPES), initial=BusStop.CHARGE_TYPES[0][0])
    modes = forms.CharField(widget=forms.HiddenInput, initial="sim,report")

    # charging infrastructure
    gc_power_opps = forms.DecimalField(max_digits=10, decimal_places=2, initial=1e5)
    gc_power_deps = forms.DecimalField(max_digits=10, decimal_places=2, initial=1e5)
    cs_power_opps = forms.DecimalField(max_digits=10, decimal_places=2, initial=300)
    cs_power_deps_depb = forms.DecimalField(max_digits=10, decimal_places=2, initial=150)
    cs_power_deps_oppb = forms.DecimalField(max_digits=10, decimal_places=2, initial=150)
    default_voltage_level = forms.CharField(
        widget=forms.RadioSelect(choices=[(c, c) for c in BusStop.VOLTAGE_LEVEL_CHOICES]),
        initial="MV")

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
    vehicle_types = forms.FileField(required=False)
    station_data_path = forms.FileField(required=False)
    outside_temperature_over_day_path = forms.FileField(required=False)
    level_of_loading_over_day_path = forms.FileField(required=False)
    cost_parameters_file = forms.FileField(required=False)

    # extended options
    strategy = forms.CharField(widget=forms.Select(choices=[('distributed', 'distributed')]),
                               initial='distributed')
    interval = forms.DecimalField(initial=15)
    signal_time_dif = forms.DecimalField(initial=10)
    days = forms.IntegerField(required=False)
    include_price_csv = forms.FileField(required=False)
    seed = forms.CharField(required=False)
    cost_calculation = forms.BooleanField(initial=False, required=False)


# class UploadFileForm(forms.ModelForm):
#     # modes = forms.MultipleChoiceField(
#     #     # choices=((1, '1'), (2, '2'), (3, '3')),
#     #     # widget=forms.CheckboxSelectMultiple
#     # )
#     class Meta:
#         model = EbusToolbox
#         # __all__ for all model elements
#         fields = '__all__'
#         exclude = ['output_directory', 'task_id']
#         help_texts = {
#                 'input_schedule': 'Schedule which describes rotations by defining consecutive '
#                                   'trips, with one row per trip and the columns x,y and z as '
#                                   'comma seperated file (.csv)'
#                                   '',
#             }
#
#
# class UploadFileForm_(forms.Form):
#     title = forms.CharField(max_length=50)
#     file = forms.FileField()

class EbusToolboxForm(forms.Form):
    title = forms.CharField(max_length=50)
    file = forms.FileField()


class ChartForm(forms.Form):
    vehicles = forms.ModelMultipleChoiceField(queryset=Vehicle.objects.all())

    def __init__(self, *args, **kwargs):
        scenario = kwargs.pop('scenario', None)
        super().__init__(*args, **kwargs)
        if scenario:
            self.fields['vehicles'].queryset = Vehicle.objects.filter(scenario=scenario)
