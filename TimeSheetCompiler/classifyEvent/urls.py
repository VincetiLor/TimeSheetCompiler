from django.urls import path

from . import views

urlpatterns = [
    path('avvia-login-google/', views.avvia_login_google, name='avvia_login_google'),
    path("", views.Hook_settings.as_view(), name="settings"),
]