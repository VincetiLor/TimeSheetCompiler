"""Tests for the classification pipeline: timesheets, working range, calendar
selection, event import, classification and deletion.

No call leaves the process: the Google helpers of authentication.services
(get_calendar_service, list_calendars, list_events) are patched where
classificationpipeline.views imports them. The same holds for OpenAI: the
client class is patched in AiClassificatorTests, the whole ai_classificator
function in ClassificaIncertiAiTests.
"""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import httplib2
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from googleapiclient.errors import HttpError
from openai import OpenAIError

from authentication.models import Docente
from authentication.services import GoogleCredentialsError
from classificationpipeline.hours import (
    check_caps, event_hours, events_by_day, hhmm_to_minutes, hours_by_category,
    minutes_to_hhmm, plan_day)
from classificationpipeline.matching import match_category, title_chunks
from classificationpipeline.models import Event, Timesheet, TimesheetHourCap
from classificationpipeline.services import LLMClassificationError, ai_classificator


ROME = ZoneInfo('Europe/Rome')

# Every test works on a January-June 2026 timesheet and on March as working range
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 6, 30)
RANGE_START = date(2026, 3, 1)
RANGE_END = date(2026, 3, 31)
MARCH_10 = datetime(2026, 3, 10, 10, 0, tzinfo=ROME)

CAL_A = 'cal-a@group.calendar.google.com'
CAL_B = 'cal-b@group.calendar.google.com'

CALENDAR_LIST_PAYLOAD = [
    {'summary': 'Didattica', 'id': CAL_A},
    {'summary': 'Ricerca', 'id': CAL_B},
]


def make_docente(username='docente', id_unimore=4242):
    user = User.objects.create_user(username=username)
    docente = Docente.objects.create(user=user, id_unimore=id_unimore, cognome='Rossi')
    return user, docente


def make_timesheet(docente, status=Timesheet.Status.OPEN):
    return Timesheet.objects.create(
        docente=docente, start_date=PERIOD_START, end_date=PERIOD_END, status=status)


def make_event(timesheet, event_id, start=MARCH_10, calendar_id=CAL_A, **kwargs):
    # classificato and compilato need a category (category_required_when_classified)
    if kwargs.get('status') in (Event.Status.CLASSIFICATO, Event.Status.COMPILATO):
        kwargs.setdefault('category', Event.Category.PREPARAZIONE_LEZIONI)
    kwargs.setdefault('title', 'Riunione')
    return Event.objects.create(
        timesheet=timesheet, calendar_id=calendar_id, event_id=event_id,
        start_time=start, end_time=start + timedelta(hours=2), **kwargs)


def page(name, timesheet, start=RANGE_START, end=RANGE_END):
    query = urlencode({'start': start.isoformat(), 'end': end.isoformat()})
    return f"{reverse('classificationpipeline:' + name, args=[timesheet.pk])}?{query}"


def delete_url(event, start=RANGE_START, end=RANGE_END):
    url = reverse('classificationpipeline:event_delete', args=[event.timesheet.pk, event.pk])
    query = urlencode({'start': start.isoformat(), 'end': end.isoformat()})
    return f"{url}?{query}"


def message_texts(response):
    texts = []
    for message in get_messages(response.wsgi_request):
        texts.append(str(message))
    return texts


class TimesheetModelTests(TestCase):

    def setUp(self):
        _, self.docente = make_docente()

    def test_a_new_timesheet_is_open(self):
        timesheet = make_timesheet(self.docente)

        self.assertTrue(timesheet.is_open)

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Timesheet.objects.create(
                    docente=self.docente, start_date=date(2026, 6, 1), end_date=date(2026, 1, 31))

    def test_events_are_deleted_with_their_timesheet(self):
        timesheet = make_timesheet(self.docente)
        make_event(timesheet, 'evt-1')

        timesheet.delete()

        self.assertEqual(Event.objects.count(), 0)

    def test_the_same_event_twice_in_a_timesheet_is_rejected(self):
        timesheet = make_timesheet(self.docente)
        make_event(timesheet, 'evt-1')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_event(timesheet, 'evt-1')

    def test_the_same_event_id_in_another_calendar_is_allowed(self):
        # A Google event id is unique only inside its calendar
        timesheet = make_timesheet(self.docente)
        make_event(timesheet, 'evt-1', calendar_id=CAL_A)
        make_event(timesheet, 'evt-1', calendar_id=CAL_B)

        self.assertEqual(Event.objects.count(), 2)

    def test_the_same_event_in_two_overlapping_timesheets_is_allowed(self):
        year = make_timesheet(self.docente)
        semester = make_timesheet(self.docente)
        make_event(year, 'evt-1')
        make_event(semester, 'evt-1')

        self.assertEqual(Event.objects.count(), 2)


class EventStatusTests(TestCase):

    def setUp(self):
        _, docente = make_docente()
        self.timesheet = make_timesheet(docente)

    def test_a_new_event_starts_as_incerto(self):
        event = make_event(self.timesheet, 'evt-1')

        self.assertEqual(event.status, Event.Status.INCERTO)

    def test_stored_status_values_are_the_three_of_the_spec(self):
        # The partial index idx_event_active matches on these exact strings
        self.assertEqual(Event.Status.values, ['incerto', 'classificato', 'compilato'])

    def test_active_selects_only_the_events_still_in_the_pipeline(self):
        make_event(self.timesheet, 'evt-1', status=Event.Status.INCERTO)
        make_event(self.timesheet, 'evt-2', status=Event.Status.CLASSIFICATO)
        make_event(self.timesheet, 'evt-3', status=Event.Status.COMPILATO)

        active = Event.objects.active().values_list('event_id', flat=True)

        self.assertEqual(sorted(active), ['evt-1', 'evt-2'])

    def test_an_invalid_status_is_not_rejected_by_the_database_known_gap(self):
        """Known gap: choices is validated by forms, not by the database."""
        event = make_event(self.timesheet, 'evt-1', status=Event.Status.COMPILATO)

        event.status = 'qualunque-cosa'
        event.save()
        event.refresh_from_db()

        self.assertEqual(event.status, 'qualunque-cosa')


class EventCategoryConstraintTests(TestCase):
    """category_required_when_classified: classificato and compilato need a
    category, incerto may have none.
    """

    def setUp(self):
        _, docente = make_docente()
        self.timesheet = make_timesheet(docente)

    def _create(self, event_id, **kwargs):
        return Event.objects.create(
            timesheet=self.timesheet, calendar_id=CAL_A, event_id=event_id,
            title='Lezione', start_time=MARCH_10, end_time=MARCH_10 + timedelta(hours=2),
            **kwargs)

    def test_a_new_event_has_no_category(self):
        self.assertEqual(self._create('evt-1').category, '')

    def test_classificato_without_a_category_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create('evt-1', status=Event.Status.CLASSIFICATO)

    def test_compilato_without_a_category_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create('evt-1', status=Event.Status.COMPILATO)

    def test_classificato_with_a_category_is_accepted(self):
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO,
            category=Event.Category.COMPITI_ORGANIZZATIVI)

        event.refresh_from_db()
        self.assertEqual(event.category, 'compiti_organizzativi')

    def test_clearing_the_category_of_a_classified_event_is_rejected(self):
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO, category=Event.Category.PREPARAZIONE_LEZIONI)

        event.category = ''
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                event.save()

    def test_undoing_a_classification_means_clearing_status_and_category_together(self):
        event = self._create(
            'evt-1', status=Event.Status.CLASSIFICATO, category=Event.Category.PREPARAZIONE_LEZIONI)

        Event.objects.filter(pk=event.pk).update(status=Event.Status.INCERTO, category='')

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)
        self.assertEqual(event.category, '')

    def test_stored_category_values_are_the_literals_the_hooks_config_uses(self):
        # Hook.config keys are these strings: they must not change silently
        self.assertEqual(Event.Category.values, [
            'assistenza_laureandi',
            'atenei_convenzionati',
            'didattica_integrativa',
            'dottorati',
            'master',
            'pnrr',
            'scuole_specializzazione',
            'compiti_organizzativi',
            'esami_laurea',
            'esami_profitto',
            'orientamento_tutorato',
            'preparazione_lezioni',
            'ricevimento_studenti',
        ])

    def test_labels_are_the_row_texts_of_the_esse3_diary(self):
        # Selenium will look for the row by this text
        self.assertEqual(Event.Category.PNRR.label, 'Attività PNRR (solo RTD-A)')
        self.assertEqual(
            Event.Category.ORIENTAMENTO_TUTORATO.label,
            'orient. e tutor. piani di studio, stages')
        self.assertEqual(len(Event.Category.choices), 13)

    def test_an_unknown_category_is_not_rejected_by_the_database_known_gap(self):
        event = self._create('evt-1', status=Event.Status.CLASSIFICATO, category='inventata')

        event.refresh_from_db()
        self.assertEqual(event.category, 'inventata')


