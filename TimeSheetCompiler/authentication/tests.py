"""Tests for the Shibboleth provisioning middleware and the Google token service.

Note on the Shibboleth container: none of these tests require it. The SP
container's only job is to inject request headers, and the Django test client
can inject those headers directly, so the middleware is testable in isolation.

Note on Google OAuth: the handshake itself belongs to allauth and to Google's
servers and is not tested here. What is tested is our consumer of the stored
token (get_token_valido), with the outbound HTTP calls mocked.
"""

from unittest import mock

from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.exceptions import MultipleObjectsReturned
from django.db.models.signals import post_save
from django.test import TestCase, override_settings

from authentication.models import Docente
from authentication.services import get_token_valido
from hooks.models import Hook


SHIB_HEADERS = {
    'shib-givenname': 'Mario',
    'shib-sn': 'Rossi',
    'shib-unique-id': '4242',
}



def _create_google_app():
    """get_token_valido reads the Google SocialApp to refresh tokens, so the
    tests that exercise it need a configured app row.
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
    """

    def setUp(self):
        _create_google_app()

    def test_form_posts_to_allauth_with_next(self):
        response = self.client.get(
            '/auth/avvia-login-google/', {'next': '/calendars/'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'action="/accounts/google/login/')
        self.assertContains(response, 'name="next" value="/calendars/"')

    def test_missing_next_falls_back_to_home(self):
        response = self.client.get('/auth/avvia-login-google/')

        self.assertContains(response, 'name="next" value="/index/"')


class GetTokenValidoTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        self.app = _create_google_app()
        self.account = SocialAccount.objects.create(
            user=self.user, provider='google', uid='uid-1')
        self.token = SocialToken.objects.create(
            app=self.app, account=self.account,
            token='access-old', token_secret='refresh-1')

    def _response(self, status_code, payload=None):
        response = mock.Mock()
        response.status_code = status_code
        response.json.return_value = payload or {}
        return response

    @mock.patch('authentication.services.requests.post')
    @mock.patch('authentication.services.requests.get')
    def test_valid_token_is_returned_without_refresh(self, mock_get, mock_post):
        mock_get.return_value = self._response(200)

        self.assertEqual(get_token_valido(self.user), 'access-old')
        mock_post.assert_not_called()
        self.token.refresh_from_db()
        self.assertEqual(self.token.token, 'access-old')

    @mock.patch('authentication.services.requests.post')
    @mock.patch('authentication.services.requests.get')
    def test_expired_token_is_refreshed_and_persisted(self, mock_get, mock_post):
        mock_get.return_value = self._response(401)
        mock_post.return_value = self._response(200, {'access_token': 'access-new'})

        self.assertEqual(get_token_valido(self.user), 'access-new')

        self.token.refresh_from_db()
        self.assertEqual(self.token.token, 'access-new')
        sent = mock_post.call_args.kwargs['data']
        self.assertEqual(sent['refresh_token'], 'refresh-1')
        self.assertEqual(sent['grant_type'], 'refresh_token')
        self.assertEqual(sent['client_id'], 'cid')

    @mock.patch('authentication.services.requests.post')
    @mock.patch('authentication.services.requests.get')
    def test_failed_refresh_raises_and_leaves_token_untouched(self, mock_get, mock_post):
        mock_get.return_value = self._response(401)
        mock_post.return_value = self._response(400, {'error': 'invalid_grant'})

        with self.assertRaises(Exception) as ctx:
            get_token_valido(self.user)
        self.assertIn('Impossibile rinnovare il token', str(ctx.exception))

        self.token.refresh_from_db()
        self.assertEqual(self.token.token, 'access-old')

    @mock.patch('authentication.services.requests.get')
    def test_user_without_token_raises_does_not_exist(self, mock_get):
        other = User.objects.create_user(username='senza-token')

        with self.assertRaises(SocialToken.DoesNotExist):
            get_token_valido(other)
        mock_get.assert_not_called()

    @mock.patch('authentication.services.requests.post')
    @mock.patch('authentication.services.requests.get')
    def test_duplicate_social_app_raises_multiple_objects_returned(
            self, mock_get, mock_post):
        """Documents open bug: a second google SocialApp row (a classic
        result of loading a fixture on top of an admin-created app) makes the
        refresh path explode with an error unrelated to the token, which the
        bare except in SelectCalendarsView then reports as 'login again'.
        """
        SocialApp.objects.create(
            provider='google', name='google-dup', client_id='cid2', secret='s2')
        mock_get.return_value = self._response(401)

        with self.assertRaises(MultipleObjectsReturned):
            get_token_valido(self.user)
        mock_post.assert_not_called()


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
