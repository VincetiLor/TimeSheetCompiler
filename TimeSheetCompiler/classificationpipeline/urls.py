from django.urls import path

from . import views


app_name = 'classificationpipeline'

# I prefissi stanno qui e non in mysite/urls.py perche' l'app espone due rotte
# con prefissi diversi (/calendars/ e /events/) sotto un unico namespace: due
# include() dello stesso modulo darebbero due istanze dello stesso namespace.
urlpatterns = [
    path('calendars/', views.SelectCalendarsView.as_view(), name='eventi_calendars'),
    path('events/', views.event_compare, name='event_compare'),
    path('events/classifica/', views.classifica_incerti, name='classifica_incerti'),
    path('events/classifica-ai/', views.classifica_incerti_ai, name='classifica_incerti_ai'),
]
