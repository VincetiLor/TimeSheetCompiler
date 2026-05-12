from django.shortcuts import redirect, render 
from django.urls import reverse
from django.views import View 
from allauth.socialaccount.models import SocialApp, SocialToken
import requests
from django.views.generic import TemplateView

def get_token_valido(user):
    token = SocialToken.objects.get(account__user=user, account__provider='google')
    test = requests.get(
        'https://www.googleapis.com/calendar/v3/users/me/calendarList',
        headers={'Authorization': f'Bearer {token.token}'},
        params={'maxResults': 1}
    )
    if test.status_code == 401:
        app = SocialApp.objects.get(provider='google')
        post = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': app.client_id,
                'client_secret': app.secret,
                'refresh_token': token.token_secret,
                'grant_type': 'refresh_token',
            }
        )
        if post.status_code == 200:
            token.token = post.json().get('access_token')
            token.save()
        else:
            raise Exception("Impossibile rinnovare il token")               
    return token.token


def avvia_login_google(request):
    next_url = request.GET.get('next', '/')
    return render(request, 'auto_login_google.html', {'next': next_url})

class SelectCalendarsView(TemplateView):
    model = SocialToken
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
    