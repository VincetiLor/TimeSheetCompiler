
from django.shortcuts import render
from django.shortcuts import redirect
from django.urls import reverse
from allauth.socialaccount.models import SocialApp, SocialToken
import requests
from django.views.generic import UpdateView
from .models import Hook
from calendars.views import get_token_valido
# Create your views here.


def avvia_login_google(request):
    next_url = request.GET.get('next', '/')
    return render(request, 'auto_login_google.html', {'next': next_url})

class Hook_settings(UpdateView):
    model = Hook
    fields = ['config']
    template_name = 'classifyEvents/settings.html'
    success_url = '/classification'

    def get_object(self, queryset=None):
        hook, created = Hook.objects.get_or_create(
            docente=self.request.user.docente)
        return hook


    def dispatch(self, request, *args, **kwargs):
        try:
            get_token_valido(request.user)
        except:
            return redirect(f"{reverse('avvia_login_google')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)