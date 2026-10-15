from django import forms

from .models import Event, TimesheetHourCap


# An empty field means no cap
class TimesheetSettingsForm(forms.Form):

    # 'campo' is defined in tailwind/input.css
    all_day_hours = forms.IntegerField(
        min_value=0, max_value=24,
        widget=forms.NumberInput(attrs={'class': 'campo'}))
    total_hours_cap = forms.IntegerField(
        min_value=0, required=False,
        widget=forms.NumberInput(attrs={'class': 'campo'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for category, label in Event.Category.choices:
            self.fields['cap_' + category] = forms.IntegerField(
                label=label, min_value=0, required=False,
                widget=forms.NumberInput(attrs={'class': 'campo'}))

    @staticmethod
    def initial_for(timesheet):
        initial = {
            'all_day_hours': timesheet.all_day_hours,
            'total_hours_cap': timesheet.total_hours_cap,
        }
        for cap in timesheet.hour_caps.all():
            initial['cap_' + cap.category] = cap.max_hours
        return initial

    def cap_fields(self):
        fields = []
        for category in Event.Category.values:
            fields.append(self['cap_' + category])
        return fields

    def save(self, timesheet):
        data = self.cleaned_data
        timesheet.all_day_hours = data['all_day_hours']
        timesheet.total_hours_cap = data['total_hours_cap']
        timesheet.save(update_fields=['all_day_hours', 'total_hours_cap'])

        for category in Event.Category.values:
            max_hours = data['cap_' + category]
            if max_hours is None:
                timesheet.hour_caps.filter(category=category).delete()
            else:
                TimesheetHourCap.objects.update_or_create(
                    timesheet=timesheet,
                    category=category,
                    defaults={'max_hours': max_hours},
                )
