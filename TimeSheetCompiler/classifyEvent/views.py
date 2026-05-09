from django import dispatch
from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from allauth.socialaccount.models import SocialApp, SocialToken
import requests
from django.views.generic import FormView
from calendars.views import get_token_valido
# Create your views here.


class Hook_settings(FormView):

    template_name = '/classifyEvents/settings.html'
    success_url = '/classifyEvents/settings.html/'


    def dispatch(self, request, *args, **kwargs):
        try:
            token = get_token_valido(request.user)
        except:
            return redirect(f"{reverse('google_login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)