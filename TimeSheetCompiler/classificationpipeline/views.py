from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import TemplateView
import requests
import json
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from hooks.models import Hook
from .models import Calendar, Event
from authentication.services import get_token_valido
from django.db import transaction


# (connect, read) in secondi: senza, una Google API bloccata tiene occupato
# il worker Django a tempo indeterminato.
GOOGLE_API_TIMEOUT = (5, 10)

# Nomi dei mesi scritti qui e non con il filtro |date:"F": LANGUAGE_CODE e'
# 'en-us' e il filtro darebbe "September".
MESI = [
    'gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
    'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre',
]


class SelectCalendarsView(LoginRequiredMixin, TemplateView):
    template_name = 'classificationpipeline/eventi_calendars.html'
    context_object_name = 'calendars'

    # Il token si cerca in get() e non in dispatch(): un dispatch() definito qui
    # girerebbe prima di quello di LoginRequiredMixin, e un anonimo finirebbe
    # all'OAuth Google invece che a LOGIN_URL.
    def get(self, request, *args, **kwargs):
        try:
            self.token = get_token_valido(self.request.user)
        except:
             return redirect(f"{reverse('avvia_login_google')}?next={request.path}")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        response = requests.get(
            'https://www.googleapis.com/calendar/v3/users/me/calendarList',
            headers = {'Authorization': f'Bearer {self.token}'},
            params={'maxResults': 10},
            timeout=GOOGLE_API_TIMEOUT,
        )
        calendar_list = response.json().get('items', [])
        calendars = {}
        for calendar in calendar_list:
            calendars[calendar['summary']] = calendar['id']

        print(calendars)
        context['calendars'] = calendars
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

    now = timezone.localtime()  
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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
        print(calendar_ids)
      
        try:
            token = get_token_valido(request.user)
        except Exception:
            return redirect('classificationpipeline:eventi_calendars')
        
        now = timezone.localtime()  
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        first_of_next_month = (first_of_this_month + timedelta(days=32)).replace(day=1)
        hooks = request.user.docente.hook.config
        for calendar_id in calendar_ids:

            response = requests.get(
                f'https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events',
                headers={'Authorization': f'Bearer {token}'},
                params = {
                    'singleEvents' : 'true',
                    'orderBy' : 'startTime',
                    'timeMin' : first_of_this_month.isoformat(),
                    'timeMax' : first_of_next_month.isoformat(),
                    },
                timeout=GOOGLE_API_TIMEOUT,
            )
            if response.status_code != 200:
                continue

            data = response.json()
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

                #print(event['summary'], event['id'])
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
                    print(
                        f"[classificato] {event_obj.title} | "
                        f"{event_obj.start_time:%d/%m %H:%M}-{event_obj.end_time:%H:%M} | "
                        f"{found_category}"
                    )
                    
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

