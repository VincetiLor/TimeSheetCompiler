from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST


def login_view(request):
    return render(request, 'authentication/index.html')


# Va chiusa anche la sessione Shibboleth: con la sola sessione Django il
# middleware rifarebbe il login alla richiesta successiva.
@require_POST
def logout_view(request):
    django_logout(request)
    return redirect(settings.SHIBBOLETH_LOGOUT_URL)


# Il template usa process='connect': allauth aggancia l'account Google
# all'utente gia' in sessione (quello Shibboleth) invece di sostituirlo, quindi
# qui serve un utente autenticato.
@login_required
def avvia_login_google(request):
    next_url = request.GET.get('next') or reverse('index')
    return render(request, 'auto_login_google.html', {'next': next_url})
