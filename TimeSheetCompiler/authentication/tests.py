"""Tests for the Shibboleth provisioning middleware and the Google token service.

Note on the Shibboleth container: none of these tests require it. The SP
container's only job is to inject request headers, and the Django test client
can inject those headers directly, so the middleware is testable in isolation.

Note on Google OAuth: the handshake itself belongs to allauth and to Google's
servers and is not tested here. What is tested is our consumer of the stored
token (authentication.services), with the outbound HTTP calls mocked: the
refresh at the transport handed to google-auth, the Calendar calls with
googleapiclient's HttpMockSequence.
"""

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock
from urllib.parse import parse_qs, urlparse

from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.utils import timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from authentication.models import Docente
from authentication.services import (
    GoogleCredentialsError,
    get_credentials,
    list_calendars,
    list_events,
)
from hooks.models import Hook


SHIB_HEADERS = {
    'shib-givenname': 'Mario',
    'shib-sn': 'Rossi',
    'shib-unique-id': '4242',
}



def _create_google_app():
    """get_credentials reads client id and secret from the Google SocialApp
    linked to the token, so the tests that exercise it need a configured row.
    """
    app = SocialApp.objects.create(
        provider='google', name='google', client_id='cid', secret='secret')
    app.sites.add(Site.objects.get_current())
    return app


