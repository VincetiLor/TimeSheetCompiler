from django import forms

from .hours import category_priority, complete_priority
from .models import Event, TimesheetHourCap


# An empty field means no cap
class TimesheetSettingsForm(forms.Form):

    # 'campo' is defined in tailwind/input.css
    all_day_hours = forms.IntegerField(
        min_value=0, max_value=8,
        widget=forms.NumberInput(attrs={'class': 'campo'}))
    total_hours_cap = forms.IntegerField(
        min_value=0, required=False,
        widget=forms.NumberInput(attrs={'class': 'campo'}))
    # Category codes separated by commas, highest priority first
    category_priority = forms.CharField(widget=forms.HiddenInput)

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
            'category_priority': ','.join(category_priority(timesheet)),
        }
        for cap in timesheet.hour_caps.all():
            initial['cap_' + cap.category] = cap.max_hours
        return initial

    def clean_category_priority(self):
        codes = self.cleaned_data['category_priority'].split(',')
        if sorted(codes) != sorted(Event.Category.values):
            raise forms.ValidationError("Ordine delle categorie non valido.")
        return codes

    def cap_fields(self):
        # [(category, field), ...] in priority order, also on a form with errors
        value = self['category_priority'].value() or ''
        fields = []
        for category in complete_priority(value.split(',')):
            fields.append((category, self['cap_' + category]))
        return fields

    def save(self, timesheet):
        data = self.cleaned_data
        timesheet.all_day_hours = data['all_day_hours']
        timesheet.total_hours_cap = data['total_hours_cap']
        timesheet.category_priority = data['category_priority']
        timesheet.save(update_fields=['all_day_hours', 'total_hours_cap', 'category_priority'])

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
