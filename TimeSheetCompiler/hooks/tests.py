"""Tests for the per-docente hook configuration view.

Covers the JSON round-trip through Hook_settings (an UpdateView over a single
JSONField), the get_or_create-on-first-visit behaviour, and two known open
bugs that are pinned here so a future fix fails loudly instead of silently
changing behaviour.
"""

import json

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase, override_settings

from authentication.models import Docente
from hooks.models import Hook


# SHIBBOLETH_DEV_MODE is read from the environment at settings-import time, so
# without this override a developer with the dev flag set in .env.dev would see
# the middleware log the "anonymous" client in and the guard tests would fail.
@override_settings(SHIBBOLETH_DEV_MODE=False)
class HookSettingsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        self.docente = Docente.objects.create(
            user=self.user, id_unimore=4242, nome='Mario', cognome='Rossi')

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/auth/'))
        # The hook row already exists: it is created by the post_save signal on
        # Docente, not by this view. What matters here is that the anonymous
        # request left it untouched.
        self.assertEqual(Hook.objects.get(docente=self.docente).config, {})

    def test_first_visit_exposes_the_hook_row(self):
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 200)
        hook = Hook.objects.get(docente=self.docente)
        self.assertEqual(hook.config, {})

    def test_second_visit_does_not_create_a_duplicate(self):
        self.client.force_login(self.user)

        self.client.get('/hooks/')
        self.client.get('/hooks/')

        self.assertEqual(Hook.objects.filter(docente=self.docente).count(), 1)

    def test_valid_json_is_saved_and_redirects(self):
        self.client.force_login(self.user)
        config = {'didattica': ['lezione', 'esame'], 'ricerca': ['seminario']}

        response = self.client.post('/hooks/', {'config': json.dumps(config)})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/hooks/')
        self.assertEqual(Hook.objects.get(docente=self.docente).config, config)

    def test_invalid_json_is_rejected_and_leaves_config_untouched(self):
        self.client.force_login(self.user)
        Hook.objects.filter(docente=self.docente).update(
            config={'didattica': ['lezione']})

        response = self.client.post('/hooks/', {'config': 'non e json {'})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'config', ['Enter a valid JSON.'])
        self.assertEqual(
            Hook.objects.get(docente=self.docente).config, {'didattica': ['lezione']})

    def test_hook_of_another_docente_is_never_touched(self):
        other_user = User.objects.create_user(username='altro')
        other = Docente.objects.create(user=other_user, id_unimore=7, cognome='Verdi')
        other_hook = Hook.objects.get(docente=other)
        other_hook.config = {'ricerca': ['x']}
        other_hook.save()
        self.client.force_login(self.user)

        self.client.post('/hooks/', {'config': json.dumps({'didattica': ['y']})})

        other_hook.refresh_from_db()
        self.assertEqual(other_hook.config, {'ricerca': ['x']})

    def test_any_valid_json_scalar_is_accepted_known_gap(self):
        """Known gap: the view exposes the raw JSONField with no schema
        validation, so a bare scalar is stored as the hook configuration even
        though every consumer will expect a mapping of category -> keywords.
        Pinned to document that hook-config validation does not exist yet.
        """
        self.client.force_login(self.user)

        response = self.client.post('/hooks/', {'config': '42'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Hook.objects.get(docente=self.docente).config, 42)

    def test_user_without_docente_crashes_known_bug(self):
        """Known bug: get_object dereferences request.user.docente with no
        guard, so an authenticated user provisioned outside the Shibboleth
        middleware (e.g. the Google-only login path) gets a 500 here instead
        of a usable page.
        """
        orphan = User.objects.create_user(username='senza-docente')
        self.client.force_login(orphan)

        with self.assertRaises(ObjectDoesNotExist):
            self.client.get('/hooks/')
