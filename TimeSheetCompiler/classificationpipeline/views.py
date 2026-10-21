from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.generic import TemplateView
from django.conf import settings
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from hooks.models import Hook
from .compilation_sync import (
    CapsExceededError,
    CompilationRunningError,
    Esse3PeriodError,
    Esse3SaveError,
    compile_events,
)
from .esse3_compilation import Esse3LoginError
from .forms import TimesheetSettingsForm
from .hours import check_caps, minutes_to_hhmm
from .matching import match_category
from .models import Event, Timesheet
from authentication.services import (
    GoogleCredentialsError,
    get_calendar_service,
    list_calendars,
    list_events,
)
from django.db import transaction
from django.db.models import Count, Q
from django.contrib import messages
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST
from googleapiclient.errors import HttpError
from selenium.common.exceptions import WebDriverException
from .services import LLMClassificationError, ai_classificator


def get_timesheet(request, timesheet_id):
    return get_object_or_404(
        Timesheet, pk=timesheet_id, docente=request.user.docente)


def get_open_timesheet(request, timesheet_id):
    timesheet = get_timesheet(request, timesheet_id)
    if not timesheet.is_open:
        raise PermissionDenied
    return timesheet


def get_work_range(request, timesheet):
    # Working range from ?start=YYYY-MM-DD&end=YYYY-MM-DD, None if not valid
    try:
        start = date.fromisoformat(request.GET.get('start', ''))
        end = date.fromisoformat(request.GET.get('end', ''))
    except ValueError:
        return None
    if start > end:
        return None
    if start < timesheet.start_date or end > timesheet.end_date:
        return None
    return start, end


def range_bounds(start, end):
    # The end date is included: the upper bound is the midnight after it
    tz = timezone.get_current_timezone()
    time_min = datetime.combine(start, time.min, tzinfo=tz)
    time_max = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    return time_min, time_max


def events_in_range(timesheet, start, end):
    time_min, time_max = range_bounds(start, end)
    return timesheet.events.filter(
        start_time__gte=time_min,
        start_time__lt=time_max,
    ).order_by('start_time')


def page_url(name, timesheet, start, end):
    url = reverse(name, args=[timesheet.pk])
    query = urlencode({'start': start.isoformat(), 'end': end.isoformat()})
    return f"{url}?{query}"


def invalid_range(request):
    messages.error(request, "Scegli un intervallo di date dentro il periodo del timesheet.")
    return redirect('classificationpipeline:timesheet_list')


def parse_month(value):
    # '2026-03' -> date(2026, 3, 1), None if not valid
    try:
        return datetime.strptime(value, '%Y-%m').date()
    except ValueError:
        return None


@login_required
def timesheet_list(request):
    timesheets = Timesheet.objects.filter(docente=request.user.docente).annotate(
        compiled_count=Count(
            'events', filter=Q(events__status=Event.Status.COMPILATO)),
    )
    return render(request, 'classificationpipeline/timesheets.html', {
        'timesheets': timesheets,
    })


@login_required
@require_POST
def timesheet_create(request):
    first_month = parse_month(request.POST.get('start_month', ''))
    last_month = parse_month(request.POST.get('end_month', ''))

    if first_month is None or last_month is None or first_month > last_month:
        messages.error(request, "Scegli un mese di inizio e un mese di fine validi.")
        return redirect('classificationpipeline:timesheet_list')

    last_day = monthrange(last_month.year, last_month.month)[1]
    timesheet = Timesheet.objects.create(
        docente=request.user.docente,
        start_date=first_month,
        end_date=last_month.replace(day=last_day),
    )
    return redirect('classificationpipeline:timesheet_settings', timesheet.pk)


@login_required
def timesheet_settings(request, timesheet_id):
    timesheet = get_open_timesheet(request, timesheet_id)

    # ?next= brings the user back to the page they came from (the events page)
    back_url = request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}):
        back_url = reverse('classificationpipeline:timesheet_list')

    if request.method == 'POST':
        form = TimesheetSettingsForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                form.save(timesheet)
            messages.success(request, "Impostazioni salvate.")
            return redirect(back_url)
    else:
        form = TimesheetSettingsForm(initial=TimesheetSettingsForm.initial_for(timesheet))

    return render(request, 'classificationpipeline/timesheet_settings.html', {
        'timesheet': timesheet,
        'form': form,
        'back_url': back_url,
    })


