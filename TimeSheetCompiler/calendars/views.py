from django.shortcuts import redirect, render 
from django.views import View 
from allauth.socialaccount.models import SocialApp, SocialToken
import requests
from django.views.generic import ListView

def get_token_valido(user):
    token = SocialToken.objects.get(account__user=user, account__provider='google')
    test = requests.get(
        'https://www.googleapis.com/calendar.readonly',
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
                'refresh_token': token.token,
                'grant_type': 'refresh_token',
            }
        )
        if post.status_code == 200:
            token.token = post.json().get('access_token')
            token.save()
        else:
            raise Exception("Impossibile rinnovare il token")               
    return token.token
# Create your views here

class SelectCalendarsView(ListView):
    model = SocialToken
    template_name = 'calendars/eventi_calendars.html'
    context_object_name = 'calendars'

    def get_queryset(self):
        try:
            token = get_token_valido(self.request.user)
        except:
            return redirect('authentication:index')

        response = requests.get(
            'https://www.googleapis.com/calendar/v3/users/me/calendarList',
            headers = {'Authorization': f'Bearer {token}'},
            params={'maxResults': 10}
        )
        calendar_list = response.json().get('items', ['no calendars found'])
        calendars = []
        for calendar in calendar_list:
            calendars.append(calendar['summary'])
        #print(calendar_list)
        return calendars
    
def event_compare(request):
    return render(request, 'calendars/event_compare.html')
    