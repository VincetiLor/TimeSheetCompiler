from django.urls import path

from . import views


app_name = 'classificationpipeline'

urlpatterns = [
    path('', views.event_compare, name='event_compare'),
]