"""Tests for the Google Calendar selection view.

No call actually leaves the process: authentication.services.get_token_valido
is patched where calendars.views imports it, and the Google calendarList call
is patched at calendars.views.requests.get.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from authentication.models import Docente


CALENDAR_LIST_PAYLOAD = {
    'items': [
        {'summary': 'Didattica', 'id': 'didattica@group.calendar.google.com'},
        {'summary': 'Ricerca', 'id': 'ricerca@group.calendar.google.com'},
    ]
}


@override_settings(SHIBBOLETH_DEV_MODE=False)
class SelectCalendarsViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        Docente.objects.create(user=self.user, id_unimore=4242, cognome='Rossi')

    def _google_response(self, payload):
        response = mock.Mock()
        response.json.return_value = payload
        return response

    @mock.patch('calendars.views.requests.get')
    @mock.patch('calendars.views.get_token_valido', return_value='access-token')
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

    @mock.patch('calendars.views.requests.get')
    @mock.patch('calendars.views.get_token_valido', return_value='access-token')
    def test_empty_calendar_list_renders_without_error(self, mock_token, mock_get):
        mock_get.return_value = self._google_response({})
        self.client.force_login(self.user)

        response = self.client.get('/calendars/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendars'], {})

    @mock.patch('calendars.views.get_token_valido', side_effect=Exception('no token'))
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