@override_settings(SHIBBOLETH_DEV_MODE=False)
class TimesheetListTests(TestCase):

    URL = '/timesheets/'

    def setUp(self):
        self.user, self.docente = make_docente()

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/index/?next=/timesheets/')

    def test_only_the_docente_own_timesheets_are_listed(self):
        own = make_timesheet(self.docente)
        _, other = make_docente('altro', 9999)
        make_timesheet(other)
        self.client.force_login(self.user)

        response = self.client.get(self.URL)

        self.assertEqual(list(response.context['timesheets']), [own])

    def test_each_timesheet_counts_its_compiled_events(self):
        timesheet = make_timesheet(self.docente)
        make_event(timesheet, 'evt-1', status=Event.Status.COMPILATO)
        make_event(timesheet, 'evt-2', status=Event.Status.COMPILATO)
        make_event(timesheet, 'evt-3', status=Event.Status.CLASSIFICATO)
        self.client.force_login(self.user)

        response = self.client.get(self.URL)

        self.assertEqual(response.context['timesheets'][0].compiled_count, 2)

    def test_a_closed_timesheet_has_no_close_button_and_no_calendar_button(self):
        timesheet = make_timesheet(self.docente, status=Timesheet.Status.CLOSED)
        self.client.force_login(self.user)

        response = self.client.get(self.URL)

        self.assertNotContains(response, f'/timesheets/{timesheet.pk}/close/')
        self.assertNotContains(response, f'/timesheets/{timesheet.pk}/calendars/')
        self.assertContains(response, f'/timesheets/{timesheet.pk}/events/')


