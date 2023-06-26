from django import forms
from .models import EbusToolbox, Vehicle




class UploadFileForm(forms.ModelForm):
    # modes = forms.MultipleChoiceField(
    #     # choices=((1, '1'), (2, '2'), (3, '3')),
    #     # widget=forms.CheckboxSelectMultiple
    # )
    class Meta:
        model = EbusToolbox
        # __all__ for all model elements
        fields = '__all__'
        exclude = ['output_directory', 'task_id']
        help_texts = {
                'input_schedule': 'Schedule which describes rotations by defining consecutive '
                                  'trips, with one row per trip and the columns x,y and z as '
                                  'comma seperated file (.csv)'
                                  '',
            }


class UploadFileForm_(forms.Form):
    title = forms.CharField(max_length=50)
    file = forms.FileField()

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