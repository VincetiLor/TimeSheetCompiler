"""Tests for the classification pipeline: calendar selection, event
comparison entry point, models and the expiry job.

The event_compare tests are deliberately thin: the view currently only echoes
the selected calendar ids into the template context. Nothing writes
Calendar/Event rows anywhere in the codebase yet, so there is no persistence to
assert on - the model tests below exercise the schema directly.

No call actually leaves the process in SelectCalendarsViewTests:
authentication.services.get_token_valido is patched where
classificationpipeline.views imports it, and the Google calendarList call is
patched at classificationpipeline.views.requests.get.
"""

from datetime import date, datetime, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from authentication.models import Docente
from classificationpipeline.models import Calendar, Event, expiry_cutoff


CALENDAR_LIST_PAYLOAD = {
    'items': [
        {'summary': 'Didattica', 'id': 'didattica@group.calendar.google.com'},
        {'summary': 'Ricerca', 'id': 'ricerca@group.calendar.google.com'},
    ]
}


@override_settings(SHIBBOLETH_DEV_MODE=False)
class EventCompareTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        self.docente = Docente.objects.create(
            user=self.user, id_unimore=4242, cognome='Rossi')

    def test_get_redirects_back_to_calendar_selection(self):
        response = self.client.get('/events/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/calendars/')

    def test_post_passes_selected_calendar_ids_to_the_template(self):
        self.client.force_login(self.user)

        response = self.client.post(
            '/events/', {'calendars': ['cal-a@google.com', 'cal-b@google.com']})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['calendar_ids'],
            ['cal-a@google.com', 'cal-b@google.com'])

    def test_post_without_any_calendar_yields_an_empty_list(self):
        self.client.force_login(self.user)

        response = self.client.post('/events/', {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendar_ids'], [])

    def test_post_is_reachable_anonymously_known_gap(self):
        """Known gap: the view has no authentication guard at all, so an
        anonymous POST reaches the classification entry point. Harmless while
        the view is a stub; it must be revisited once the pipeline touches
        per-docente data.
        """
        response = self.client.post('/events/', {'calendars': ['cal-a@google.com']})

        self.assertEqual(response.status_code, 200)

    def test_no_calendar_or_event_row_is_persisted_yet_known_gap(self):
        """Known gap: the classification pipeline is not implemented, so the
        POST that is supposed to start it stores nothing. This test fails as
        soon as ingestion is added, which is the signal to replace it with
        real assertions on the created rows.
        """
        self.client.force_login(self.user)

        self.client.post('/events/', {'calendars': ['cal-a@google.com']})

        self.assertEqual(Calendar.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)


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
            **kwargs)

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
            start_time=start, end_time=start, status=status)

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
            status=status)

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
            start_time=old, end_time=old, status=Event.Status.COMPILATO)

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

    @mock.patch('classificationpipeline.views.get_token_valido', side_effect=Exception('no token'))
    def test_missing_google_token_redirects_to_google_login(self, mock_token):
        self.client.force_login(self.user)

        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/auth/avvia-login-google/?next=/calendars/')

    def test_anonymous_user_is_sent_to_google_login_not_to_login_url_known_gap(self):
        """Known gap: this view has no LoginRequiredMixin, so an anonymous
        request falls into the bare except in dispatch and is routed to the
        Google OAuth starter instead of LOGIN_URL. Pinned to make the missing
        mixin visible; the assertion changes when it is added.
        """
        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/auth/avvia-login-google/?next=/calendars/')
