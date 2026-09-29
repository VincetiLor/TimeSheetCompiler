"""Tests for the classification pipeline: calendar selection, event
comparison entry point, models and the expiry job.

No call actually leaves the process in SelectCalendarsViewTests or
EventCompareTests: authentication.services.get_token_valido is patched where
classificationpipeline.views imports it, and the Google calendarList/events
calls are patched at classificationpipeline.views.requests.get.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from authentication.models import Docente
from classificationpipeline.models import Calendar, Event, expiry_cutoff
from classificationpipeline.views import MESI


def _with_category(kwargs):
    """Completa i kwargs di Event.objects.create per il vincolo di categoria.

    `category_required_when_classified` impone una categoria a ogni riga
    classificato/compilato, ma i test qui sotto parlano di *status*: senza
    questo helper ognuno dovrebbe ripetere una categoria che non sta
    verificando. La categoria esplicita resta possibile e vince, cosi' i test
    che parlano davvero di categorie (EventCategoryConstraintTests) non passano
    da qui.
    """
    kwargs = dict(kwargs)
    needs_category = kwargs.get('status') in (
        Event.Status.CLASSIFICATO, Event.Status.COMPILATO)
    if needs_category and not kwargs.get('category'):
        kwargs['category'] = Event.Category.DIDATTICA
    return kwargs


CALENDAR_LIST_PAYLOAD = {
    'items': [
        {'summary': 'Didattica', 'id': 'didattica@group.calendar.google.com'},
        {'summary': 'Ricerca', 'id': 'ricerca@group.calendar.google.com'},
    ]
}


@override_settings(SHIBBOLETH_DEV_MODE=False)
class EventCompareTests(TestCase):
    """The POST fetches the current month's events of each selected calendar,
    caches them as Calendar/Event rows and classifies the incerto ones with the
    docente's hooks. Google is never reached: get_token_valido and
    requests.get are patched where classificationpipeline.views imports them.
    """

    CAL_A = 'cal-a@google.com'
    CAL_B = 'cal-b@google.com'

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        self.docente = Docente.objects.create(
            user=self.user, id_unimore=4242, cognome='Rossi')
        # The Hook row is created by the post_save signal on Docente.
        self.docente.hook.config = {'didattica': ['lezione']}
        self.docente.hook.save()

        # Day 2 of the current month always falls inside the window the view
        # queries (first of this month -> first of next month).
        self.day2 = timezone.localtime().replace(
            day=2, hour=10, minute=0, second=0, microsecond=0)

    def _google_response(self, payload, status_code=200):
        response = mock.Mock(status_code=status_code)
        response.json.return_value = payload
        return response

    def _events_payload(self, summary='Calendario A'):
        return {
            'summary': summary,
            'timeZone': 'Europe/Rome',
            'items': [
                {
                    'id': 'ev-lezione',
                    'summary': 'Lezione di Analisi',
                    'start': {'dateTime': self.day2.isoformat()},
                    'end': {'dateTime': (self.day2 + timedelta(hours=2)).isoformat()},
                },
                {
                    'id': 'ev-riunione',
                    'summary': 'Riunione di dipartimento',
                    'start': {'dateTime': (self.day2 + timedelta(hours=3)).isoformat()},
                    'end': {'dateTime': (self.day2 + timedelta(hours=4)).isoformat()},
                },
                {
                    'id': 'ev-convegno',
                    'summary': 'Convegno',
                    'start': {'date': self.day2.date().isoformat()},
                    'end': {'date': (self.day2.date() + timedelta(days=1)).isoformat()},
                },
            ],
        }

    def test_anonymous_get_is_redirected_to_login(self):
        response = self.client.get('/events/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/index/?next=/events/')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido')
    def test_anonymous_post_is_redirected_to_login_before_any_google_call(
            self, mock_token, mock_get):
        response = self.client.post('/events/', {'calendars': [self.CAL_A]})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/index/?next=/events/')
        mock_token.assert_not_called()
        mock_get.assert_not_called()

    def test_get_redirects_back_to_calendar_selection(self):
        self.client.force_login(self.user)

        response = self.client.get('/events/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/calendars/')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', side_effect=Exception('no token'))
    def test_missing_google_token_redirects_to_calendar_selection(self, mock_token, mock_get):
        self.client.force_login(self.user)

        response = self.client.post('/events/', {'calendars': [self.CAL_A]})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/calendars/')
        mock_get.assert_not_called()

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_each_selected_calendar_is_fetched_with_the_token(self, mock_token, mock_get):
        mock_get.return_value = self._google_response({'items': []})
        self.client.force_login(self.user)

        response = self.client.post('/events/', {'calendars': [self.CAL_A, self.CAL_B]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendar_ids'], [self.CAL_A, self.CAL_B])
        self.assertEqual(mock_get.call_count, 2)
        urls = [c.args[0] for c in mock_get.call_args_list]
        self.assertIn(self.CAL_A, urls[0])
        self.assertIn(self.CAL_B, urls[1])
        for c in mock_get.call_args_list:
            self.assertEqual(c.kwargs['headers']['Authorization'], 'Bearer access-token')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_post_without_any_calendar_fetches_nothing(self, mock_token, mock_get):
        self.client.force_login(self.user)

        response = self.client.post('/events/', {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendar_ids'], [])
        mock_get.assert_not_called()
        self.assertEqual(Calendar.objects.count(), 0)

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_events_are_stored_under_the_docente_calendar(self, mock_token, mock_get):
        mock_get.return_value = self._google_response(self._events_payload())
        self.client.force_login(self.user)

        self.client.post('/events/', {'calendars': [self.CAL_A]})

        calendar = Calendar.objects.get()
        self.assertEqual(calendar.docente, self.docente)
        self.assertEqual(calendar.calendar_id, self.CAL_A)
        self.assertEqual(calendar.title, 'Calendario A')
        self.assertEqual(
            set(calendar.events.values_list('event_id', flat=True)),
            {'ev-lezione', 'ev-riunione', 'ev-convegno'})

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_all_day_event_starts_at_midnight_in_the_calendar_timezone(
            self, mock_token, mock_get):
        mock_get.return_value = self._google_response(self._events_payload())
        self.client.force_login(self.user)

        self.client.post('/events/', {'calendars': [self.CAL_A]})

        convegno = Event.objects.get(event_id='ev-convegno')
        start = timezone.localtime(convegno.start_time)
        self.assertEqual(start.date(), self.day2.date())
        self.assertEqual((start.hour, start.minute), (0, 0))
        self.assertEqual(convegno.end_time - convegno.start_time, timedelta(days=1))

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_hook_keyword_classifies_the_event_and_the_rest_stays_incerto(
            self, mock_token, mock_get):
        mock_get.return_value = self._google_response(self._events_payload())
        self.client.force_login(self.user)

        response = self.client.post('/events/', {'calendars': [self.CAL_A]})

        lezione = Event.objects.get(event_id='ev-lezione')
        self.assertEqual(lezione.status, Event.Status.CLASSIFICATO)
        self.assertEqual(lezione.category, Event.Category.DIDATTICA)
        riunione = Event.objects.get(event_id='ev-riunione')
        self.assertEqual(riunione.status, Event.Status.INCERTO)
        self.assertEqual(riunione.category, '')

        self.assertEqual(
            [e.event_id for e in response.context['eventi_classificati']],
            ['ev-lezione'])
        self.assertEqual(
            {e.event_id for e in response.context['eventi_incerti']},
            {'ev-riunione', 'ev-convegno'})

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_page_names_the_month_in_italian_and_shows_all_day_events(
            self, mock_token, mock_get):
        """LANGUAGE_CODE is en-us, so |date:"F" would say "September": the
        month name comes from the view. All-day events are stored as
        midnight-to-midnight and must not render as 00:00-00:00.
        """
        mock_get.return_value = self._google_response(self._events_payload())
        self.client.force_login(self.user)

        response = self.client.post('/events/', {'calendars': [self.CAL_A]})

        now = timezone.localtime()
        self.assertEqual(response.context['mese'], f'{MESI[now.month - 1]} {now.year}')
        self.assertContains(response, 'Tutto il giorno')
        self.assertNotContains(response, '00:00–00:00')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_a_second_post_updates_rows_instead_of_duplicating_them(
            self, mock_token, mock_get):
        mock_get.return_value = self._google_response(self._events_payload())
        self.client.force_login(self.user)
        self.client.post('/events/', {'calendars': [self.CAL_A]})

        payload = self._events_payload(summary='Calendario A rinominato')
        payload['items'][1]['summary'] = 'Riunione spostata'
        mock_get.return_value = self._google_response(payload)
        self.client.post('/events/', {'calendars': [self.CAL_A]})

        self.assertEqual(Calendar.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 3)
        self.assertEqual(Event.objects.get(event_id='ev-riunione').title, 'Riunione spostata')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_a_calendar_google_refuses_is_skipped(self, mock_token, mock_get):
        mock_get.side_effect = [
            self._google_response({}, status_code=404),
            self._google_response(self._events_payload(summary='Calendario B')),
        ]
        self.client.force_login(self.user)

        response = self.client.post('/events/', {'calendars': [self.CAL_A, self.CAL_B]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(Calendar.objects.values_list('calendar_id', flat=True)), [self.CAL_B])

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_events_of_another_docente_are_not_shown(self, mock_token, mock_get):
        other_user = User.objects.create_user(username='altro')
        other = Docente.objects.create(user=other_user, id_unimore=9999, cognome='Bianchi')
        other_calendar = Calendar.objects.create(
            docente=other, calendar_id=self.CAL_A, title='Suo')
        Event.objects.create(
            calendar=other_calendar, event_id='ev-altrui', title='Lezione altrui',
            start_time=self.day2, end_time=self.day2 + timedelta(hours=1))
        mock_get.return_value = self._google_response({'items': []})
        self.client.force_login(self.user)

        response = self.client.post('/events/', {'calendars': [self.CAL_A]})

        self.assertEqual(list(response.context['eventi_classificati']), [])
        self.assertEqual(list(response.context['eventi_incerti']), [])


@override_settings(SHIBBOLETH_DEV_MODE=False)
class ModelTests(TestCase):

    def test_events_are_cascade_deleted_with_their_calendar(self):
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(user=user, id_unimore=4242, cognome='Rossi')
        calendar = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')
        Event.objects.create(
            calendar=calendar, event_id='evt-1', title='Lezione',
            start_time='2026-09-01T09:00:00Z', end_time='2026-09-01T11:00:00Z')

        calendar.delete()

        self.assertEqual(Event.objects.count(), 0)

    def test_duplicate_event_id_in_the_same_calendar_is_rejected(self):
        """(calendar, event_id) is the deduplication key: re-running ingestion
        for the same calendar must not duplicate events.
        """
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(user=user, id_unimore=4242, cognome='Rossi')
        calendar = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')
        Event.objects.create(
            calendar=calendar, event_id='evt-1', title='Lezione',
            start_time='2026-09-01T09:00:00Z', end_time='2026-09-01T11:00:00Z')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Event.objects.create(
                    calendar=calendar, event_id='evt-1', title='Lezione',
                    start_time='2026-09-01T09:00:00Z',
                    end_time='2026-09-01T11:00:00Z')

        self.assertEqual(Event.objects.filter(event_id='evt-1').count(), 1)

    def test_the_same_event_id_in_another_calendar_is_allowed(self):
        """The constraint is scoped to the calendar, not global: two calendars
        may legitimately carry the same Google event id.
        """
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(user=user, id_unimore=4242, cognome='Rossi')
        first = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')
        second = Calendar.objects.create(
            docente=docente, calendar_id='cal-b@google.com', title='Ricerca')
        for calendar in (first, second):
            Event.objects.create(
                calendar=calendar, event_id='evt-1', title='Lezione',
                start_time='2026-09-01T09:00:00Z', end_time='2026-09-01T11:00:00Z')

        self.assertEqual(Event.objects.filter(event_id='evt-1').count(), 2)

    def test_duplicate_calendar_id_for_the_same_docente_is_rejected(self):
        """(docente, calendar_id) is unique: selecting the same Google calendar
        twice must not create a second row.
        """
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(user=user, id_unimore=4242, cognome='Rossi')
        Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Calendar.objects.create(
                    docente=docente, calendar_id='cal-a@google.com', title='Altro')

        self.assertEqual(Calendar.objects.filter(docente=docente).count(), 1)


class EventStatusTests(TestCase):
    """The status field is the backbone of the compilation pipeline: it is what
    tells apart an event still to be worked on from one already in the
    timesheet, and from one frozen because its period expired.
    """

    def setUp(self):
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(
            user=user, id_unimore=4242, cognome='Rossi')
        self.calendar = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')

    def _event(self, event_id, **kwargs):
        return Event.objects.create(
            calendar=self.calendar, event_id=event_id, title='Lezione',
            start_time='2026-09-01T09:00:00Z', end_time='2026-09-01T11:00:00Z',
            **_with_category(kwargs))

    def test_a_new_event_starts_as_incerto(self):
        """Ingestion does not classify: everything enters unclassified and the
        pipeline moves it forward from there.
        """
        event = self._event('evt-1')

        self.assertEqual(event.status, Event.Status.INCERTO)

    def test_active_statuses_select_only_the_events_still_in_the_pipeline(self):
        self._event('evt-1', status=Event.Status.INCERTO)
        self._event('evt-2', status=Event.Status.CLASSIFICATO)
        self._event('evt-3', status=Event.Status.COMPILATO)
        self._event('evt-4', status=Event.Status.PASSATO)

        active = Event.objects.filter(status__in=Event.ACTIVE_STATUSES)

        self.assertEqual(
            sorted(active.values_list('event_id', flat=True)),
            ['evt-1', 'evt-2'])

    def test_stored_status_values_are_the_literals_the_spec_uses(self):
        """The partial index condition and the future mark_passed_events job
        match on these exact strings, so they are part of the contract.
        """
        self.assertEqual(
            [value for value, _ in Event.Status.choices],
            ['incerto', 'classificato', 'compilato', 'passato'])

    def test_an_invalid_status_is_not_rejected_by_the_database_known_gap(self):
        """Known gap: choices is validated by forms, not by the database, and
        there is no state machine guarding the transitions. Nothing stops an
        arbitrary string, nor a compilato -> incerto step backwards.
        """
        event = self._event('evt-1', status=Event.Status.COMPILATO)

        event.status = 'qualunque-cosa'
        event.save()
        event.refresh_from_db()

        self.assertEqual(event.status, 'qualunque-cosa')


class EventCategoryConstraintTests(TestCase):
    """`category_required_when_classified` e' l'unica cosa che impedisce a un
    evento di dirsi classificato senza dire in che categoria.

    Il vincolo e' scritto come implicazione (`status classificato/compilato ->
    category non vuota`), non come congiunzione: la differenza e' che gli stati
    fuori dalla pipeline restano liberi di non avere categoria. Questi test
    fissano entrambi i lati, perche' una congiunzione passerebbe i test sul
    ramo classificato e fallirebbe su tutto il resto.
    """

    def setUp(self):
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(
            user=user, id_unimore=4242, cognome='Rossi')
        self.calendar = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')

    def _create(self, event_id, **kwargs):
        return Event.objects.create(
            calendar=self.calendar, event_id=event_id, title='Lezione',
            start_time='2026-09-01T09:00:00Z', end_time='2026-09-01T11:00:00Z',
            **kwargs)

    def test_a_new_event_has_no_category(self):
        """L'ingestione non classifica: la categoria arriva dopo, da un hook,
        dall'LLM o da una scelta manuale.
        """
        event = self._create('evt-1')

        self.assertEqual(event.category, '')

    def test_classificato_without_a_category_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create('evt-1', status=Event.Status.CLASSIFICATO)

    def test_compilato_without_a_category_is_rejected(self):
        """compilato e' lo stato terminale: se ci arriva senza categoria,
        l'informazione finita nel timesheet non e' piu' ricostruibile.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create('evt-1', status=Event.Status.COMPILATO)

    def test_incerto_without_a_category_is_allowed(self):
        event = self._create('evt-1', status=Event.Status.INCERTO)

        self.assertEqual(event.status, Event.Status.INCERTO)
        self.assertEqual(event.category, '')

    def test_passato_without_a_category_is_allowed(self):
        """Un evento scaduto senza essere mai stato classificato e' il caso
        normale, non un errore: mark_passed_events lo promuove com'e'.
        """
        event = self._create('evt-1', status=Event.Status.PASSATO)

        self.assertEqual(event.category, '')

    def test_classificato_with_a_category_is_accepted(self):
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO,
            category=Event.Category.RICERCA_FINANZIATA)

        event.refresh_from_db()
        self.assertEqual(event.category, 'ricerca_finanziata')

    def test_clearing_the_category_of_a_classified_event_is_rejected(self):
        """Il vincolo vale a ogni scrittura, non solo alla creazione: e' quello
        che rende sicuro un annulla fatto come update di massa.
        """
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO,
            category=Event.Category.DIDATTICA)

        event.category = ''
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                event.save()

    def test_undoing_a_classification_means_clearing_status_and_category_together(self):
        """La sola mossa consentita per togliere una classificazione: tornare a
        incerto e svuotare la categoria nella stessa scrittura.
        """
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO,
            category=Event.Category.DIDATTICA)

        Event.objects.filter(pk=event.pk).update(
            status=Event.Status.INCERTO, category='')

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)
        self.assertEqual(event.category, '')

    def test_an_expired_classified_event_keeps_its_category(self):
        """mark_passed_events sposta solo lo status. La categoria resta, e il
        vincolo non la richiede piu': entrambe le cose vanno bene insieme.
        """
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO,
            category=Event.Category.DIDATTICA)

        Event.objects.filter(pk=event.pk).update(status=Event.Status.PASSATO)

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PASSATO)
        self.assertEqual(event.category, 'didattica')

    def test_stored_category_values_are_the_literals_the_hooks_config_uses(self):
        """Le chiavi di `Hook.config` sono queste stringhe: il form degli hook
        costruisce il suo menu da `Event.Category.choices`, quindi i due lati
        restano allineati solo finche' questi valori non cambiano in silenzio.
        """
        self.assertEqual(
            [value for value, _ in Event.Category.choices],
            ['ricerca_finanziata', 'didattica', 'ricerca_base'])

    def test_an_unknown_category_is_not_rejected_by_the_database_known_gap(self):
        """Known gap, gemello di quello su status: `choices` e' validato dai
        form, non dal database. Il vincolo controlla che la categoria non sia
        vuota, non che sia una di quelle previste.
        """
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO, category='inventata')

        event.refresh_from_db()
        self.assertEqual(event.category, 'inventata')


class ExpiryCutoffTests(TestCase):
    """The delivery rule in one place: an event of month M expires on day 8 of
    month M+1, because the timesheet is due by the 5th-6th and the 8th is the
    grace margin.

    These never call timezone.now(): a test whose result depends on the day it
    runs is not testing the code.
    """

    def _cutoff_on(self, year, month, day):
        at = datetime(year, month, day, 12, 0, tzinfo=dt_timezone.utc)
        return expiry_cutoff(at).date()

    def test_before_the_eighth_the_previous_month_still_has_time(self):
        # 5 August: July is still deliverable, so only June and earlier expired.
        self.assertEqual(self._cutoff_on(2026, 8, 5), date(2026, 7, 1))

    def test_the_seventh_is_still_in_time(self):
        self.assertEqual(self._cutoff_on(2026, 8, 7), date(2026, 7, 1))

    def test_on_the_eighth_the_previous_month_expires(self):
        self.assertEqual(self._cutoff_on(2026, 8, 8), date(2026, 8, 1))

    def test_after_the_eighth_nothing_changes_until_next_month(self):
        self.assertEqual(self._cutoff_on(2026, 8, 31), date(2026, 8, 1))

    def test_early_january_reaches_back_into_the_previous_year(self):
        self.assertEqual(self._cutoff_on(2026, 1, 3), date(2025, 12, 1))

    def test_mid_january_expires_december(self):
        self.assertEqual(self._cutoff_on(2026, 1, 10), date(2026, 1, 1))

    def test_short_february_is_handled_like_any_other_month(self):
        self.assertEqual(self._cutoff_on(2027, 3, 5), date(2027, 2, 1))

    def test_the_cutoff_is_midnight_not_the_current_time(self):
        at = datetime(2026, 8, 20, 17, 45, 13, tzinfo=dt_timezone.utc)
        cutoff = expiry_cutoff(at)

        self.assertEqual((cutoff.hour, cutoff.minute, cutoff.second), (0, 0, 0))


class MarkPassedEventsCommandTests(TestCase):
    """The command only wires the rule to a schedule; the rule itself is tested
    above. What matters here is what it writes and, more importantly, what it
    refuses to touch.
    """

    def setUp(self):
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(
            user=user, id_unimore=4242, cognome='Rossi')
        self.calendar = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')

    def _event(self, event_id, start, status=Event.Status.INCERTO):
        return Event.objects.create(
            calendar=self.calendar, event_id=event_id, title='Lezione',
            start_time=start, end_time=start,
            **_with_category({'status': status}))

    def _long_expired(self, event_id, status=Event.Status.INCERTO):
        """An event old enough to be expired whatever day the suite runs."""
        return self._event(
            event_id, datetime(2020, 1, 15, 9, 0, tzinfo=dt_timezone.utc), status)

    def _run(self, *args):
        out = StringIO()
        call_command('mark_passed_events', *args, stdout=out)
        return out.getvalue()

    def test_an_expired_active_event_becomes_passato(self):
        event = self._long_expired('evt-1')

        self._run()

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PASSATO)

    def test_an_expired_classificato_event_also_becomes_passato(self):
        """classificato means "has a classification", not "already filed": if
        it never reached the timesheet it expires like an uncertain one.
        """
        event = self._long_expired('evt-1', status=Event.Status.CLASSIFICATO)

        self._run()

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.PASSATO)

    def test_a_compilato_event_is_never_rewritten(self):
        """The whole point of the passato state is not to touch history:
        compilato is reserved for what actually reached the timesheet.
        """
        event = self._long_expired('evt-1', status=Event.Status.COMPILATO)

        self._run()

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.COMPILATO)

    def test_an_event_of_the_current_month_is_left_alone(self):
        """The current month can never be expired: its deadline is day 8 of the
        next one.
        """
        event = self._event('evt-1', timezone.now())

        self._run()

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)

    def test_running_twice_promotes_nothing_the_second_time(self):
        """Idempotency is what makes a nightly schedule safe."""
        self._long_expired('evt-1')

        self._run()
        second = self._run()

        self.assertIn('0 eventi promossi', second)

    def test_dry_run_reports_without_writing(self):
        event = self._long_expired('evt-1')

        output = self._run('--dry-run')

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)
        self.assertIn('1 eventi da promuovere', output)


class EventQuerySetTests(TestCase):

    def setUp(self):
        user = User.objects.create_user(username='docente')
        docente = Docente.objects.create(
            user=user, id_unimore=4242, cognome='Rossi')
        self.calendar = Calendar.objects.create(
            docente=docente, calendar_id='cal-a@google.com', title='Didattica')

    def _event(self, event_id, status):
        return Event.objects.create(
            calendar=self.calendar, event_id=event_id, title='Lezione',
            start_time='2026-09-01T09:00:00Z', end_time='2026-09-01T11:00:00Z',
            **_with_category({'status': status}))

    def test_active_selects_only_the_events_still_in_the_pipeline(self):
        self._event('evt-1', Event.Status.INCERTO)
        self._event('evt-2', Event.Status.CLASSIFICATO)
        self._event('evt-3', Event.Status.COMPILATO)
        self._event('evt-4', Event.Status.PASSATO)

        self.assertEqual(
            sorted(Event.objects.active().values_list('event_id', flat=True)),
            ['evt-1', 'evt-2'])

    def test_expired_never_returns_events_outside_the_pipeline(self):
        """expired() is active() plus a date filter: an old compilato event is
        old, but it is not the command's business.
        """
        old = datetime(2020, 1, 15, 9, 0, tzinfo=dt_timezone.utc)
        Event.objects.create(
            calendar=self.calendar, event_id='evt-1', title='Lezione',
            start_time=old, end_time=old, status=Event.Status.COMPILATO,
            category=Event.Category.DIDATTICA)

        self.assertEqual(Event.objects.expired().count(), 0)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class SelectCalendarsViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        Docente.objects.create(user=self.user, id_unimore=4242, cognome='Rossi')

    def _google_response(self, payload):
        response = mock.Mock()
        response.json.return_value = payload
        return response

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_calendar_list_is_mapped_to_summary_id_pairs(self, mock_token, mock_get):
        mock_get.return_value = self._google_response(CALENDAR_LIST_PAYLOAD)
        self.client.force_login(self.user)

        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendars'], {
            'Didattica': 'didattica@group.calendar.google.com',
            'Ricerca': 'ricerca@group.calendar.google.com',
        })
        self.assertEqual(
            mock_get.call_args.kwargs['headers']['Authorization'],
            'Bearer access-token')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_empty_calendar_list_renders_without_error(self, mock_token, mock_get):
        mock_get.return_value = self._google_response({})
        self.client.force_login(self.user)

        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendars'], {})
        self.assertContains(response, 'non ha calendari da leggere')
        self.assertNotContains(response, 'name="calendars"')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido', return_value='access-token')
    def test_every_row_posts_under_the_name_event_compare_reads(
            self, mock_token, mock_get):
        """event_compare reads request.POST.getlist('calendars'): the visible
        row and the <template> cloned by "Aggiungi calendario" must both use
        that name, or the added calendars would be silently dropped.
        """
        mock_get.return_value = self._google_response(CALENDAR_LIST_PAYLOAD)
        self.client.force_login(self.user)

        response = self.client.get('/calendars/')

        self.assertContains(response, 'name="calendars"', count=2)
        self.assertContains(response, 'action="/events/"')

    @mock.patch('classificationpipeline.views.get_token_valido', side_effect=Exception('no token'))
    def test_missing_google_token_redirects_to_google_login(self, mock_token):
        self.client.force_login(self.user)

        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/auth/avvia-login-google/?next=/calendars/')

    @mock.patch('classificationpipeline.views.requests.get')
    @mock.patch('classificationpipeline.views.get_token_valido')
    def test_anonymous_user_is_sent_to_login_url_before_any_google_call(
            self, mock_token, mock_get):
        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/index/?next=/calendars/')
        mock_token.assert_not_called()
        mock_get.assert_not_called()
