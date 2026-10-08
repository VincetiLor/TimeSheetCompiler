from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse


def login_view(request):
    return render(request, 'authentication/index.html')


# Il template usa process='connect': allauth aggancia l'account Google
# all'utente gia' in sessione (quello Shibboleth) invece di sostituirlo, quindi
# qui serve un utente autenticato.
@login_required
def avvia_login_google(request):
    # Senza next si torna alla home: '/' non e' mappata e darebbe 404.
    next_url = request.GET.get('next') or reverse('index')
    return render(request, 'auto_login_google.html', {'next': next_url})
