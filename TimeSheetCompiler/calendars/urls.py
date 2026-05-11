from django.urls import path

from . import views

urlpatterns = [
    path('avvia-login-google/', views.avvia_login_google, name='avvia_login_google'),
    path("", views.SelectCalendarsView.as_view(), name="eventi_calendars"),
    path("compare/", views.event_compare, name="event_compare"),
]