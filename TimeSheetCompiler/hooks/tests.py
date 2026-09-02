"""Tests for the per-docente hook configuration view.

The view no longer exposes the raw JSONField: it renders one row per rule
(category + keyword) through a formset, and rebuilds the
`{category: [keyword, ...]}` mapping on save. What is pinned here is that
round-trip, the normalisation done once at save time, the rules the formset
refuses, and the guards that were already true of the old JSON textarea
(get_or_create on first visit, isolation between docenti).

The category choices come from `Event.Category`, so a POST that names a
category outside that vocabulary is rejected by the same declaration that
builds the menu - which is the point of moving off the free-form textarea.
"""

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase, override_settings

from authentication.models import Docente
from classificationpipeline.models import Event
from hooks.models import Hook


def formset_post(rows, initial=0):
    """Build the POST payload a formset expects out of a list of rules.

    A formset reads its own size from the management form, so the numbering
    (`form-N-campo`) and the TOTAL_FORMS count are part of the request, not an
    implementation detail the tests can skip. Writing it by hand in every test
    would mean repeating the same bookkeeping ten times.
    """
    data = {
        'form-TOTAL_FORMS': str(len(rows)),
        'form-INITIAL_FORMS': str(initial),
        'form-MIN_NUM_FORMS': '0',
        'form-MAX_NUM_FORMS': '1000',
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f'form-{index}-{field}'] = value
    return data