@login_required
@require_POST
def timesheet_close(request, timesheet_id):
    timesheet = get_open_timesheet(request, timesheet_id)
    timesheet.status = Timesheet.Status.CLOSED
    timesheet.save(update_fields=['status'])
    return redirect('classificationpipeline:timesheet_list')


@login_required
@require_POST
def timesheet_delete(request, timesheet_id):
    timesheet = get_timesheet(request, timesheet_id)
    timesheet.delete()
    return redirect('classificationpipeline:timesheet_list')


class SelectCalendarsView(LoginRequiredMixin, TemplateView):
    template_name = 'classificationpipeline/eventi_calendars.html'
    context_object_name = 'calendars'

    def get(self, request, *args, **kwargs):
        self.timesheet = get_open_timesheet(request, kwargs['timesheet_id'])
        self.work_range = get_work_range(request, self.timesheet)
        if self.work_range is None:
            return invalid_range(request)

        query = urlencode({'next': request.get_full_path()})
        login_url = f"{reverse('avvia_login_google')}?{query}"
        try:
            self.calendar_items = list_calendars(get_calendar_service(request.user))
        except GoogleCredentialsError:
            return redirect(login_url)
        except HttpError as exc:
            if exc.resp.status not in (401, 403):
                raise
            return redirect(login_url)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end = self.work_range
        context['calendars'] = {
            calendar['summary']: calendar['id'] for calendar in self.calendar_items
        }
        context['timesheet'] = self.timesheet
        context['start'] = start
        context['end'] = end
        context['import_url'] = page_url(
            'classificationpipeline:event_compare', self.timesheet, start, end)
        return context


def parse_google_datetime(node, calendar_tz):
    """Converte un nodo `start`/`end` di Google in un datetime aware.

    Google usa due forme mutuamente esclusive:
      - evento con orario:  {'dateTime': '2026-09-08T08:00:00+02:00', ...}
      - giornata intera:    {'date': '2026-09-08'}

    Nel secondo caso non c'e' nessun orario, quindi la data viene collocata a
    mezzanotte nel fuso del calendario (`calendar_tz`, dal campo `timeZone`
    della risposta) e non in quello del server.

    Per le giornate intere `end.date` e' esclusivo: un evento del solo 8
    settembre ha `end.date` = 9 settembre. Le ore non vengono da qui ma da
    `hours.event_hours`, che usa `all_day_hours` del timesheet per ogni giorno.
    """
    if 'dateTime' in node:
        return datetime.fromisoformat(node['dateTime'])

    giorno = datetime.fromisoformat(node['date'])
    return giorno.replace(tzinfo=ZoneInfo(calendar_tz))


def show_events(request, timesheet, start, end):
    events = events_in_range(timesheet, start, end)
    classified = events.filter(status=Event.Status.CLASSIFICATO)
    caps = check_caps(timesheet, classified, start, end)

    over_caps = False
    for cap in caps:
        if cap['excess'] > 0:
            over_caps = True

    return render(request, 'classificationpipeline/event_compare.html', {
        'timesheet': timesheet,
        'start': start,
        'end': end,
        'caps': caps,
        'over_caps': over_caps,
        'eventi_classificati': classified,
        'eventi_incerti': events.filter(status=Event.Status.INCERTO),
        'categorie': Event.Category.choices,
        'range_query': urlencode({'start': start.isoformat(), 'end': end.isoformat()}),
        'settings_query': urlencode({'next': request.get_full_path()}),
        'classify_url': page_url(
            'classificationpipeline:classifica_incerti', timesheet, start, end),
        'classify_ai_url': page_url(
            'classificationpipeline:classifica_incerti_ai', timesheet, start, end),
        'compile_url': page_url(
            'classificationpipeline:timesheet_compile', timesheet, start, end),
    })