@override_settings(SHIBBOLETH_DEV_MODE=False)
class TimesheetCreateTests(TestCase):

    URL = '/timesheets/new/'

    def setUp(self):
        self.user, self.docente = make_docente()
        self.client.force_login(self.user)

    def test_the_two_months_become_the_first_and_the_last_day_then_the_settings_open(self):
        response = self.client.post(self.URL, {'start_month': '2026-01', 'end_month': '2026-06'})

        timesheet = Timesheet.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f'/timesheets/{timesheet.pk}/settings/')
        self.assertEqual(timesheet.docente, self.docente)
        self.assertEqual(timesheet.start_date, date(2026, 1, 1))
        self.assertEqual(timesheet.end_date, date(2026, 6, 30))
        self.assertTrue(timesheet.is_open)

    def test_a_leap_february_ends_on_the_29th(self):
        self.client.post(self.URL, {'start_month': '2028-02', 'end_month': '2028-02'})

        self.assertEqual(Timesheet.objects.get().end_date, date(2028, 2, 29))

    def test_overlapping_timesheets_are_allowed(self):
        self.client.post(self.URL, {'start_month': '2026-01', 'end_month': '2026-12'})
        self.client.post(self.URL, {'start_month': '2026-01', 'end_month': '2026-06'})

        self.assertEqual(Timesheet.objects.filter(docente=self.docente).count(), 2)

    def test_a_last_month_before_the_first_is_rejected(self):
        response = self.client.post(self.URL, {'start_month': '2026-06', 'end_month': '2026-01'})

        self.assertEqual(response.url, '/timesheets/')
        self.assertEqual(Timesheet.objects.count(), 0)
        self.assertEqual(len(message_texts(response)), 1)

    def test_a_malformed_month_is_rejected(self):
        response = self.client.post(self.URL, {'start_month': 'gennaio', 'end_month': '2026-06'})

        self.assertEqual(response.url, '/timesheets/')
        self.assertEqual(Timesheet.objects.count(), 0)

    def test_get_is_rejected(self):
        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, 405)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class TimesheetSettingsTests(TestCase):

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.url = f'/timesheets/{self.timesheet.pk}/settings/'
        self.client.force_login(self.user)

    def caps(self):
        caps = {}
        for cap in self.timesheet.hour_caps.all():
            caps[cap.category] = cap.max_hours
        return caps

    def test_the_page_shows_the_saved_values(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.ESAMI_LAUREA, max_hours=30)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form['all_day_hours'].value(), 8)
        self.assertEqual(form['cap_esami_laurea'].value(), 30)
        self.assertIsNone(form['cap_master'].value())

    def test_saving_stores_the_hours_and_one_cap_per_filled_category(self):
        response = self.client.post(self.url, {
            'all_day_hours': '6',
            'total_hours_cap': '350',
            'cap_esami_laurea': '30',
            'cap_master': '0',
            'cap_dottorati': '',
        })

        self.assertEqual(response.url, '/timesheets/')
        self.timesheet.refresh_from_db()
        self.assertEqual(self.timesheet.all_day_hours, 6)
        self.assertEqual(self.timesheet.total_hours_cap, 350)
        self.assertEqual(self.caps(), {'esami_laurea': 30, 'master': 0})

    def test_an_emptied_field_removes_the_cap(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.ESAMI_LAUREA, max_hours=30)
        self.timesheet.total_hours_cap = 350
        self.timesheet.save()

        self.client.post(self.url, {'all_day_hours': '8', 'total_hours_cap': '', 'cap_esami_laurea': ''})

        self.timesheet.refresh_from_db()
        self.assertIsNone(self.timesheet.total_hours_cap)
        self.assertEqual(self.caps(), {})

    def test_a_changed_cap_is_updated_not_duplicated(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.ESAMI_LAUREA, max_hours=30)

        self.client.post(self.url, {'all_day_hours': '8', 'cap_esami_laurea': '45'})

        self.assertEqual(self.caps(), {'esami_laurea': 45})

    def test_invalid_values_save_nothing(self):
        response = self.client.post(self.url, {
            'all_day_hours': '25',
            'cap_esami_laurea': '-1',
            'cap_master': '10',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.timesheet.refresh_from_db()
        self.assertEqual(self.timesheet.all_day_hours, 8)
        self.assertEqual(self.caps(), {})

    def test_all_day_hours_cannot_exceed_the_8_hours_of_a_day(self):
        self.timesheet.all_day_hours = 6
        self.timesheet.save()

        response = self.client.post(self.url, {'all_day_hours': '9'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('all_day_hours', response.context['form'].errors)
        self.timesheet.refresh_from_db()
        self.assertEqual(self.timesheet.all_day_hours, 6)

    def test_saving_goes_back_to_the_page_in_next(self):
        events_page = page('event_compare', self.timesheet)
        url = f"{self.url}?{urlencode({'next': events_page})}"

        response = self.client.post(url, {'all_day_hours': '8'})

        self.assertEqual(response.url, events_page)

    def test_a_next_to_another_site_is_ignored(self):
        url = f"{self.url}?{urlencode({'next': 'https://esempio.com/'})}"

        response = self.client.post(url, {'all_day_hours': '8'})

        self.assertEqual(response.url, '/timesheets/')

    def test_another_docente_timesheet_is_not_found(self):
        _, other = make_docente('altro', 9999)
        foreign = make_timesheet(other)

        response = self.client.get(f'/timesheets/{foreign.pk}/settings/')

        self.assertEqual(response.status_code, 404)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class TimesheetCloseTests(TestCase):

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.url = f'/timesheets/{self.timesheet.pk}/close/'
        self.client.force_login(self.user)

    def test_closing_sets_the_status_and_goes_back_to_the_list(self):
        response = self.client.post(self.url)

        self.assertEqual(response.url, '/timesheets/')
        self.timesheet.refresh_from_db()
        self.assertFalse(self.timesheet.is_open)

    def test_a_closed_timesheet_cannot_be_closed_again(self):
        # Closing is not undoable and goes through the same lock as every write
        self.client.post(self.url)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)

    def test_another_docente_timesheet_is_not_found(self):
        _, other = make_docente('altro', 9999)
        foreign = make_timesheet(other)

        response = self.client.post(f'/timesheets/{foreign.pk}/close/')

        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertTrue(foreign.is_open)

    def test_get_is_rejected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class TimesheetDeleteTests(TestCase):

    def setUp(self):
        self.user, self.docente = make_docente()
        self.client.force_login(self.user)

    def test_the_timesheet_is_deleted_with_its_events(self):
        timesheet = make_timesheet(self.docente)
        make_event(timesheet, 'evt-1', status=Event.Status.COMPILATO)

        response = self.client.post(f'/timesheets/{timesheet.pk}/delete/')

        self.assertEqual(response.url, '/timesheets/')
        self.assertEqual(Timesheet.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)

    def test_a_closed_timesheet_can_be_deleted(self):
        timesheet = make_timesheet(self.docente, status=Timesheet.Status.CLOSED)

        self.client.post(f'/timesheets/{timesheet.pk}/delete/')

        self.assertEqual(Timesheet.objects.count(), 0)

    def test_another_docente_timesheet_is_not_found(self):
        _, other = make_docente('altro', 9999)
        foreign = make_timesheet(other)

        response = self.client.post(f'/timesheets/{foreign.pk}/delete/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Timesheet.objects.count(), 1)

    def test_get_is_rejected(self):
        timesheet = make_timesheet(self.docente)

        response = self.client.get(f'/timesheets/{timesheet.pk}/delete/')

        self.assertEqual(response.status_code, 405)
        self.assertEqual(Timesheet.objects.count(), 1)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class WorkRangeTests(TestCase):
    """The working range travels as ?start=&end= and must fall inside the
    timesheet period. Checked through the event page, the same helper serves
    every page of the flow.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.client.force_login(self.user)

    def _get(self, start, end):
        url = f'/timesheets/{self.timesheet.pk}/events/'
        return self.client.get(url, {'start': start, 'end': end})

    def assertRejected(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/timesheets/')
        self.assertEqual(len(message_texts(response)), 1)

    def test_a_range_inside_the_period_is_accepted(self):
        response = self._get('2026-03-01', '2026-03-31')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['start'], RANGE_START)
        self.assertEqual(response.context['end'], RANGE_END)

    def test_the_whole_period_is_accepted(self):
        response = self._get('2026-01-01', '2026-06-30')

        self.assertEqual(response.status_code, 200)

    def test_a_single_day_is_accepted(self):
        response = self._get('2026-03-10', '2026-03-10')

        self.assertEqual(response.status_code, 200)

    def test_missing_dates_are_rejected(self):
        response = self.client.get(f'/timesheets/{self.timesheet.pk}/events/')

        self.assertRejected(response)

    def test_malformed_dates_are_rejected(self):
        self.assertRejected(self._get('10/03/2026', '2026-03-31'))

    def test_start_after_end_is_rejected(self):
        self.assertRejected(self._get('2026-03-31', '2026-03-01'))

    def test_a_start_before_the_period_is_rejected(self):
        self.assertRejected(self._get('2025-12-31', '2026-03-31'))

    def test_an_end_after_the_period_is_rejected(self):
        self.assertRejected(self._get('2026-03-01', '2026-07-01'))

    def test_another_docente_timesheet_is_not_found(self):
        _, other = make_docente('altro', 9999)
        foreign = make_timesheet(other)

        response = self.client.get(page('event_compare', foreign))

        self.assertEqual(response.status_code, 404)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class SelectCalendarsViewTests(TestCase):

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.url = page('eventi_calendars', self.timesheet)

    @mock.patch('classificationpipeline.views.list_calendars')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_calendar_list_is_mapped_to_summary_id_pairs(self, mock_service, mock_list):
        mock_list.return_value = CALENDAR_LIST_PAYLOAD
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendars'], {'Didattica': CAL_A, 'Ricerca': CAL_B})
        mock_service.assert_called_once_with(self.user)
        mock_list.assert_called_once_with(mock_service.return_value)

    @mock.patch('classificationpipeline.views.list_calendars')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_the_form_posts_to_the_import_with_the_same_range(self, mock_service, mock_list):
        mock_list.return_value = CALENDAR_LIST_PAYLOAD
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        import_url = page('event_compare', self.timesheet)
        self.assertEqual(response.context['import_url'], import_url)
        self.assertContains(response, f'action="{import_url.replace("&", "&amp;")}"')
        # The visible row and the <template> cloned by "Aggiungi calendario"
        self.assertContains(response, 'name="calendars"', count=2)

    @mock.patch('classificationpipeline.views.list_calendars')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_empty_calendar_list_renders_without_error(self, mock_service, mock_list):
        mock_list.return_value = []
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'non ha calendari da leggere')
        self.assertNotContains(response, 'name="calendars"')

    @mock.patch(
        'classificationpipeline.views.get_calendar_service',
        side_effect=GoogleCredentialsError('no token'))
    def test_missing_google_token_redirects_to_google_login(self, mock_service):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"/auth/avvia-login-google/?{urlencode({'next': self.url})}")

    @mock.patch(
        'classificationpipeline.views.list_calendars',
        side_effect=HttpError(httplib2.Response({'status': 403}), b''))
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_consent_without_calendar_permission_redirects_to_google_login(
            self, mock_service, mock_list):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/auth/avvia-login-google/'))

    @mock.patch('classificationpipeline.views.list_calendars')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_an_invalid_range_goes_back_to_the_list_before_any_google_call(
            self, mock_service, mock_list):
        self.client.force_login(self.user)

        response = self.client.get(
            page('eventi_calendars', self.timesheet, RANGE_START, date(2026, 7, 31)))

        self.assertEqual(response.url, '/timesheets/')
        mock_service.assert_not_called()

    @mock.patch('classificationpipeline.views.list_calendars')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_anonymous_user_is_sent_to_login_before_any_google_call(
            self, mock_service, mock_list):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/index/?next='))
        mock_service.assert_not_called()


@override_settings(SHIBBOLETH_DEV_MODE=False)
class EventPageTests(TestCase):
    """GET of event_compare: the active events of the timesheet that start
    inside the working range.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.client.force_login(self.user)

    def _shown(self, response):
        ids = []
        for event in response.context['eventi_classificati']:
            ids.append(event.event_id)
        for event in response.context['eventi_incerti']:
            ids.append(event.event_id)
        return sorted(ids)

    def test_events_are_split_by_status(self):
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)
        make_event(self.timesheet, 'evt-2')

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(response.context['eventi_classificati'][0].event_id, 'evt-1')
        self.assertEqual(response.context['eventi_incerti'][0].event_id, 'evt-2')

    def test_only_events_inside_the_range_are_shown(self):
        make_event(self.timesheet, 'first-day', start=datetime(2026, 3, 1, 0, 0, tzinfo=ROME))
        make_event(self.timesheet, 'last-day', start=datetime(2026, 3, 31, 23, 0, tzinfo=ROME))
        make_event(self.timesheet, 'february', start=datetime(2026, 2, 28, 23, 0, tzinfo=ROME))
        make_event(self.timesheet, 'april', start=datetime(2026, 4, 1, 0, 0, tzinfo=ROME))

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(self._shown(response), ['first-day', 'last-day'])

    def test_compilato_events_are_not_shown(self):
        make_event(self.timesheet, 'evt-1', status=Event.Status.COMPILATO)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(self._shown(response), [])

    def test_events_of_another_timesheet_are_not_shown(self):
        other_timesheet = make_timesheet(self.docente)
        make_event(other_timesheet, 'evt-1')

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(self._shown(response), [])

    def test_all_day_events_are_not_shown_as_midnight_to_midnight(self):
        midnight = datetime(2026, 3, 10, 0, 0, tzinfo=ROME)
        Event.objects.create(
            timesheet=self.timesheet, calendar_id=CAL_A, event_id='evt-1', title='Convegno',
            start_time=midnight, end_time=midnight + timedelta(days=1), all_day=True)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertContains(response, 'Tutto il giorno')
        self.assertNotContains(response, '00:00–00:00')

    def test_an_open_timesheet_shows_the_classification_form_and_the_delete_buttons(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertContains(response, f'name="categoria_{event.pk}"')
        self.assertContains(response, 'id="togli-evento"')
        self.assertContains(response, delete_url(event).replace('&', '&amp;'))

    def test_an_open_timesheet_links_to_its_settings_and_back_here(self):
        response = self.client.get(page('event_compare', self.timesheet))

        next_query = urlencode({'next': page('event_compare', self.timesheet)})
        self.assertContains(response, f'/timesheets/{self.timesheet.pk}/settings/?{next_query}')

    def test_a_closed_timesheet_is_read_only(self):
        self.timesheet.status = Timesheet.Status.CLOSED
        self.timesheet.save()
        event = make_event(self.timesheet, 'evt-1')
        make_event(self.timesheet, 'evt-2', status=Event.Status.CLASSIFICATO)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._shown(response), ['evt-1', 'evt-2'])
        self.assertNotContains(response, f'name="categoria_{event.pk}"')
        self.assertNotContains(response, 'id="togli-evento"')
        self.assertNotContains(response, '/delete/')
        self.assertNotContains(response, '/settings/')

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/index/?next='))


@override_settings(SHIBBOLETH_DEV_MODE=False)
class EventImportTests(TestCase):
    """POST of event_compare: fetches from Google the events of the working
    range, stores them under the timesheet and classifies the incerto ones with
    the docente's hooks.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        # The Hook row is created by the post_save signal on Docente
        self.docente.hook.config = {'preparazione_lezioni': ['lezione']}
        self.docente.hook.save()
        self.timesheet = make_timesheet(self.docente)
        self.url = page('event_compare', self.timesheet)
        self.client.force_login(self.user)

    def _events_payload(self, summary='Calendario A'):
        return {
            'summary': summary,
            'timeZone': 'Europe/Rome',
            'items': [
                {
                    'id': 'ev-lezione',
                    'summary': 'Lezione di Analisi',
                    'start': {'dateTime': MARCH_10.isoformat()},
                    'end': {'dateTime': (MARCH_10 + timedelta(hours=2)).isoformat()},
                },
                {
                    'id': 'ev-riunione',
                    'summary': 'Riunione di dipartimento',
                    'start': {'dateTime': (MARCH_10 + timedelta(hours=3)).isoformat()},
                    'end': {'dateTime': (MARCH_10 + timedelta(hours=4)).isoformat()},
                },
                {
                    'id': 'ev-convegno',
                    'summary': 'Convegno',
                    'start': {'date': '2026-03-12'},
                    'end': {'date': '2026-03-13'},
                },
            ],
        }

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_each_calendar_is_fetched_for_the_working_range(self, mock_service, mock_list):
        mock_list.return_value = {'items': []}

        response = self.client.post(self.url, {'calendars': [CAL_A, CAL_B]})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url)
        mock_service.assert_called_once_with(self.user)
        self.assertEqual(mock_list.call_count, 2)
        calendar_ids = []
        for call in mock_list.call_args_list:
            service, calendar_id, time_min, time_max = call.args
            calendar_ids.append(calendar_id)
            self.assertIs(service, mock_service.return_value)
            self.assertEqual(time_min, datetime(2026, 3, 1, 0, 0, tzinfo=ROME))
            # The end date is included: the bound is the midnight after it
            self.assertEqual(time_max, datetime(2026, 4, 1, 0, 0, tzinfo=ROME))
        self.assertEqual(calendar_ids, [CAL_A, CAL_B])

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_events_are_stored_under_the_timesheet(self, mock_service, mock_list):
        mock_list.return_value = self._events_payload()

        self.client.post(self.url, {'calendars': [CAL_A]})

        events = Event.objects.filter(timesheet=self.timesheet)
        self.assertEqual(
            sorted(events.values_list('event_id', flat=True)),
            ['ev-convegno', 'ev-lezione', 'ev-riunione'])
        lezione = events.get(event_id='ev-lezione')
        self.assertEqual(lezione.calendar_id, CAL_A)
        self.assertEqual(lezione.calendar_title, 'Calendario A')
        self.assertFalse(lezione.all_day)

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_an_all_day_event_is_flagged_and_starts_at_midnight(self, mock_service, mock_list):
        mock_list.return_value = self._events_payload()

        self.client.post(self.url, {'calendars': [CAL_A]})

        convegno = Event.objects.get(event_id='ev-convegno')
        self.assertTrue(convegno.all_day)
        start = timezone.localtime(convegno.start_time)
        self.assertEqual((start.date(), start.hour, start.minute), (date(2026, 3, 12), 0, 0))

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_hook_keyword_classifies_the_event_and_the_rest_stays_incerto(
            self, mock_service, mock_list):
        mock_list.return_value = self._events_payload()

        self.client.post(self.url, {'calendars': [CAL_A]})

        lezione = Event.objects.get(event_id='ev-lezione')
        self.assertEqual(lezione.status, Event.Status.CLASSIFICATO)
        self.assertEqual(lezione.category, Event.Category.PREPARAZIONE_LEZIONI)
        riunione = Event.objects.get(event_id='ev-riunione')
        self.assertEqual(riunione.status, Event.Status.INCERTO)
        self.assertEqual(riunione.category, '')

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_a_misspelled_hook_keyword_still_classifies_the_event(
            self, mock_service, mock_list):
        payload = self._events_payload()
        payload['items'][0]['summary'] = 'Lezzione di Analisi'
        mock_list.return_value = payload

        self.client.post(self.url, {'calendars': [CAL_A]})

        lezione = Event.objects.get(event_id='ev-lezione')
        self.assertEqual(lezione.status, Event.Status.CLASSIFICATO)
        self.assertEqual(lezione.category, Event.Category.PREPARAZIONE_LEZIONI)

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_a_second_import_updates_rows_instead_of_duplicating_them(
            self, mock_service, mock_list):
        mock_list.return_value = self._events_payload()
        self.client.post(self.url, {'calendars': [CAL_A]})

        payload = self._events_payload()
        payload['items'][1]['summary'] = 'Riunione spostata'
        mock_list.return_value = payload
        self.client.post(self.url, {'calendars': [CAL_A]})

        self.assertEqual(Event.objects.count(), 3)
        self.assertEqual(Event.objects.get(event_id='ev-riunione').title, 'Riunione spostata')

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_a_second_import_keeps_status_and_category_of_worked_events(
            self, mock_service, mock_list):
        make_event(
            self.timesheet, 'ev-riunione', status=Event.Status.CLASSIFICATO,
            category=Event.Category.RICEVIMENTO_STUDENTI)
        make_event(
            self.timesheet, 'ev-convegno', status=Event.Status.COMPILATO,
            category=Event.Category.COMPITI_ORGANIZZATIVI)
        mock_list.return_value = self._events_payload()

        self.client.post(self.url, {'calendars': [CAL_A]})

        riunione = Event.objects.get(event_id='ev-riunione')
        self.assertEqual(riunione.status, Event.Status.CLASSIFICATO)
        self.assertEqual(riunione.category, Event.Category.RICEVIMENTO_STUDENTI)
        convegno = Event.objects.get(event_id='ev-convegno')
        self.assertEqual(convegno.status, Event.Status.COMPILATO)
        self.assertEqual(convegno.category, Event.Category.COMPITI_ORGANIZZATIVI)

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_a_deleted_event_comes_back_as_a_new_row_on_the_next_import(
            self, mock_service, mock_list):
        mock_list.return_value = self._events_payload()
        self.client.post(self.url, {'calendars': [CAL_A]})
        riunione = Event.objects.get(event_id='ev-riunione')
        self.client.post(delete_url(riunione))

        self.client.post(self.url, {'calendars': [CAL_A]})

        back = Event.objects.get(event_id='ev-riunione')
        self.assertNotEqual(back.pk, riunione.pk)
        self.assertEqual(back.status, Event.Status.INCERTO)

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_importing_into_two_overlapping_timesheets_gives_one_row_each(
            self, mock_service, mock_list):
        semester = make_timesheet(self.docente)
        mock_list.return_value = self._events_payload()

        self.client.post(self.url, {'calendars': [CAL_A]})
        self.client.post(page('event_compare', semester), {'calendars': [CAL_A]})

        self.assertEqual(Event.objects.filter(event_id='ev-lezione').count(), 2)
        self.assertEqual(Event.objects.filter(timesheet=semester).count(), 3)

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_a_calendar_google_refuses_is_skipped(self, mock_service, mock_list):
        mock_list.side_effect = [
            HttpError(httplib2.Response({'status': 404}), b'Not Found'),
            self._events_payload(summary='Calendario B'),
        ]

        self.client.post(self.url, {'calendars': [CAL_A, CAL_B]})

        calendar_ids = set(Event.objects.values_list('calendar_id', flat=True))
        self.assertEqual(calendar_ids, {CAL_B})

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch(
        'classificationpipeline.views.get_calendar_service',
        side_effect=GoogleCredentialsError('no token'))
    def test_missing_google_token_goes_back_to_the_calendar_selection(
            self, mock_service, mock_list):
        response = self.client.post(self.url, {'calendars': [CAL_A]})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, page('eventi_calendars', self.timesheet))
        mock_list.assert_not_called()

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_post_without_any_calendar_fetches_nothing(self, mock_service, mock_list):
        self.client.post(self.url, {})

        mock_list.assert_not_called()
        self.assertEqual(Event.objects.count(), 0)

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_anonymous_post_is_redirected_to_login_before_any_google_call(
            self, mock_service, mock_list):
        self.client.logout()

        response = self.client.post(self.url, {'calendars': [CAL_A]})

        self.assertTrue(response.url.startswith('/index/?next='))
        mock_service.assert_not_called()


@override_settings(KEYWORD_SIMILARITY_THRESHOLD=0.85)
class MatchCategoryTests(SimpleTestCase):
    """match_category: exact substring first, then similarity between each
    keyword and the groups of title words as long as the keyword.
    """

    def test_title_chunks_have_the_length_of_the_keyword(self):
        self.assertEqual(
            title_chunks('consiglio di dipartimento straordinario', 3),
            ['consiglio di dipartimento', 'di dipartimento straordinario'])

    def test_a_title_shorter_than_the_keyword_is_one_chunk(self):
        self.assertEqual(title_chunks('consiglio  straordinario', 3),
                         ['consiglio straordinario'])

    def test_an_exact_keyword_matches_ignoring_case(self):
        hooks = {'preparazione_lezioni': ['lezione']}

        self.assertEqual(match_category('LEZIONE di Analisi', hooks),
                         'preparazione_lezioni')

    def test_no_match_gives_an_empty_category(self):
        hooks = {'preparazione_lezioni': ['lezione']}

        self.assertEqual(match_category('Riunione', hooks), '')

    def test_empty_hooks_give_an_empty_category(self):
        self.assertEqual(match_category('Lezione', {}), '')

    def test_on_exact_matches_the_first_category_wins(self):
        hooks = {
            'preparazione_lezioni': ['lezione'],
            'ricevimento_studenti': ['ricevimento'],
        }

        self.assertEqual(match_category('Lezione e ricevimento', hooks),
                         'preparazione_lezioni')

    def test_a_misspelled_word_matches_by_similarity(self):
        # 'lezzione' against 'lezione' is about 0.93; the whole title only 0.64
        hooks = {'preparazione_lezioni': ['lezione']}

        self.assertEqual(match_category('Lezzione fisica', hooks),
                         'preparazione_lezioni')

    def test_a_keyword_of_several_words_is_compared_with_groups_of_words(self):
        hooks = {'compiti_organizzativi': ['consiglio di dipartimento']}

        self.assertEqual(
            match_category('Consiglio di dipartmento straordinario', hooks),
            'compiti_organizzativi')

    def test_a_short_keyword_with_one_wrong_letter_stays_below_threshold(self):
        # 'lab' against 'lav' is about 0.67
        hooks = {'didattica_integrativa': ['lab']}

        self.assertEqual(match_category('Lav chimica', hooks), '')

    def test_an_exact_match_wins_over_a_more_similar_earlier_category(self):
        hooks = {
            'ricevimento_studenti': ['lezioni'],
            'preparazione_lezioni': ['lezione'],
        }

        self.assertEqual(match_category('Lezione fisica', hooks),
                         'preparazione_lezioni')

    def test_the_most_similar_category_wins(self):
        # 'esame' against 'esami' is 0.8, 'lezione' against 'lezzione' 0.93
        hooks = {
            'esami_profitto': ['esami'],
            'preparazione_lezioni': ['lezione'],
        }

        with self.settings(KEYWORD_SIMILARITY_THRESHOLD=0.8):
            self.assertEqual(match_category('Esame lezzione', hooks),
                             'preparazione_lezioni')

    def test_on_equal_similarity_the_first_category_wins(self):
        hooks = {
            'preparazione_lezioni': ['lezione'],
            'ricevimento_studenti': ['lezione'],
        }

        self.assertEqual(match_category('Lezzione', hooks),
                         'preparazione_lezioni')

    def test_a_score_equal_to_the_threshold_is_accepted(self):
        # 'esame' against 'esami' is exactly 0.8
        hooks = {'esami_profitto': ['esame']}

        with self.settings(KEYWORD_SIMILARITY_THRESHOLD=0.8):
            self.assertEqual(match_category('Esami', hooks), 'esami_profitto')
        self.assertEqual(match_category('Esami', hooks), '')


@override_settings(SHIBBOLETH_DEV_MODE=False)
class ClassificaIncertiTests(TestCase):
    """Each field of the form is categoria_<pk> and carries one of
    Event.Category's values. Only the incerto events of this timesheet are
    touched; anything else in the POST is ignored.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.url = page('classifica_incerti', self.timesheet)
        self.events_url = page('event_compare', self.timesheet)
        self.client.force_login(self.user)

    def test_chosen_category_classifies_the_event_and_redirects_to_the_events(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.post(
            self.url, {f'categoria_{event.pk}': Event.Category.RICEVIMENTO_STUDENTI})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.events_url)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.CLASSIFICATO)
        self.assertEqual(event.category, Event.Category.RICEVIMENTO_STUDENTI)

    def test_several_events_each_get_their_own_category(self):
        first = make_event(self.timesheet, 'evt-1')
        second = make_event(self.timesheet, 'evt-2')

        self.client.post(self.url, {
            f'categoria_{first.pk}': Event.Category.PREPARAZIONE_LEZIONI,
            f'categoria_{second.pk}': Event.Category.COMPITI_ORGANIZZATIVI,
        })

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.category, Event.Category.PREPARAZIONE_LEZIONI)
        self.assertEqual(second.category, Event.Category.COMPITI_ORGANIZZATIVI)

    def test_an_event_left_without_a_choice_stays_incerto(self):
        chosen = make_event(self.timesheet, 'evt-1')
        skipped = make_event(self.timesheet, 'evt-2')

        self.client.post(self.url, {
            f'categoria_{chosen.pk}': Event.Category.PREPARAZIONE_LEZIONI,
            f'categoria_{skipped.pk}': '',
        })

        skipped.refresh_from_db()
        self.assertEqual(skipped.status, Event.Status.INCERTO)
        self.assertEqual(skipped.category, '')

    def test_an_unknown_category_is_ignored(self):
        event = make_event(self.timesheet, 'evt-1')

        self.client.post(self.url, {f'categoria_{event.pk}': 'vacanza'})

        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)

    def test_malformed_field_names_are_ignored(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.post(self.url, {
            'categoria_abc': Event.Category.PREPARAZIONE_LEZIONI,
            'altro_campo': Event.Category.PREPARAZIONE_LEZIONI,
        })

        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)

    def test_events_of_another_timesheet_are_not_touched(self):
        other_timesheet = make_timesheet(self.docente)
        foreign = make_event(other_timesheet, 'evt-1')

        self.client.post(self.url, {f'categoria_{foreign.pk}': Event.Category.PREPARAZIONE_LEZIONI})

        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Event.Status.INCERTO)

    def test_an_already_classified_event_is_not_rewritten(self):
        event = make_event(
            self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO,
            category=Event.Category.PREPARAZIONE_LEZIONI)

        self.client.post(self.url, {f'categoria_{event.pk}': Event.Category.RICEVIMENTO_STUDENTI})

        event.refresh_from_db()
        self.assertEqual(event.category, Event.Category.PREPARAZIONE_LEZIONI)

    def test_get_changes_nothing_and_redirects_to_the_events(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.get(
            self.url, {f'categoria_{event.pk}': Event.Category.PREPARAZIONE_LEZIONI})

        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)

    def test_anonymous_post_is_redirected_to_login_and_changes_nothing(self):
        event = make_event(self.timesheet, 'evt-1')
        self.client.logout()

        response = self.client.post(
            self.url, {f'categoria_{event.pk}': Event.Category.PREPARAZIONE_LEZIONI})

        self.assertTrue(response.url.startswith('/index/?next='))
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class ClassificaIncertiAiTests(TestCase):
    """The "Classifica con AI" button: the incerto events of the timesheet in
    the working range go to ai_classificator, the categories it returns are
    saved.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.url = page('classifica_incerti_ai', self.timesheet)
        self.events_url = page('event_compare', self.timesheet)
        self.client.force_login(self.user)

    def _sent_pks(self, mock_classify):
        pks = []
        for event in mock_classify.call_args.args[0]:
            pks.append(event.pk)
        return sorted(pks)

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_returned_categories_are_saved_and_user_goes_back_to_the_events(
            self, mock_classify):
        first = make_event(self.timesheet, 'evt-1')
        second = make_event(self.timesheet, 'evt-2')
        mock_classify.return_value = {
            first.pk: Event.Category.PREPARAZIONE_LEZIONI,
            second.pk: Event.Category.COMPITI_ORGANIZZATIVI,
        }

        response = self.client.post(self.url)

        self.assertEqual(response.url, self.events_url)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Event.Status.CLASSIFICATO)
        self.assertEqual(first.category, Event.Category.PREPARAZIONE_LEZIONI)
        self.assertEqual(second.category, Event.Category.COMPITI_ORGANIZZATIVI)

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_an_event_missing_from_the_result_stays_incerto(self, mock_classify):
        classified = make_event(self.timesheet, 'evt-1')
        below_threshold = make_event(self.timesheet, 'evt-2')
        mock_classify.return_value = {classified.pk: Event.Category.PREPARAZIONE_LEZIONI}

        self.client.post(self.url)

        below_threshold.refresh_from_db()
        self.assertEqual(below_threshold.status, Event.Status.INCERTO)

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_only_incerto_events_of_this_timesheet_in_the_range_are_sent(
            self, mock_classify):
        mock_classify.return_value = {}
        own = make_event(self.timesheet, 'evt-1')
        make_event(self.timesheet, 'evt-2', status=Event.Status.CLASSIFICATO)
        make_event(self.timesheet, 'evt-3', status=Event.Status.COMPILATO)
        make_event(self.timesheet, 'evt-4', start=datetime(2026, 2, 10, 10, 0, tzinfo=ROME))
        make_event(self.timesheet, 'evt-5', start=datetime(2026, 4, 10, 10, 0, tzinfo=ROME))
        make_event(make_timesheet(self.docente), 'evt-6')

        self.client.post(self.url)

        self.assertEqual(self._sent_pks(mock_classify), [own.pk])

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_a_result_for_an_event_that_was_not_sent_is_ignored(self, mock_classify):
        foreign = make_event(make_timesheet(self.docente), 'evt-1')
        mock_classify.return_value = {foreign.pk: Event.Category.PREPARAZIONE_LEZIONI}

        self.client.post(self.url)

        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Event.Status.INCERTO)

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_a_failed_classification_changes_nothing_and_warns_the_user(self, mock_classify):
        event = make_event(self.timesheet, 'evt-1')
        mock_classify.side_effect = LLMClassificationError('rete assente')

        response = self.client.post(self.url)

        self.assertEqual(response.url, self.events_url)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.INCERTO)
        texts = message_texts(response)
        self.assertEqual(len(texts), 1)
        self.assertIn('non disponibile', texts[0])

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_get_is_rejected_before_any_call(self, mock_classify):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        mock_classify.assert_not_called()

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_anonymous_post_is_redirected_to_login_before_any_call(self, mock_classify):
        self.client.logout()

        response = self.client.post(self.url)

        self.assertTrue(response.url.startswith('/index/?next='))
        mock_classify.assert_not_called()

    @override_settings(OPENAI_API_KEY='')
    def test_without_incerto_events_the_real_service_is_never_configured(self):
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        response = self.client.post(self.url)

        self.assertEqual(response.url, self.events_url)
        self.assertEqual(message_texts(response), [])


@override_settings(SHIBBOLETH_DEV_MODE=False)
class EventDeleteTests(TestCase):
    """The × next to an event deletes the row. A later import of the same days
    brings it back as incerto (see EventImportTests).
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.client.force_login(self.user)

    def test_an_incerto_event_is_deleted_and_user_goes_back_to_the_events(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.post(delete_url(event))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, page('event_compare', self.timesheet))
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_a_classificato_event_is_deleted(self):
        event = make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        self.client.post(delete_url(event))

        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_only_the_chosen_event_is_deleted(self):
        event = make_event(self.timesheet, 'evt-1')
        kept = make_event(self.timesheet, 'evt-2')

        self.client.post(delete_url(event))

        self.assertEqual(list(Event.objects.all()), [kept])

    def test_a_compilato_event_is_not_deleted(self):
        # It is the only trace of what is already on Esse3
        event = make_event(self.timesheet, 'evt-1', status=Event.Status.COMPILATO)

        response = self.client.post(delete_url(event))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_an_event_of_another_timesheet_is_not_found(self):
        other_timesheet = make_timesheet(self.docente)
        foreign = make_event(other_timesheet, 'evt-1')
        url = reverse('classificationpipeline:event_delete', args=[self.timesheet.pk, foreign.pk])

        response = self.client.post(
            url + '?' + urlencode({'start': RANGE_START, 'end': RANGE_END}))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Event.objects.filter(pk=foreign.pk).exists())

    def test_an_event_of_another_docente_is_not_found(self):
        _, other = make_docente('altro', 9999)
        foreign = make_event(make_timesheet(other), 'evt-1')

        response = self.client.post(delete_url(foreign))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Event.objects.filter(pk=foreign.pk).exists())

    def test_an_invalid_range_deletes_nothing(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.post(delete_url(event, RANGE_START, date(2026, 7, 31)))

        self.assertEqual(response.url, '/timesheets/')
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_get_is_rejected(self):
        event = make_event(self.timesheet, 'evt-1')

        response = self.client.get(delete_url(event))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_anonymous_post_is_redirected_to_login_and_deletes_nothing(self):
        event = make_event(self.timesheet, 'evt-1')
        self.client.logout()

        response = self.client.post(delete_url(event))

        self.assertTrue(response.url.startswith('/index/?next='))
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())