@override_settings(SHIBBOLETH_DEV_MODE=False)
class ShibbolethMiddlewareTests(TestCase):
    """Exercises the middleware through the real stack (/index/), because
    django_login needs a session and the middleware is declared in MIDDLEWARE.
    """

    def setUp(self):
        _create_google_app()

    def test_provisions_user_and_docente_on_first_hit(self):
        response = self.client.get('/index/', headers=SHIB_HEADERS)

        self.assertEqual(response.status_code, 200)
        docente = Docente.objects.get(id_unimore=4242)
        self.assertEqual(docente.nome, 'Mario')
        self.assertEqual(docente.cognome, 'Rossi')
        self.assertEqual(docente.user.username, 'shib-4242')
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_second_hit_reuses_existing_docente(self):
        self.client.get('/index/', headers=SHIB_HEADERS)
        self.client.logout()
        self.client.get('/index/', headers=SHIB_HEADERS)

        self.assertEqual(User.objects.filter(username='shib-4242').count(), 1)
        self.assertEqual(Docente.objects.filter(id_unimore=4242).count(), 1)

    def test_name_change_is_synced_on_existing_docente(self):
        self.client.get('/index/', headers=SHIB_HEADERS)
        self.client.logout()

        self.client.get('/index/', headers={**SHIB_HEADERS, 'shib-sn': 'Bianchi'})

        docente = Docente.objects.get(id_unimore=4242)
        self.assertEqual(docente.cognome, 'Bianchi')

    def test_no_headers_passes_through_anonymous(self):
        response = self.client.get('/index/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertEqual(Docente.objects.count(), 0)

    def test_partial_headers_provision_nothing(self):
        response = self.client.get('/index/', headers={'shib-unique-id': '4242'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Docente.objects.count(), 0)

    def test_non_numeric_id_provisions_nothing_but_serves_request(self):
        response = self.client.get(
            '/index/', headers={**SHIB_HEADERS, 'shib-unique-id': 'not-a-number'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Docente.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)

    def test_already_authenticated_user_is_not_reprovisioned(self):
        user = User.objects.create_user(username='local', password='pw')
        self.client.force_login(user)

        self.client.get('/index/', headers=SHIB_HEADERS)

        self.assertEqual(Docente.objects.count(), 0)
        self.assertEqual(User.objects.count(), 1)


@override_settings(
    SHIBBOLETH_DEV_MODE=True,
    SHIBBOLETH_DEV_FAKE_HEADERS={
        'HTTP_SHIB_GIVENNAME': 'Dev',
        'HTTP_SHIB_SN': 'Tester',
        'HTTP_SHIB_UNIQUE_ID': '1',
    },
)
class ShibbolethDevModeTests(TestCase):
    """SHIBBOLETH_DEV_MODE is computed at import time from DEBUG and an env
    var, so it can only be flipped in tests via override_settings - setting
    the environment variable inside a test has no effect.
    """

    def setUp(self):
        _create_google_app()

    def test_dev_headers_are_used_when_none_are_present(self):
        self.client.get('/index/')

        docente = Docente.objects.get(id_unimore=1)
        self.assertEqual(docente.nome, 'Dev')
        self.assertEqual(docente.cognome, 'Tester')

    def test_dev_headers_override_partially_present_real_headers(self):
        """Known behaviour: _read_headers merges as {**meta, **FAKE}, so the
        fake values win over real ones whenever any single header is missing.
        A partially-configured SP would silently log everyone in as the same
        dev identity. Pinned here so the merge order cannot change unnoticed.
        """
        self.client.get('/index/', headers={'shib-unique-id': '999'})

        self.assertFalse(Docente.objects.filter(id_unimore=999).exists())
        self.assertTrue(Docente.objects.filter(id_unimore=1).exists())

    def test_complete_real_headers_win_over_dev_headers(self):
        self.client.get('/index/', headers=SHIB_HEADERS)

        self.assertTrue(Docente.objects.filter(id_unimore=4242).exists())
        self.assertFalse(Docente.objects.filter(id_unimore=1).exists())


@override_settings(SHIBBOLETH_DEV_MODE=False)
class AvviaLoginGoogleTests(TestCase):
    """The page auto-submits a POST to allauth's provider login URL, which is
    what actually starts the OAuth handshake (a GET would stop on allauth's
    confirmation page). The handshake itself is not tested.

    The form asks allauth for process=connect: the Google account is attached
    to the user already in session (the Shibboleth one). With process=login
    allauth would log that user out and enter as the owner of the Google
    account, a different User without a Docente.
    """

    def setUp(self):
        _create_google_app()
        self.user = User.objects.create_user(username='docente')

    def test_form_posts_to_allauth_with_next(self):
        self.client.force_login(self.user)

        response = self.client.get(
            '/auth/avvia-login-google/', {'next': '/calendars/'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'action="/accounts/google/login/')
        self.assertContains(response, 'name="next" value="/calendars/"')

    def test_form_connects_instead_of_logging_in(self):
        self.client.force_login(self.user)

        response = self.client.get('/auth/avvia-login-google/')

        self.assertContains(response, 'process=connect')
        self.assertNotContains(response, 'process=login')

    def test_missing_next_falls_back_to_home(self):
        self.client.force_login(self.user)

        response = self.client.get('/auth/avvia-login-google/')

        self.assertContains(response, 'name="next" value="/index/"')

    def test_anonymous_user_is_sent_to_login_url(self):
        response = self.client.get('/auth/avvia-login-google/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/index/?next=/auth/avvia-login-google/')


class GetCredentialsTests(TestCase):
    """The refresh goes through google-auth for real: only the transport
    (authentication.services.Request) is replaced, so what is asserted is the
    request google-auth would send to Google's token endpoint.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        self.app = _create_google_app()
        self.account = SocialAccount.objects.create(
            user=self.user, provider='google', uid='uid-1')
        self.token = SocialToken.objects.create(
            app=self.app, account=self.account,
            token='access-old', token_secret='refresh-1',
            expires_at=timezone.now() + timedelta(hours=1))

    def _expire(self):
        self.token.expires_at = timezone.now() - timedelta(hours=1)
        self.token.save()

    def _transport(self, mock_request_cls, status, payload):
        transport = mock_request_cls.return_value
        transport.return_value = mock.Mock(
            status=status, data=json.dumps(payload).encode())
        return transport

    def _sent_form(self, transport):
        body = transport.call_args.kwargs['body']
        if isinstance(body, bytes):
            body = body.decode()
        return {key: values[0] for key, values in parse_qs(body).items()}

    @mock.patch('authentication.services.Request')
    def test_unexpired_token_is_returned_without_refresh(self, mock_request_cls):
        credentials = get_credentials(self.user)

        self.assertEqual(credentials.token, 'access-old')
        mock_request_cls.return_value.assert_not_called()

    @mock.patch('authentication.services.Request')
    def test_expired_token_is_refreshed_and_persisted(self, mock_request_cls):
        self._expire()
        transport = self._transport(
            mock_request_cls, 200, {'access_token': 'access-new', 'expires_in': 3600})

        credentials = get_credentials(self.user)

        self.assertEqual(credentials.token, 'access-new')
        self.token.refresh_from_db()
        self.assertEqual(self.token.token, 'access-new')
        self.assertEqual(self.token.token_secret, 'refresh-1')
        self.assertGreater(self.token.expires_at, timezone.now())
        sent = self._sent_form(transport)
        self.assertEqual(sent['refresh_token'], 'refresh-1')
        self.assertEqual(sent['grant_type'], 'refresh_token')
        self.assertEqual(sent['client_id'], 'cid')
        self.assertEqual(sent['client_secret'], 'secret')
        self.assertIsNotNone(transport.call_args.kwargs['timeout'])

    @mock.patch('authentication.services.Request')
    def test_token_without_known_expiry_is_refreshed(self, mock_request_cls):
        """Rows written before expires_at was kept up to date: with no expiry
        google-auth would consider the token valid forever.
        """
        self.token.expires_at = None
        self.token.save()
        self._transport(
            mock_request_cls, 200, {'access_token': 'access-new', 'expires_in': 3600})

        self.assertEqual(get_credentials(self.user).token, 'access-new')
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.expires_at)

    @mock.patch('authentication.services.Request')
    def test_failed_refresh_raises_and_leaves_token_untouched(self, mock_request_cls):
        self._expire()
        self._transport(mock_request_cls, 400, {'error': 'invalid_grant'})

        with self.assertRaises(GoogleCredentialsError):
            get_credentials(self.user)

        self.token.refresh_from_db()
        self.assertEqual(self.token.token, 'access-old')

    @mock.patch('authentication.services.Request')
    def test_expired_token_without_refresh_token_raises(self, mock_request_cls):
        self._expire()
        self.token.token_secret = ''
        self.token.save()

        with self.assertRaises(GoogleCredentialsError):
            get_credentials(self.user)
        mock_request_cls.return_value.assert_not_called()

    def test_user_without_token_raises(self):
        other = User.objects.create_user(username='senza-token')

        with self.assertRaises(GoogleCredentialsError):
            get_credentials(other)

    @mock.patch('authentication.services.Request')
    def test_duplicate_social_app_does_not_break_the_refresh(self, mock_request_cls):
        """Client id and secret come from the app linked to the token, not from
        a lookup by provider: a second google SocialApp row (a classic result
        of loading a fixture on top of an admin-created app) is harmless.
        """
        SocialApp.objects.create(
            provider='google', name='google-dup', client_id='cid2', secret='s2')
        self._expire()
        transport = self._transport(
            mock_request_cls, 200, {'access_token': 'access-new', 'expires_in': 3600})

        get_credentials(self.user)

        self.assertEqual(self._sent_form(transport)['client_id'], 'cid')

    def test_with_two_google_accounts_the_last_authorized_one_is_used(self):
        second = SocialAccount.objects.create(
            user=self.user, provider='google', uid='uid-2')
        SocialToken.objects.create(
            app=self.app, account=second,
            token='access-second', token_secret='refresh-2',
            expires_at=timezone.now() + timedelta(hours=1))

        self.assertEqual(get_credentials(self.user).token, 'access-second')

        # last_login is auto_now: saving the first account makes it the latest.
        self.account.save()
        self.assertEqual(get_credentials(self.user).token, 'access-old')


class CalendarApiTests(TestCase):
    """list_calendars/list_events against googleapiclient's HttpMockSequence:
    the real client builds the requests, the canned responses replace Google.
    """

    def _service(self, *responses):
        self.http = HttpMockSequence([
            ({'status': str(status)}, json.dumps(payload))
            for status, payload in responses
        ])
        return build('calendar', 'v3', http=self.http, cache_discovery=False)

    def _query(self, index=-1):
        return parse_qs(urlparse(self.http.request_sequence[index][0]).query)

    def test_list_calendars_follows_every_page(self):
        service = self._service(
            (200, {'items': [{'id': 'a', 'summary': 'A'}], 'nextPageToken': 'p2'}),
            (200, {'items': [{'id': 'b', 'summary': 'B'}]}),
        )

        calendars = list_calendars(service)

        self.assertEqual([c['id'] for c in calendars], ['a', 'b'])
        self.assertEqual(self._query()['pageToken'], ['p2'])

    def test_list_calendars_without_items_is_empty(self):
        self.assertEqual(list_calendars(self._service((200, {}))), [])

    def test_list_events_merges_pages_and_keeps_calendar_metadata(self):
        service = self._service(
            (200, {'summary': 'Cal', 'timeZone': 'Europe/Rome',
                   'items': [{'id': 'e1'}], 'nextPageToken': 'p2'}),
            (200, {'summary': 'Cal', 'timeZone': 'Europe/Rome',
                   'items': [{'id': 'e2'}]}),
        )
        start = datetime(2026, 10, 1, tzinfo=dt_timezone.utc)
        end = datetime(2026, 11, 1, tzinfo=dt_timezone.utc)

        data = list_events(service, 'cal@google.com', start, end)

        self.assertEqual(data['summary'], 'Cal')
        self.assertEqual(data['timeZone'], 'Europe/Rome')
        self.assertEqual([e['id'] for e in data['items']], ['e1', 'e2'])
        self.assertNotIn('nextPageToken', data)
        first = self._query(0)
        self.assertEqual(first['singleEvents'], ['true'])
        self.assertEqual(first['timeMin'], [start.isoformat()])
        self.assertEqual(first['timeMax'], [end.isoformat()])

    def test_calendar_id_is_escaped_in_the_url(self):
        """Holiday calendars have a '#' in the id: unescaped, everything after
        it would be dropped as a URL fragment.
        """
        service = self._service((200, {'items': []}))
        start = datetime(2026, 10, 1, tzinfo=dt_timezone.utc)

        list_events(service, 'it.italian#holiday@group.v.calendar.google.com',
                    start, start + timedelta(days=31))

        path = urlparse(self.http.request_sequence[0][0]).path
        self.assertIn('it.italian%23holiday%40group.v.calendar.google.com', path)

    def test_calendar_refused_by_google_raises_http_error(self):
        service = self._service((404, {'error': {'message': 'Not Found'}}))
        start = datetime(2026, 10, 1, tzinfo=dt_timezone.utc)

        with self.assertRaises(HttpError):
            list_events(service, 'gone@google.com', start, start + timedelta(days=31))


class HookProvisioningSignalTests(TestCase):
    """The post_save receiver on Docente is what guarantees the OneToOne to
    Hook is never missing, whatever path created the Docente.
    """

    def _docente(self, username='docente', id_unimore=4242):
        user = User.objects.create_user(username=username)
        return Docente.objects.create(
            user=user, id_unimore=id_unimore, nome='Mario', cognome='Rossi')

    def test_creating_a_docente_creates_its_hook(self):
        docente = self._docente()

        self.assertEqual(docente.hook.config, {})

    def test_updating_a_docente_does_not_create_a_second_hook(self):
        docente = self._docente()
        docente.hook.config = {'didattica': ['lezione']}
        docente.hook.save()

        docente.cognome = 'Bianchi'
        docente.save()

        self.assertEqual(Hook.objects.filter(docente=docente).count(), 1)
        self.assertEqual(
            Hook.objects.get(docente=docente).config, {'didattica': ['lezione']})

    def test_fixture_loads_are_ignored(self):
        """raw=True marks a save replayed by loaddata: the fixture already
        carries its own Hook rows, so the receiver must stay out of the way.
        """
        docente = self._docente()
        Hook.objects.filter(docente=docente).delete()

        post_save.send(
            sender=Docente, instance=docente, created=True, raw=True)

        self.assertFalse(Hook.objects.filter(docente=docente).exists())
