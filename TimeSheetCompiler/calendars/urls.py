from django.urls import path

from . import views

app_name = 'calendars'

urlpatterns = [
    path('avvia-login-google/', views.avvia_login_google, name='avvia_login_google'),
    path("", views.SelectCalendarsView.as_view(), name="eventi_calendars"),
]