@login_required
def event_compare(request, timesheet_id):

    if request.method != 'POST':
        timesheet = get_timesheet(request, timesheet_id)
        work_range = get_work_range(request, timesheet)
        if work_range is None:
            return invalid_range(request)
        start, end = work_range
        return show_events(request, timesheet, start, end)

    timesheet = get_open_timesheet(request, timesheet_id)
    work_range = get_work_range(request, timesheet)
    if work_range is None:
        return invalid_range(request)
    start, end = work_range
    time_min, time_max = range_bounds(start, end)

    calendars_url = page_url(
        'classificationpipeline:eventi_calendars', timesheet, start, end)
    calendar_ids = request.POST.getlist('calendars')

    try:
        service = get_calendar_service(request.user)
    except GoogleCredentialsError:
        return redirect(calendars_url)

    hooks = request.user.docente.hook.config
    for calendar_id in calendar_ids:

        try:
            data = list_events(service, calendar_id, time_min, time_max)
        except HttpError:
            # Calendario che Google rifiuta (rimosso, non piu' condiviso):
            # si salta e si prosegue con gli altri.
            continue
        except GoogleCredentialsError:
            return redirect(calendars_url)

        calendar_tz = data.get('timeZone', settings.TIME_ZONE)
        calendar_title = data.get('summary', calendar_id)
        events = data.get('items', [])

        for event in events:

            event_obj, created = Event.objects.update_or_create(
                timesheet = timesheet,
                calendar_id = calendar_id,
                event_id = event['id'],
                defaults={
                    'calendar_title' : calendar_title,
                    'title' : event.get('summary', ''),
                    'description' : event.get('description', ''),
                    'start_time' : parse_google_datetime(event['start'], calendar_tz),
                    'end_time' : parse_google_datetime(event['end'], calendar_tz),
                    'all_day' : 'date' in event['start'],
                },
            )

            if event_obj.status != Event.Status.INCERTO:
                continue
            found_category = match_category(event.get('summary', ''), hooks)
            if found_category:
                event_obj.category = found_category
                event_obj.status = Event.Status.CLASSIFICATO
                event_obj.save(update_fields=['category', 'status'])
                print(
                    f"[classificato] {event_obj.title} | "
                    f"{event_obj.start_time:%d/%m %H:%M}-{event_obj.end_time:%H:%M} | "
                    f"{found_category}"
                )

    return redirect(page_url(
        'classificationpipeline:event_compare', timesheet, start, end))


@login_required
@require_POST
def event_delete(request, timesheet_id, event_id):
    timesheet = get_open_timesheet(request, timesheet_id)
    work_range = get_work_range(request, timesheet)
    if work_range is None:
        return invalid_range(request)
    start, end = work_range

    # A compilato event is already on Esse3: it is the only trace of it
    event = get_object_or_404(
        Event,
        pk=event_id,
        timesheet=timesheet,
        status__in=Event.ACTIVE_STATUSES,
    )
    event.delete()

    return redirect(page_url(
        'classificationpipeline:event_compare', timesheet, start, end))


@login_required
def classifica_incerti(request, timesheet_id):

    timesheet = get_open_timesheet(request, timesheet_id)
    work_range = get_work_range(request, timesheet)
    if work_range is None:
        return invalid_range(request)
    start, end = work_range

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
            timesheet = timesheet,
            status = Event.Status.INCERTO,
        ))

        for event in events:
            event.category = choices[event.pk]
            event.status = Event.Status.CLASSIFICATO

        with transaction.atomic():
            Event.objects.bulk_update(events, ['category', 'status'])

    return redirect(page_url(
        'classificationpipeline:event_compare', timesheet, start, end))



