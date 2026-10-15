"""Tests for the per-docente hook configuration view.

The view does not expose the raw JSONField: it renders one fixed row per
category of `Event.Category`, each with a text field of keywords separated by
`;`, and rebuilds the `{category: [keyword, ...]}` mapping on save. What is
pinned here is that round-trip, the splitting and normalisation done once at
save time, the rules the formset refuses, and the guards that were already
true of the old JSON textarea (get_or_create on first visit, isolation between
docenti).

The category of each row comes from `Event.Category`, so a POST that names a
category outside that vocabulary is rejected by the same declaration that
builds the rows.
"""

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase, override_settings

from authentication.models import Docente
from classificationpipeline.models import Event
from hooks.models import Hook


def category_rows(keywords_by_category=None):
    """The rows the page sends: one per category, in vocabulary order, with
    the text typed in the keywords field ('' where nothing was typed).
    """
    if keywords_by_category is None:
        keywords_by_category = {}
    rows = []
    for category, label in Event.Category.choices:
        rows.append({
            'category': category,
            'keywords': keywords_by_category.get(category, ''),
        })
    return rows


def formset_post(rows, initial=None):
    """Build the POST payload a formset expects out of a list of rows.

    The page renders every row from initial data, so the browser sends
    INITIAL_FORMS equal to the number of rows: that is the default here. With
    a lower value Django would treat the unchanged rows as blank extras and
    drop them from cleaned_data.
    """
    if initial is None:
        initial = len(rows)
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

    def _save(self, keywords_by_category):
        return self.client.post(
            '/hooks/', formset_post(category_rows(keywords_by_category)))

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/index/'))
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

    def test_every_category_has_one_row_even_on_first_visit(self):
        """The whole configuration fits in one page: no row to add before a
        category can be given keywords.
        """
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        initials = []
        for form in response.context['form'].forms:
            initials.append(form.initial)
        self.assertEqual(initials, category_rows())

    def test_each_row_is_labelled_with_its_category(self):
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        for category, label in Event.Category.choices:
            self.assertContains(response, label)

    def test_saved_keywords_come_back_joined_in_their_row(self):
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['lezione', 'esame']})
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        initials = []
        for form in response.context['form'].forms:
            initials.append(form.initial)
        self.assertEqual(initials, category_rows(
            {'preparazione_lezioni': 'lezione; esame'}))

    def test_keywords_are_split_on_semicolons(self):
        self.client.force_login(self.user)

        response = self._save(
            {'preparazione_lezioni': 'lezione; esame;seminario'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/hooks/')
        self.assertEqual(self._config(), {
            'preparazione_lezioni': ['lezione', 'esame', 'seminario'],
        })

    def test_a_keyword_is_normalised_once_at_save_time(self):
        """Casing and stray spaces are decided here and never again: every
        consumer downstream matches lowercase substrings and must not have to
        reimplement this rule.
        """
        self.client.force_login(self.user)

        self._save({'preparazione_lezioni': '  Lezione Magistrale  ; ESAME '})

        self.assertEqual(self._config(), {
            'preparazione_lezioni': ['lezione magistrale', 'esame'],
        })

    def test_empty_pieces_are_ignored(self):
        """'' is a substring of every title: an empty piece stored as a keyword
        would classify every event into that category.
        """
        self.client.force_login(self.user)

        self._save({'preparazione_lezioni': ';lezione;; ;'})

        self.assertEqual(self._config(), {'preparazione_lezioni': ['lezione']})

    def test_the_same_keyword_twice_in_one_row_is_stored_once(self):
        self.client.force_login(self.user)

        self._save({'preparazione_lezioni': 'lezione; Lezione'})

        self.assertEqual(self._config(), {'preparazione_lezioni': ['lezione']})

    def test_a_blank_row_stores_no_category(self):
        """A category left empty is simply absent from the config, not mapped
        to an empty list or to an empty keyword.
        """
        self.client.force_login(self.user)

        response = self._save({
            'preparazione_lezioni': 'lezione',
            'esami_profitto': '   ',
            'ricevimento_studenti': ' ; ;',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._config(), {'preparazione_lezioni': ['lezione']})

    def test_keywords_of_several_categories_are_saved_together(self):
        self.client.force_login(self.user)

        self._save({
            'preparazione_lezioni': 'lezione',
            'ricevimento_studenti': 'ricevimento; tesi',
        })

        self.assertEqual(self._config(), {
            'preparazione_lezioni': ['lezione'],
            'ricevimento_studenti': ['ricevimento', 'tesi'],
        })

    def test_editing_a_row_replaces_its_keywords(self):
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['lezione', 'esame']})
        self.client.force_login(self.user)

        response = self._save({'preparazione_lezioni': 'lezione; tutorato'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._config(), {
            'preparazione_lezioni': ['lezione', 'tutorato'],
        })

    def test_emptying_every_row_clears_the_configuration(self):
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['lezione']})
        self.client.force_login(self.user)

        self._save({})

        self.assertEqual(self._config(), {})

    def test_a_delete_flag_is_ignored(self):
        """There is no DELETE checkbox: a hand-made POST that sends one must
        not drop the row behind the user's back.
        """
        self.client.force_login(self.user)
        rows = category_rows({'preparazione_lezioni': 'esame'})
        for row in rows:
            row['DELETE'] = 'on'

        self.client.post('/hooks/', formset_post(rows))

        self.assertEqual(self._config(), {'preparazione_lezioni': ['esame']})

    def test_the_same_keyword_on_two_categories_is_rejected(self):
        """The collision the matcher has no answer for: a title containing that
        substring would hit two rules with no precedence between them. It is
        refused at save time so the matcher never has to resolve it.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['lezione']})
        self.client.force_login(self.user)

        response = self._save({
            'preparazione_lezioni': 'lezione; seminario',
            'ricevimento_studenti': 'tesi; Seminario',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'seminario')
        self.assertTrue(response.context['form'].non_form_errors())
        self.assertEqual(self._config(), {'preparazione_lezioni': ['lezione']})

    def test_moving_a_keyword_to_another_category_is_not_a_collision(self):
        """Moving a keyword means deleting it from one row and typing it in
        another: the old pairing is not in the POST any more.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['seminario']})
        self.client.force_login(self.user)

        response = self._save({'ricevimento_studenti': 'seminario'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._config(), {'ricevimento_studenti': ['seminario']})

    def test_a_category_outside_the_vocabulary_is_rejected(self):
        """The category travels in a hidden field, but the POST carries only
        strings: the server cannot tell a value rendered by the page from one
        sent with curl or left in a tab opened before a release renamed the
        category.
        """
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['lezione']})
        self.client.force_login(self.user)
        rows = category_rows()
        rows[0] = {'category': 'categoria_inesistente', 'keywords': 'lezione'}

        response = self.client.post('/hooks/', formset_post(rows))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._config(), {'preparazione_lezioni': ['lezione']})

    def test_the_accepted_categories_are_the_event_vocabulary(self):
        """One declaration builds the rows and checks the submit. If the two
        drifted apart, a hook could file events under a category the Event
        table refuses.
        """
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        field = response.context['form'].empty_form.fields['category']
        accepted = []
        for value, label in field.choices:
            if value:
                accepted.append(value)
        expected = []
        for value, label in Event.Category.choices:
            expected.append(value)
        self.assertEqual(accepted, expected)

    def test_hook_of_another_docente_is_never_touched(self):
        other_user = User.objects.create_user(username='altro')
        other = Docente.objects.create(user=other_user, id_unimore=7, cognome='Verdi')
        other_hook = Hook.objects.get(docente=other)
        other_hook.config = {'ricevimento_studenti': ['x']}
        other_hook.save()
        self.client.force_login(self.user)

        self._save({'preparazione_lezioni': 'y'})

        other_hook.refresh_from_db()
        self.assertEqual(other_hook.config, {'ricevimento_studenti': ['x']})

    def test_a_config_that_is_not_a_mapping_renders_empty_rows(self):
        """`Hook.config` is a free JSONField and rows predating this form can
        hold anything (the old view accepted a bare `42`). A non-conforming
        config must show as empty rows, not crash the settings page.
        """
        Hook.objects.filter(docente=self.docente).update(config=42)
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        self.assertEqual(response.status_code, 200)
        initials = []
        for form in response.context['form'].forms:
            initials.append(form.initial)
        self.assertEqual(initials, category_rows())

    def test_non_text_saved_keywords_are_left_out_of_the_row(self):
        Hook.objects.filter(docente=self.docente).update(
            config={'preparazione_lezioni': ['lezione', 3],
                    'esami_profitto': 'esame'})
        self.client.force_login(self.user)

        response = self.client.get('/hooks/')

        initials = []
        for form in response.context['form'].forms:
            initials.append(form.initial)
        self.assertEqual(initials, category_rows(
            {'preparazione_lezioni': 'lezione'}))

    def test_saving_over_a_malformed_config_repairs_it(self):
        """The way out of the case above: the first save through this form
        replaces whatever was there with a well-formed mapping.
        """
        Hook.objects.filter(docente=self.docente).update(config=42)
        self.client.force_login(self.user)

        self._save({'preparazione_lezioni': 'lezione'})

        self.assertEqual(self._config(), {'preparazione_lezioni': ['lezione']})

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
