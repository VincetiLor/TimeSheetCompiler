from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from allauth.socialaccount.models import SocialApp, SocialToken
import requests
from calendars.views import get_token_valido
# Create your views here.


def classificator_settings(request):

    try:
        token = get_token_valido(request.user)
    except:
        return redirect(f"{reverse('google_login')}?next={request.path}")
    

    return render(request, 'classifyEvents/settings.html')