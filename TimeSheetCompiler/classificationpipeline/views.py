from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import TemplateView
import requests

from authentication.services import get_token_valido


class SelectCalendarsView(TemplateView):
    template_name = 'classificationpipeline/eventi_calendars.html'
    context_object_name = 'calendars'

    def dispatch(self, request, *args, **kwargs):
        try:
            self.token = get_token_valido(self.request.user)
        except:
             return redirect(f"{reverse('avvia_login_google')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        response = requests.get(
            'https://www.googleapis.com/calendar/v3/users/me/calendarList',
            headers = {'Authorization': f'Bearer {self.token}'},
            params={'maxResults': 10}
        )
        calendar_list = response.json().get('items', [])
        calendars = {}
        for calendar in calendar_list:
            calendars[calendar['summary']] = calendar['id']

        print(calendars)
        context['calendars'] = calendars
        return context


def event_compare(request):
    if request.method == 'POST':
        calendar_ids = request.POST.getlist('calendars')
        print(calendar_ids)
        return render(request, 'classificationpipeline/event_compare.html', {'calendar_ids': calendar_ids})
    else:
        return redirect('classificationpipeline:eventi_calendars')
