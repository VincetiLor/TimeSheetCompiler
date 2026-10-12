from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import TemplateView
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from hooks.models import Hook
from .models import Calendar, Event, expiry_cutoff
from authentication.services import (
    GoogleCredentialsError,
    get_calendar_service,
    list_calendars,
    list_events,
)
from django.db import transaction
from django.contrib import messages
from django.views.decorators.http import require_POST
from googleapiclient.errors import HttpError
from .services import LLMClassificationError, classify_events


MESI = [
    'gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
    'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre',
]


class SelectCalendarsView(LoginRequiredMixin, TemplateView):
    template_name = 'classificationpipeline/eventi_calendars.html'
    context_object_name = 'calendars'

    def get(self, request, *args, **kwargs):
        try:
            self.calendar_items = list_calendars(get_calendar_service(request.user))
        except GoogleCredentialsError:
            return redirect(f"{reverse('avvia_login_google')}?next={request.path}")
        except HttpError as exc:
            if exc.resp.status not in (401, 403):
                raise
            return redirect(f"{reverse('avvia_login_google')}?next={request.path}")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['calendars'] = {
            calendar['summary']: calendar['id'] for calendar in self.calendar_items
        }
        return context


def parse_google_datetime(node, calendar_tz):
    """Converte un nodo `start`/`end` di Google in un datetime aware.

    Google usa due forme mutuamente esclusive:
      - evento con orario:  {'dateTime': '2026-09-08T08:00:00+02:00', ...}
      - giornata intera:    {'date': '2026-09-08'}

    Nel secondo caso non c'e' nessun orario, quindi la data viene collocata a
    mezzanotte nel fuso del calendario (`calendar_tz`, dal campo `timeZone`
    della risposta) e non in quello del server.

    Nota: per le giornate intere `end.date` e' esclusivo — un evento del solo
    8 settembre ha `end.date` = 9 settembre — quindi la durata che ne risulta
    e' di 24 ore. La convenzione da applicare al timesheet non e' ancora decisa.
    """
    if 'dateTime' in node:
        return datetime.fromisoformat(node['dateTime'])

    giorno = datetime.fromisoformat(node['date'])
    return giorno.replace(tzinfo=ZoneInfo(calendar_tz))


@login_required
def check_events(request):

    first_of_this_month = expiry_cutoff()
    first_of_next_month = (first_of_this_month + timedelta(days=32)).replace(day=1)

    events = Event.objects.filter(
        calendar__docente=request.user.docente,
        start_time__gte=first_of_this_month,
        start_time__lt=first_of_next_month,
    ).order_by('start_time')

    return render(request, 'classificationpipeline/event_compare.html', {         
    'mese': f"{MESI[first_of_this_month.month - 1]} {first_of_this_month.year}",
    'eventi_classificati': events.filter(status=Event.Status.CLASSIFICATO),
    'eventi_incerti': events.filter(status=Event.Status.INCERTO),
    'categorie': Event.Category.choices,
    })


@login_required
def event_compare(request):

    if request.method == 'POST':
        calendar_ids = request.POST.getlist('calendars')

        try:
            service = get_calendar_service(request.user)
        except GoogleCredentialsError:
            return redirect('classificationpipeline:eventi_calendars')

        first_of_this_month = expiry_cutoff()
        first_of_next_month = (first_of_this_month + timedelta(days=32)).replace(day=1)
        hooks = request.user.docente.hook.config
        for calendar_id in calendar_ids:

            try:
                data = list_events(
                    service, calendar_id, first_of_this_month, first_of_next_month)
            except HttpError:
                # Calendario che Google rifiuta (rimosso, non piu' condiviso):
                # si salta e si prosegue con gli altri.
                continue
            except GoogleCredentialsError:
                return redirect('classificationpipeline:eventi_calendars')

            calendar_tz = data.get('timeZone', settings.TIME_ZONE)
            events = data.get('items', [])

            calendar_obj, _ = Calendar.objects.get_or_create(
                docente=request.user.docente,
                calendar_id=calendar_id,
                defaults={
                    'title': data.get('summary', calendar_id),
                    'description': data.get('description', ''),
                },
            )

            for event in events:

                event_obj, created = Event.objects.update_or_create(
                    calendar = calendar_obj,
                    event_id = event['id'],
                    defaults={
                        'title' : event.get('summary', ''),
                        'description' : event.get('description', ''),
                        'start_time' : parse_google_datetime(event['start'], calendar_tz),
                        'end_time' : parse_google_datetime(event['end'], calendar_tz),
                    },
                )

                if event_obj.status != Event.Status.INCERTO:
                    continue
                title = event.get('summary', '').lower()
                found_category = ''
                for category, keywords in hooks.items():
                    if any(keyword in title for keyword in keywords):
                        found_category = category
                        break
                if found_category:
                    event_obj.category = found_category
                    event_obj.status = Event.Status.CLASSIFICATO
                    event_obj.save(update_fields=['category', 'status'])

        eventi = (
            Event.objects
            .filter(
                calendar__docente=request.user.docente,
                calendar__calendar_id__in=calendar_ids, 
                start_time__gte=first_of_this_month,
                start_time__lt=first_of_next_month,
            )
            .select_related('calendar')
            .order_by('start_time')
        )

        return render(request, 'classificationpipeline/event_compare.html', {
            'calendar_ids': calendar_ids,
            'mese': f"{MESI[first_of_this_month.month - 1]} {first_of_this_month.year}",
            'eventi_classificati': eventi.filter(status=Event.Status.CLASSIFICATO),
            'eventi_incerti': eventi.filter(status=Event.Status.INCERTO),
            'categorie': Event.Category.choices,
        })
    
    else:
        return check_events(request)


@login_required
def classifica_incerti(request):

    if request.method == 'POST':

        choices = {}
        for key, value in request.POST.items():
            if not value or not key.startswith('categoria_'):
                continue
            if value not in Event.Category.values:
                continue
            try:
                pk = int(key.removeprefix('categoria_'))
            except ValueError:
                continue
            choices[pk] = value


        events = list(Event.objects.filter(
            pk__in = choices,
            calendar__docente = request.user.docente,
            status = Event.Status.INCERTO,
        ))

        for event in events:
            event.category = choices[event.pk]
            event.status = Event.Status.CLASSIFICATO

        with transaction.atomic():
            Event.objects.bulk_update(events, ['category', 'status'])

    return redirect('classificationpipeline:event_compare')



@login_required
@require_POST
def classifica_incerti_ai(request):

    first_of_this_month = expiry_cutoff()
    first_of_next_month = (first_of_this_month + timedelta(days=32)).replace(day=1)

    events = list(Event.objects.filter(
        calendar__docente=request.user.docente,
        status=Event.Status.INCERTO,
        start_time__gte=first_of_this_month,
        start_time__lt=first_of_next_month,
    ))

    try:
        choices = classify_events(events)
    except LLMClassificationError:
        messages.error(request, "Classificazione automatica non disponibile, riprova piu' tardi.")
        return redirect('classificationpipeline:event_compare')

    classified = []
    for event in events:
        if event.pk in choices:
            event.category = choices[event.pk]
            event.status = Event.Status.CLASSIFICATO
            classified.append(event)

    Event.objects.bulk_update(classified, ['category', 'status'])

    return redirect('classificationpipeline:event_compare')
