from django.shortcuts import redirect, render 
from django.views import View 
from allauth.socialaccount.models import SocialApp, SocialToken
from requests import get    
from googleapiclient.discovery import build
import requests

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

def eventi_calendar(request):
    try:
        token = get_token_valido(request.user)
    except:
        return redirect('authentication:index')

    response = requests.get(
        'https://www.googleapis.com/calendar/v3/users/me/calendarList',
        headers = {'Authorization': f'Bearer {token}'},
        params={'maxResults': 10}
    )


    calendar_list = response.json().get('items', ['no calendars found'])

    return render(request, 'calendars/eventi_calendars.html', {'calendars': calendar_list})