from django.urls import path

from . import views

urlpatterns = [
    path("", views.login_view, name="index"),
    path("avvia-login-google/", views.avvia_login_google, name="avvia_login_google"),
]