@override_settings(SHIBBOLETH_DEV_MODE=False)
class ClosedTimesheetTests(TestCase):
    """A closed timesheet can be read and deleted, nothing else. Every view
    that writes goes through get_open_timesheet: one test per view, so a new
    view that forgets it shows up here.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente, status=Timesheet.Status.CLOSED)
        self.event = make_event(self.timesheet, 'evt-1')
        self.client.force_login(self.user)

    def assertUntouched(self):
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.INCERTO)
        self.assertEqual(self.event.category, '')

    @mock.patch('classificationpipeline.views.list_calendars')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_calendar_selection_is_forbidden(self, mock_service, mock_list):
        response = self.client.get(page('eventi_calendars', self.timesheet))

        self.assertEqual(response.status_code, 403)
        mock_service.assert_not_called()

    @mock.patch('classificationpipeline.views.list_events')
    @mock.patch('classificationpipeline.views.get_calendar_service')
    def test_import_is_forbidden(self, mock_service, mock_list):
        response = self.client.post(page('event_compare', self.timesheet), {'calendars': [CAL_A]})

        self.assertEqual(response.status_code, 403)
        mock_service.assert_not_called()

    def test_manual_classification_is_forbidden(self):
        response = self.client.post(
            page('classifica_incerti', self.timesheet),
            {f'categoria_{self.event.pk}': Event.Category.PREPARAZIONE_LEZIONI})

        self.assertEqual(response.status_code, 403)
        self.assertUntouched()

    @mock.patch('classificationpipeline.views.ai_classificator')
    def test_ai_classification_is_forbidden(self, mock_classify):
        response = self.client.post(page('classifica_incerti_ai', self.timesheet))

        self.assertEqual(response.status_code, 403)
        mock_classify.assert_not_called()
        self.assertUntouched()

    def test_event_deletion_is_forbidden(self):
        response = self.client.post(delete_url(self.event))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    @mock.patch('classificationpipeline.compilation_sync.write_to_esse3')
    def test_compilation_is_forbidden(self, mock_write):
        make_event(self.timesheet, 'evt-2', status=Event.Status.CLASSIFICATO)

        response = self.client.post(page('timesheet_compile', self.timesheet))

        self.assertEqual(response.status_code, 403)
        mock_write.assert_not_called()
        self.assertFalse(self.timesheet.events.filter(status=Event.Status.COMPILATO).exists())

    def test_settings_are_forbidden(self):
        url = f'/timesheets/{self.timesheet.pk}/settings/'

        self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(url, {'all_day_hours': '4'})

        self.assertEqual(response.status_code, 403)
        self.timesheet.refresh_from_db()
        self.assertEqual(self.timesheet.all_day_hours, 8)


@override_settings(
    OPENAI_API_KEY='chiave-di-test', OPENAI_MODEL='modello-di-test',
    OPENAI_TIMEOUT=12.0, LLM_CONFIDENCE_THRESHOLD=70)
class AiClassificatorTests(TestCase):
    """ai_classificator sends every event in a single request and returns
    {pk: category}. It never writes to the database. The OpenAI client class is
    patched where classificationpipeline.services imports it.
    """

    def setUp(self):
        _, docente = make_docente()
        timesheet = make_timesheet(docente)
        self.lezione = make_event(
            timesheet, 'evt-1', title='Lezione di Analisi', description='Aula M1.4')
        self.riunione = make_event(
            timesheet, 'evt-2', title='Riunione di dipartimento', description=None)

    def _reply(self, content, finish_reason='stop'):
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(finish_reason=finish_reason, message=message)
        return SimpleNamespace(choices=[choice])

    def _rows(self, *rows):
        eventi = []
        for pk, category in rows:
            eventi.append({'id': pk, 'category': category})
        return self._reply(json.dumps({'eventi': eventi}))

    def _create(self, mock_openai):
        return mock_openai.return_value.chat.completions.create

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_no_events_returns_empty_without_building_a_client(self, mock_openai):
        self.assertEqual(ai_classificator([]), {})
        mock_openai.assert_not_called()

    @override_settings(OPENAI_API_KEY='')
    @mock.patch('classificationpipeline.services.OpenAI')
    def test_missing_api_key_raises_without_building_a_client(self, mock_openai):
        with self.assertRaises(LLMClassificationError):
            ai_classificator([self.lezione])
        mock_openai.assert_not_called()

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_each_event_gets_the_category_returned_for_its_id(self, mock_openai):
        self._create(mock_openai).return_value = self._rows(
            (self.lezione.pk, 'preparazione_lezioni'), (self.riunione.pk, 'ricevimento_studenti'))

        result = ai_classificator([self.lezione, self.riunione])

        self.assertEqual(result, {self.lezione.pk: 'preparazione_lezioni', self.riunione.pk: 'ricevimento_studenti'})

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_all_events_travel_in_a_single_request(self, mock_openai):
        self._create(mock_openai).return_value = self._rows()

        ai_classificator([self.lezione, self.riunione])

        self.assertEqual(self._create(mock_openai).call_count, 1)

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_a_null_category_leaves_the_event_out_of_the_result(self, mock_openai):
        self._create(mock_openai).return_value = self._rows(
            (self.lezione.pk, 'preparazione_lezioni'), (self.riunione.pk, None))

        result = ai_classificator([self.lezione, self.riunione])

        self.assertEqual(result, {self.lezione.pk: 'preparazione_lezioni'})

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_an_id_that_was_not_sent_is_discarded(self, mock_openai):
        self._create(mock_openai).return_value = self._rows(
            (self.lezione.pk, 'preparazione_lezioni'), (self.riunione.pk, 'preparazione_lezioni'))

        result = ai_classificator([self.lezione])

        self.assertEqual(result, {self.lezione.pk: 'preparazione_lezioni'})

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_an_unknown_category_is_discarded(self, mock_openai):
        self._create(mock_openai).return_value = self._rows((self.lezione.pk, 'vacanza'))

        self.assertEqual(ai_classificator([self.lezione]), {})

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_the_database_is_not_touched(self, mock_openai):
        self._create(mock_openai).return_value = self._rows((self.lezione.pk, 'preparazione_lezioni'))

        ai_classificator([self.lezione])

        self.lezione.refresh_from_db()
        self.assertEqual(self.lezione.status, Event.Status.INCERTO)
        self.assertEqual(self.lezione.category, '')

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_client_and_request_use_the_configured_settings(self, mock_openai):
        self._create(mock_openai).return_value = self._rows()

        ai_classificator([self.lezione])

        client_kwargs = mock_openai.call_args.kwargs
        self.assertEqual(client_kwargs['api_key'], 'chiave-di-test')
        self.assertEqual(client_kwargs['timeout'], 12.0)
        request_kwargs = self._create(mock_openai).call_args.kwargs
        self.assertEqual(request_kwargs['model'], 'modello-di-test')

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_prompt_carries_threshold_categories_and_event_fields(self, mock_openai):
        self._create(mock_openai).return_value = self._rows()

        ai_classificator([self.lezione, self.riunione])

        messages = self._create(mock_openai).call_args.kwargs['messages']
        system, user = messages[0], messages[1]
        self.assertEqual(system['role'], 'system')
        self.assertIn('70%', system['content'])
        self.assertEqual(user['role'], 'user')
        for code in Event.Category.values:
            self.assertIn(code, user['content'])
        self.assertIn(f'id {self.lezione.pk}', user['content'])
        self.assertIn('Lezione di Analisi', user['content'])
        self.assertIn('Aula M1.4', user['content'])
        self.assertIn('Riunione di dipartimento', user['content'])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_a_missing_description_is_sent_as_empty_text(self, mock_openai):
        self._create(mock_openai).return_value = self._rows()

        ai_classificator([self.riunione])

        user = self._create(mock_openai).call_args.kwargs['messages'][1]
        self.assertNotIn('None', user['content'])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_the_response_schema_allows_only_known_categories_or_null(self, mock_openai):
        self._create(mock_openai).return_value = self._rows()

        ai_classificator([self.lezione])

        response_format = self._create(mock_openai).call_args.kwargs['response_format']
        schema = response_format['json_schema']['schema']
        allowed = schema['properties']['eventi']['items']['properties']['category']['enum']
        self.assertEqual(set(allowed), set(Event.Category.values) | {None})
        self.assertTrue(response_format['json_schema']['strict'])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_an_openai_failure_becomes_a_classification_error(self, mock_openai):
        self._create(mock_openai).side_effect = OpenAIError('rete assente')

        with self.assertRaises(LLMClassificationError):
            ai_classificator([self.lezione])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_a_truncated_reply_is_rejected(self, mock_openai):
        content = json.dumps({'eventi': [{'id': self.lezione.pk, 'category': 'preparazione_lezioni'}]})
        self._create(mock_openai).return_value = self._reply(content, finish_reason='length')

        with self.assertRaises(LLMClassificationError):
            ai_classificator([self.lezione])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_an_empty_reply_is_rejected(self, mock_openai):
        self._create(mock_openai).return_value = self._reply('')

        with self.assertRaises(LLMClassificationError):
            ai_classificator([self.lezione])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_a_reply_that_is_not_json_is_rejected(self, mock_openai):
        self._create(mock_openai).return_value = self._reply('preparazione_lezioni')

        with self.assertRaises(LLMClassificationError):
            ai_classificator([self.lezione])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_a_reply_without_the_events_list_is_rejected(self, mock_openai):
        self._create(mock_openai).return_value = self._reply(json.dumps({'altro': []}))

        with self.assertRaises(LLMClassificationError):
            ai_classificator([self.lezione])

    @mock.patch('classificationpipeline.services.OpenAI')
    def test_a_row_without_id_is_not_turned_into_a_classification_error_known_gap(
            self, mock_openai):
        self._create(mock_openai).return_value = self._reply(
            json.dumps({'eventi': [{'category': 'preparazione_lezioni'}]}))

        with self.assertRaises(KeyError):
            ai_classificator([self.lezione])


class EventHoursTests(SimpleTestCase):
    """Hours of an event per day, in Europe/Rome. Events are built without
    saving them: the functions only read their fields.
    """

    def _timed(self, start, hours, category=Event.Category.ESAMI_PROFITTO):
        return Event(
            start_time=start, end_time=start + timedelta(hours=hours), category=category)

    def _all_day(self, first_day, days, category=Event.Category.ESAMI_LAUREA):
        midnight = datetime(first_day.year, first_day.month, first_day.day, tzinfo=ROME)
        return Event(
            start_time=midnight, end_time=midnight + timedelta(days=days),
            all_day=True, category=category)

    def test_a_timed_event_counts_its_duration_on_its_day(self):
        event = self._timed(MARCH_10, 2)

        self.assertEqual(event_hours(event, 8), {date(2026, 3, 10): 2})

    def test_half_hours_are_kept(self):
        event = self._timed(MARCH_10, 1.5)

        self.assertEqual(event_hours(event, 8), {date(2026, 3, 10): 1.5})

    def test_a_timed_event_past_midnight_counts_on_the_day_it_starts(self):
        event = self._timed(datetime(2026, 3, 10, 23, 0, tzinfo=ROME), 2)

        self.assertEqual(event_hours(event, 8), {date(2026, 3, 10): 2})

    def test_a_one_day_all_day_event_counts_the_set_hours_not_24(self):
        event = self._all_day(date(2026, 3, 10), 1)

        self.assertEqual(event_hours(event, 8), {date(2026, 3, 10): 8})

    def test_a_multi_day_all_day_event_counts_on_each_day(self):
        event = self._all_day(date(2026, 3, 10), 2)

        self.assertEqual(
            event_hours(event, 6), {date(2026, 3, 10): 6, date(2026, 3, 11): 6})

    def test_an_all_day_event_across_the_dst_change_still_counts_each_day(self):
        # 29 March 2026: Italy moves to summer time, that day lasts 23 hours
        event = self._all_day(date(2026, 3, 28), 3)

        self.assertEqual(
            sorted(event_hours(event, 8)),
            [date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)])

    def test_hours_are_summed_per_category(self):
        events = [
            self._timed(MARCH_10, 2, Event.Category.ESAMI_PROFITTO),
            self._timed(MARCH_10 + timedelta(days=1), 3, Event.Category.ESAMI_PROFITTO),
            self._all_day(date(2026, 3, 12), 1, Event.Category.ESAMI_LAUREA),
        ]

        self.assertEqual(hours_by_category(events, 8, RANGE_START, RANGE_END), {
            Event.Category.ESAMI_PROFITTO: 5,
            Event.Category.ESAMI_LAUREA: 8,
        })

    def test_only_the_days_inside_the_range_are_counted(self):
        # 31 March - 2 April, working range March: only the 31st counts
        events = [self._all_day(date(2026, 3, 31), 3)]

        totals = hours_by_category(events, 8, RANGE_START, RANGE_END)

        self.assertEqual(totals, {Event.Category.ESAMI_LAUREA: 8})

    def test_no_events_gives_no_hours(self):
        self.assertEqual(hours_by_category([], 8, RANGE_START, RANGE_END), {})

    def test_events_by_day_gives_the_minutes_of_each_event_on_its_day(self):
        first = self._timed(MARCH_10, 2)
        second = self._timed(MARCH_10 + timedelta(hours=3), 1.5)
        other_day = self._timed(MARCH_10 + timedelta(days=1), 1)

        days = events_by_day([first, second, other_day], 8, RANGE_START, RANGE_END)

        self.assertEqual(days, {
            date(2026, 3, 10): [(first, 120), (second, 90)],
            date(2026, 3, 11): [(other_day, 60)],
        })

    def test_events_by_day_rounds_to_whole_minutes(self):
        # 1h20 is 1.333... hours: 79.99... minutes before rounding
        event = self._timed(MARCH_10, 80 / 60)

        days = events_by_day([event], 8, RANGE_START, RANGE_END)

        self.assertEqual(days, {date(2026, 3, 10): [(event, 80)]})

    def test_events_by_day_splits_an_all_day_event_and_skips_days_outside_the_range(self):
        # 30 March - 2 April, working range March: only the 30th and 31st
        event = self._all_day(date(2026, 3, 30), 4)

        days = events_by_day([event], 6, RANGE_START, RANGE_END)

        self.assertEqual(days, {
            date(2026, 3, 30): [(event, 360)],
            date(2026, 3, 31): [(event, 360)],
        })

    def test_events_by_day_leaves_out_events_outside_the_range(self):
        april = self._timed(datetime(2026, 4, 10, 10, 0, tzinfo=ROME), 2)

        self.assertEqual(events_by_day([april], 8, RANGE_START, RANGE_END), {})


class PlanDayTests(SimpleTestCase):
    """What to write in the diary cells of one day, at most 8 hours in all.
    Events are built without saving them: plan_day only reads their fields.
    """

    def _event(self, hour, category):
        start = datetime(2026, 3, 10, hour, 0, tzinfo=ROME)
        return Event(start_time=start, end_time=start, category=category)

    def test_an_empty_day_takes_every_event_whole(self):
        exam = self._event(9, Event.Category.ESAMI_PROFITTO)
        office_hours = self._event(11, Event.Category.RICEVIMENTO_STUDENTI)

        cells, written = plan_day({}, [(exam, 120), (office_hours, 90)])

        self.assertEqual(cells, {'esami_profitto': 120, 'ricevimento_studenti': 90})
        self.assertEqual(written, [(exam, 120), (office_hours, 90)])

    def test_empty_cells_first_then_the_last_event_that_fits_is_cut(self):
        # 6 hours already in the diary, 2 free: B whole, C cut to 30, A left out
        present = {'esami_profitto': 120, 'preparazione_lezioni': 240}
        a = self._event(9, Event.Category.ESAMI_PROFITTO)
        b = self._event(11, Event.Category.RICEVIMENTO_STUDENTI)
        c = self._event(15, Event.Category.RICEVIMENTO_STUDENTI)

        cells, written = plan_day(present, [(a, 60), (b, 90), (c, 60)])

        self.assertEqual(cells, {'ricevimento_studenti': 120})
        self.assertEqual(written, [(b, 90), (c, 30)])

    def test_a_filled_cell_gets_the_sum(self):
        exam = self._event(9, Event.Category.ESAMI_PROFITTO)

        cells, written = plan_day({'esami_profitto': 120}, [(exam, 60)])

        self.assertEqual(cells, {'esami_profitto': 180})
        self.assertEqual(written, [(exam, 60)])

    def test_a_full_day_is_skipped(self):
        exam = self._event(9, Event.Category.ESAMI_PROFITTO)

        self.assertEqual(plan_day({'master': 480}, [(exam, 60)]), ({}, []))

    def test_a_day_over_8_hours_by_hand_is_skipped(self):
        exam = self._event(9, Event.Category.ESAMI_PROFITTO)

        self.assertEqual(plan_day({'master': 540}, [(exam, 60)]), ({}, []))

    def test_inside_a_group_the_earlier_event_goes_first(self):
        late = self._event(15, Event.Category.MASTER)
        early = self._event(9, Event.Category.DOTTORATI)

        cells, written = plan_day({}, [(late, 300), (early, 300)])

        self.assertEqual(written, [(early, 300), (late, 180)])
        self.assertEqual(cells, {'dottorati': 300, 'master': 180})

    def test_two_events_of_an_empty_cell_add_up_in_that_cell(self):
        first = self._event(9, Event.Category.MASTER)
        second = self._event(11, Event.Category.MASTER)

        cells, written = plan_day({}, [(first, 60), (second, 30)])

        self.assertEqual(cells, {'master': 90})
        self.assertEqual(written, [(first, 60), (second, 30)])

    def test_an_exact_fit_leaves_the_next_event_out(self):
        first = self._event(9, Event.Category.MASTER)
        second = self._event(11, Event.Category.DOTTORATI)

        cells, written = plan_day({'pnrr': 360}, [(first, 120), (second, 60)])

        self.assertEqual(written, [(first, 120)])
        self.assertEqual(cells, {'master': 120})


class HhmmConversionTests(SimpleTestCase):
    """Esse3 diary cells hold HH:MM, we count whole minutes."""

    def test_minutes_to_hhmm(self):
        self.assertEqual(minutes_to_hhmm(0), '00:00')
        self.assertEqual(minutes_to_hhmm(5), '00:05')
        self.assertEqual(minutes_to_hhmm(90), '01:30')
        self.assertEqual(minutes_to_hhmm(480), '08:00')

    def test_59_and_60_minutes_never_give_00_60(self):
        self.assertEqual(minutes_to_hhmm(59), '00:59')
        self.assertEqual(minutes_to_hhmm(60), '01:00')

    def test_hhmm_to_minutes(self):
        self.assertEqual(hhmm_to_minutes('02:30'), 150)
        self.assertEqual(hhmm_to_minutes('08:00'), 480)
        self.assertEqual(hhmm_to_minutes('00:00'), 0)

    def test_an_empty_cell_is_zero_minutes(self):
        self.assertEqual(hhmm_to_minutes(''), 0)

    def test_a_value_survives_the_round_trip(self):
        self.assertEqual(hhmm_to_minutes(minutes_to_hhmm(135)), 135)


class HourCapModelTests(TestCase):

    def setUp(self):
        _, docente = make_docente()
        self.timesheet = make_timesheet(docente)

    def test_a_new_timesheet_has_8_hours_per_all_day_event_and_no_caps(self):
        self.assertEqual(self.timesheet.all_day_hours, 8)
        self.assertIsNone(self.timesheet.total_hours_cap)
        self.assertEqual(self.timesheet.hour_caps.count(), 0)

    def test_two_caps_for_the_same_category_are_rejected(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.MASTER, max_hours=100)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TimesheetHourCap.objects.create(
                    timesheet=self.timesheet, category=Event.Category.MASTER, max_hours=50)

    def test_the_same_category_can_have_a_cap_in_each_timesheet(self):
        other = make_timesheet(self.timesheet.docente)
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.MASTER, max_hours=100)
        TimesheetHourCap.objects.create(
            timesheet=other, category=Event.Category.MASTER, max_hours=50)

        self.assertEqual(TimesheetHourCap.objects.count(), 2)

    def test_caps_are_deleted_with_their_timesheet(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.MASTER, max_hours=100)

        self.timesheet.delete()

        self.assertEqual(TimesheetHourCap.objects.count(), 0)


class CheckCapsTests(TestCase):
    """Compiled hours of the whole timesheet plus the hours about to be
    compiled in the working range, compared with the caps.
    """

    def setUp(self):
        _, docente = make_docente()
        self.timesheet = make_timesheet(docente)

    def cap(self, category, max_hours):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=category, max_hours=max_hours)

    def classified(self):
        return self.timesheet.events.filter(status=Event.Status.CLASSIFICATO)

    def check(self):
        return check_caps(self.timesheet, self.classified(), RANGE_START, RANGE_END)

    def test_no_caps_gives_no_rows(self):
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        self.assertEqual(self.check(), [])

    def test_compiled_hours_of_the_whole_timesheet_add_to_the_new_ones(self):
        self.cap(Event.Category.PREPARAZIONE_LEZIONI, 5)
        make_event(self.timesheet, 'evt-jan', start=datetime(2026, 1, 15, 10, 0, tzinfo=ROME),
                   status=Event.Status.COMPILATO, compile_minutes=120)
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)
        make_event(self.timesheet, 'evt-2', start=MARCH_10 + timedelta(days=1),
                   status=Event.Status.CLASSIFICATO)

        [row] = self.check()

        self.assertEqual(row['label'], 'preparazione lezioni')
        self.assertEqual(row['compiled'], 2)
        self.assertEqual(row['to_compile'], 4)
        self.assertEqual(row['used'], 6)
        self.assertEqual(row['excess'], 1)

    def test_a_cut_compiled_event_counts_only_the_minutes_written(self):
        # 2 hour event, only 1h30 written on Esse3
        self.cap(Event.Category.PREPARAZIONE_LEZIONI, 10)
        make_event(self.timesheet, 'evt-cut', status=Event.Status.COMPILATO, compile_minutes=90)

        [row] = self.check()

        self.assertEqual(row['compiled'], 1.5)

    def test_a_compiled_event_without_minutes_counts_zero(self):
        self.cap(Event.Category.PREPARAZIONE_LEZIONI, 10)
        make_event(self.timesheet, 'evt-1', status=Event.Status.COMPILATO)

        [row] = self.check()

        self.assertEqual(row['compiled'], 0)

    def test_a_cap_reached_exactly_is_not_exceeded(self):
        self.cap(Event.Category.PREPARAZIONE_LEZIONI, 2)
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        [row] = self.check()

        self.assertEqual(row['excess'], 0)

    def test_compiled_events_of_another_timesheet_do_not_count(self):
        self.cap(Event.Category.PREPARAZIONE_LEZIONI, 10)
        other = make_timesheet(self.timesheet.docente)
        make_event(other, 'evt-other', status=Event.Status.COMPILATO)

        [row] = self.check()

        self.assertEqual(row['compiled'], 0)

    def test_an_all_day_event_counts_only_its_days_inside_the_range(self):
        # 31 March - 2 April, 6 hours per day: only the 31st is in March
        self.timesheet.all_day_hours = 6
        self.timesheet.save()
        self.cap(Event.Category.ESAMI_LAUREA, 10)
        midnight = datetime(2026, 3, 31, tzinfo=ROME)
        Event.objects.create(
            timesheet=self.timesheet, calendar_id=CAL_A, event_id='evt-days',
            title='Sessione di laurea', start_time=midnight,
            end_time=midnight + timedelta(days=3), all_day=True,
            status=Event.Status.CLASSIFICATO, category=Event.Category.ESAMI_LAUREA)

        [row] = self.check()

        self.assertEqual(row['to_compile'], 6)

    def test_only_capped_categories_get_a_row_but_the_total_counts_them_all(self):
        self.cap(Event.Category.MASTER, 100)
        self.timesheet.total_hours_cap = 3
        self.timesheet.save()
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)
        make_event(self.timesheet, 'evt-2', start=MARCH_10 + timedelta(days=1),
                   status=Event.Status.CLASSIFICATO, category=Event.Category.MASTER)

        master, total = self.check()

        self.assertEqual(master['label'], 'attività master')
        self.assertEqual(master['used'], 2)
        self.assertEqual(total['label'], 'Totale')
        self.assertEqual(total['used'], 4)
        self.assertEqual(total['excess'], 1)


@override_settings(SHIBBOLETH_DEV_MODE=False)
class EventPageCapsTests(TestCase):

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)
        self.client.force_login(self.user)

    def test_without_caps_there_is_no_caps_box(self):
        response = self.client.get(page('event_compare', self.timesheet))

        self.assertEqual(response.context['caps'], [])
        self.assertNotContains(response, 'Soglie di ore')

    def test_a_cap_within_its_limit_is_shown_without_warning(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.PREPARAZIONE_LEZIONI, max_hours=10)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertFalse(response.context['over_caps'])
        self.assertContains(response, 'Soglie di ore')
        self.assertNotContains(response, 'Alcune soglie sono superate')

    def test_an_exceeded_cap_is_shown_with_a_link_to_the_settings(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.PREPARAZIONE_LEZIONI, max_hours=1)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertTrue(response.context['over_caps'])
        self.assertContains(response, 'Alcune soglie sono superate')
        self.assertContains(response, 'Non si può compilare finché una soglia è superata.')


@override_settings(SHIBBOLETH_DEV_MODE=False)
class TimesheetCompileTests(TestCase):
    """write_to_esse3 is patched: the tests check what compile_events does
    around it, not Esse3.
    """

    def setUp(self):
        self.user, self.docente = make_docente()
        self.timesheet = make_timesheet(self.docente)
        self.url = page('timesheet_compile', self.timesheet)
        self.client.force_login(self.user)
        patcher = mock.patch('classificationpipeline.compilation_sync.write_to_esse3')
        self.mock_write = patcher.start()
        self.addCleanup(patcher.stop)

    def status(self, event):
        event.refresh_from_db()
        return event.status

    def test_classified_events_of_the_range_become_compilato(self):
        first = make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)
        second = make_event(self.timesheet, 'evt-2', start=MARCH_10 + timedelta(days=1),
                            status=Event.Status.CLASSIFICATO)

        response = self.client.post(self.url)

        self.assertEqual(response.url, page('event_compare', self.timesheet))
        self.assertEqual(self.status(first), Event.Status.COMPILATO)
        self.assertEqual(self.status(second), Event.Status.COMPILATO)
        self.assertEqual(message_texts(response), ['Eventi compilati: 2.'])
        [timesheet, events] = self.mock_write.call_args.args
        self.assertEqual(timesheet, self.timesheet)
        self.assertEqual(sorted(event.event_id for event in events), ['evt-1', 'evt-2'])

    def test_uncertain_events_and_events_outside_the_range_are_left_alone(self):
        uncertain = make_event(self.timesheet, 'evt-1')
        april = make_event(self.timesheet, 'evt-2', start=datetime(2026, 4, 10, 10, 0, tzinfo=ROME),
                           status=Event.Status.CLASSIFICATO)

        response = self.client.post(self.url)

        self.assertEqual(self.status(uncertain), Event.Status.INCERTO)
        self.assertEqual(self.status(april), Event.Status.CLASSIFICATO)
        self.assertEqual(message_texts(response), ['Nessun evento classificato da compilare.'])

    def test_an_exceeded_cap_compiles_nothing(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.PREPARAZIONE_LEZIONI, max_hours=1)
        event = make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        response = self.client.post(self.url)

        self.assertEqual(self.status(event), Event.Status.CLASSIFICATO)
        self.mock_write.assert_not_called()
        self.assertEqual(len(message_texts(response)), 1)

    def test_compiled_hours_count_against_the_cap_the_next_time(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.PREPARAZIONE_LEZIONI, max_hours=3)
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)
        self.client.post(self.url)
        later = make_event(self.timesheet, 'evt-2', start=MARCH_10 + timedelta(days=1),
                           status=Event.Status.CLASSIFICATO)

        self.client.post(self.url)

        self.assertEqual(self.status(later), Event.Status.CLASSIFICATO)

    def test_get_is_rejected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_the_button_is_off_when_a_cap_is_exceeded(self):
        TimesheetHourCap.objects.create(
            timesheet=self.timesheet, category=Event.Category.PREPARAZIONE_LEZIONI, max_hours=1)
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertContains(response, 'disabled aria-describedby="nota-compila"')

    def test_the_button_is_on_when_there_is_something_to_compile(self):
        make_event(self.timesheet, 'evt-1', status=Event.Status.CLASSIFICATO)

        response = self.client.get(page('event_compare', self.timesheet))

        self.assertContains(response, self.url.replace('&', '&amp;'))
        self.assertNotContains(response, 'disabled aria-describedby="nota-compila"')
