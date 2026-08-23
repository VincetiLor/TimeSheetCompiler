from django.shortcuts import redirect
from django.urls import reverse
import requests
from django.views.generic import TemplateView

from authentication.services import get_token_valido


class SelectCalendarsView(TemplateView):
    template_name = 'calendars/eventi_calendars.html'
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
    