@login_required
@require_POST
def classifica_incerti_ai(request, timesheet_id):

    timesheet = get_open_timesheet(request, timesheet_id)
    work_range = get_work_range(request, timesheet)
    if work_range is None:
        return invalid_range(request)
    start, end = work_range
    events_url = page_url(
        'classificationpipeline:event_compare', timesheet, start, end)

    events = list(events_in_range(timesheet, start, end).filter(
        status=Event.Status.INCERTO,
    ))

    try:
        choices = ai_classificator(events)
    except LLMClassificationError:
        messages.error(request, "Classificazione automatica non disponibile, riprova piu' tardi.")
        return redirect(events_url)

    classified = []
    for event in events:
        if event.pk in choices:
            event.category = choices[event.pk]
            event.status = Event.Status.CLASSIFICATO
            classified.append(event)

    Event.objects.bulk_update(classified, ['category', 'status'])

    return redirect(events_url)


def compile_messages(request, compiled, cut, left):
    # left: events still classificato after the compilation, shown on the page
    if not compiled and not left:
        messages.error(request, "Nessun evento classificato da compilare.")
        return
    if compiled:
        messages.success(request, f"Eventi compilati: {compiled}.")
    if cut:
        parts = []
        for event, written, planned in cut:
            day = timezone.localtime(event.start_time)
            parts.append(f"{day:%d/%m} {event.get_category_display()} "
                         f"({minutes_to_hhmm(written)} su {minutes_to_hhmm(planned)})")
        messages.warning(request, "Ore tagliate per il limite di 8 ore al giorno: " + "; ".join(parts) + ".")
    if left:
        messages.warning(request, (
            f"Eventi non scritti: {left}. Rimasti tra i classificati: "
            "su Esse3 il loro giorno era al completo o è avvenuto un errore durante la compilazione."))


@sensitive_post_parameters('esse3_password', 'esse3_otp_seed')
@sensitive_variables('credentials', 'otp_seed')
@login_required
@require_POST
def timesheet_compile(request, timesheet_id):

    timesheet = get_open_timesheet(request, timesheet_id)
    work_range = get_work_range(request, timesheet)
    if work_range is None:
        return invalid_range(request)
    start, end = work_range

    events = events_in_range(timesheet, start, end).filter(
        status=Event.Status.CLASSIFICATO,
    )

    # Esse3 username is the Unimore id we already have. Password and OTP seed
    # come from the form, used only for this request and never saved.
    otp_seed = request.POST.get('esse3_otp_seed', '')
    credentials = {
        'username': str(request.user.docente.id_unimore),
        'password': request.POST.get('esse3_password', ''),
        'otp_seed': otp_seed.replace(' ', '').replace('-', '').upper(),
    }

    try:
        compiled, cut = compile_events(timesheet, events, start, end, credentials)
    except CompilationRunningError:
        messages.error(request, "Una compilazione di questo timesheet è già in corso: aspetta che finisca.")
    except CapsExceededError:
        messages.error(request, "Alcune soglie di ore sono superate: non è stato compilato niente.")
    except Esse3LoginError:
        messages.error(request, "Accesso a Esse3 non riuscito: controlla password e seed OTP.")
    except Esse3PeriodError as error:
        messages.error(request, (
            f"Compilazione fermata: Esse3 non ha aperto il periodo del {error.day:%d/%m/%Y}. "
            "I giorni salvati prima sono tra i compilati, gli eventi rimasti tra i "
            "classificati non sono stati scritti."))
    except Esse3SaveError as error:
        messages.error(request, (
            f"Compilazione fermata: dopo il salvataggio il diario dal {error.first:%d/%m/%Y} "
            f"al {error.last:%d/%m/%Y} non è quello atteso. Controlla quei giorni su Esse3: "
            "le ore potrebbero esserci già, anche se gli eventi sono rimasti tra i classificati."))
    except WebDriverException:
        messages.error(request, (
            "Compilazione interrotta: Esse3 non risponde o il browser si è chiuso. "
            "I giorni salvati prima sono tra i compilati. Prima di ricompilare controlla "
            "su Esse3 l'ultimo periodo: le sue ore potrebbero esserci già."))
    else:
        left = events_in_range(timesheet, start, end).filter(
            status=Event.Status.CLASSIFICATO,
        ).count()
        compile_messages(request, compiled, cut, left)

    return redirect(page_url(
        'classificationpipeline:event_compare', timesheet, start, end))
