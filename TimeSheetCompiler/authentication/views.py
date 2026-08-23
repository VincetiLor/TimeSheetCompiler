from django.shortcuts import render


def login_view(request):
    return render(request, 'authentication/index.html')


def avvia_login_google(request):
    next_url = request.GET.get('next', '/')
    return render(request, 'auto_login_google.html', {'next': next_url})
