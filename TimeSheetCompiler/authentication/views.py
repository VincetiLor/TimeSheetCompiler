from django.shortcuts import render
from django.urls import reverse


def login_view(request):
    return render(request, 'authentication/index.html')


def avvia_login_google(request):
    # Senza next si torna alla home: '/' non e' mappata e darebbe 404.
    next_url = request.GET.get('next') or reverse('index')
    return render(request, 'auto_login_google.html', {'next': next_url})
