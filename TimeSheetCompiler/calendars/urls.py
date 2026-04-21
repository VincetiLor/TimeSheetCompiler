from django.urls import path

from . import views

urlpatterns = [
    path("", views.eventi_calendar, name="eventi_calendars"),
]