# SHIBBOLETH_DEV_MODE is read from the environment at settings-import time, so
# without this override a developer with the dev flag set in .env.dev would see
# the middleware log the "anonymous" client in and the guard tests would fail.
@override_settings(SHIBBOLETH_DEV_MODE=False)
class HookSettingsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='docente')
        self.docente = Docente.objects.create(
            user=self.user, id_unimore=4242, nome='Mario', cognome='Rossi')

    def _config(self):
        return Hook.objects.get(docente=self.docente).config

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/auth/'))
        # The hook row already exists: it is created by the post_save signal on
        # Docente, not by this view. What matters here is that the anonymous
        # request left it untouched.
        self.assertEqual(self._config(), {})

    def test_first_visit_exposes_the_hook_row(self):
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._config(), {})

    def test_second_visit_does_not_create_a_duplicate(self):
        self.client.force_login(self.user)

        self.client.get('/hooks/')
        self.client.get('/hooks/')

        self.assertEqual(Hook.objects.filter(docente=self.docente).count(), 1)

    def test_an_empty_form_is_always_offered(self):
        """There is no other way to add the first rule: with no extra form the
        settings page of a docente who never configured anything would have
        nothing to fill in.
        """
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        self.assertEqual(len(response.context['form'].forms), 1)

    def test_saved_rules_come_back_as_one_row_each(self):
        """The stored shape is one category to many keywords; the form shows
        one keyword per row, so a category with two keywords is two rows.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'didattica': ['lezione', 'esame']})
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        rows = [form.initial for form in response.context['form'].forms
                if form.initial]
        self.assertEqual(rows, [
            {'category': 'didattica', 'keyword': 'lezione'},
            {'category': 'didattica', 'keyword': 'esame'},
        ])

    def test_rows_are_saved_grouped_by_category(self):
        self.client.force_login(self.user)

        response = self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'lezione'},
            {'category': 'ricerca_base', 'keyword': 'seminario'},
            {'category': 'didattica', 'keyword': 'esame'},
        ]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/hooks/')
        self.assertEqual(self._config(), {
            'didattica': ['lezione', 'esame'],
            'ricerca_base': ['seminario'],
        })

    def test_a_keyword_is_normalised_once_at_save_time(self):
        """Casing and stray spaces are decided here and never again: every
        consumer downstream matches lowercase substrings and must not have to
        reimplement this rule.
        """
        self.client.force_login(self.user)

        self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': '  Lezione Magistrale  '},
        ]))

        self.assertEqual(self._config(), {'didattica': ['lezione magistrale']})

    def test_submitting_no_rows_clears_the_configuration(self):
        """Deleting the last rule has to be possible, and an empty formset is
        how it arrives.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'didattica': ['lezione']})
        self.client.force_login(self.user)

        self.client.post('/hooks/', formset_post([]))

        self.assertEqual(self._config(), {})

    def test_a_deleted_row_is_not_saved(self):
        self.client.force_login(self.user)

        self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'lezione'},
            {'category': 'didattica', 'keyword': 'esame', 'DELETE': 'on'},
        ]))

        self.assertEqual(self._config(), {'didattica': ['lezione']})

    def test_the_same_keyword_twice_in_one_category_is_stored_once(self):
        """Two identical rows are a repetition, not two rules: they collapse
        instead of producing a duplicated keyword list.
        """
        self.client.force_login(self.user)

        self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'lezione'},
            {'category': 'didattica', 'keyword': 'Lezione'},
        ]))

        self.assertEqual(self._config(), {'didattica': ['lezione']})

    def test_the_same_keyword_on_two_categories_is_rejected(self):
        """The collision the matcher has no answer for: a title containing that
        substring would hit two rules with no precedence between them. It is
        refused at save time so the matcher never has to resolve it.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'didattica': ['lezione']})
        self.client.force_login(self.user)

        response = self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'seminario'},
            {'category': 'ricerca_base', 'keyword': 'seminario'},
        ]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_form_errors())
        self.assertEqual(self._config(), {'didattica': ['lezione']})

    def test_a_deleted_row_does_not_collide(self):
        """Moving a keyword from one category to another means deleting one row
        and adding the other; if deletion were ignored the move would be
        rejected as a collision with itself.
        """
        self.client.force_login(self.user)

        response = self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'seminario', 'DELETE': 'on'},
            {'category': 'ricerca_base', 'keyword': 'seminario'},
        ]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._config(), {'ricerca_base': ['seminario']})

    def test_a_blank_keyword_is_rejected(self):
        """The worst bug this form can ship: '' is a substring of every title,
        so an empty keyword would classify every event into that category and
        empty the incerto branch outright.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'didattica': ['lezione']})
        self.client.force_login(self.user)

        response = self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': '   '},
        ]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._config(), {'didattica': ['lezione']})

    def test_a_category_outside_the_vocabulary_is_rejected(self):
        """The select lives in the page, but the POST carries only strings: the
        server cannot tell a value picked from the menu from one sent with curl
        or left in a tab opened before a release renamed the category.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'didattica': ['lezione']})
        self.client.force_login(self.user)

        response = self.client.post('/hooks/', formset_post([
            {'category': 'categoria_inesistente', 'keyword': 'lezione'},
        ]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._config(), {'didattica': ['lezione']})

    def test_the_offered_categories_are_the_event_vocabulary(self):
        """One declaration builds the menu and checks the submit. If the two
        drifted apart, a hook could file events under a category the Event
        table refuses.
        """
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        field = response.context['form'].forms[0].fields['category']
        self.assertEqual(
            [value for value, _ in field.choices if value],
            [value for value, _ in Event.Category.choices])

    def test_hook_of_another_docente_is_never_touched(self):
        other_user = User.objects.create_user(username='altro')
        other = Docente.objects.create(user=other_user, id_unimore=7, cognome='Verdi')
        other_hook = Hook.objects.get(docente=other)
        other_hook.config = {'ricerca_base': ['x']}
        other_hook.save()
        self.client.force_login(self.user)

        self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'y'},
        ]))

        other_hook.refresh_from_db()
        self.assertEqual(other_hook.config, {'ricerca_base': ['x']})

    def test_a_config_that_is_not_a_mapping_renders_an_empty_form(self):
        """`Hook.config` is a free JSONField and rows predating this form can
        hold anything (the old view accepted a bare `42`). A non-conforming
        config must show as an empty form, not crash the settings page.
        """
        Hook.objects.filter(docente=self.docente).update(config=42)
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [form.initial for form in response.context['form'].forms
             if form.initial],
            [])

    def test_saving_over_a_malformed_config_repairs_it(self):
        """The way out of the known gap above: the first save through this form
        replaces whatever was there with a well-formed mapping.
        """
        Hook.objects.filter(docente=self.docente).update(config=42)
        self.client.force_login(self.user)

        self.client.post('/hooks/', formset_post([
            {'category': 'didattica', 'keyword': 'lezione'},
        ]))

        self.assertEqual(self._config(), {'didattica': ['lezione']})

    def test_user_without_docente_crashes_known_bug(self):
        """Known bug: the view dereferences request.user.docente with no guard,
        so an authenticated user provisioned outside the Shibboleth middleware
        (e.g. the Google-only login path) gets a 500 here instead of a usable
        page.
        """
        orphan = User.objects.create_user(username='senza-docente')
        self.client.force_login(orphan)

        with self.assertRaises(ObjectDoesNotExist):
            self.client.get('/hooks/')
