from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import FormView
from .models import Hook
from classificationpipeline.models import Event


def config_to_rows(config):
    if not isinstance(config, dict):
        return []
    rows = []
    for category, keywords in config.items():
        if not isinstance(keywords, (list, tuple)):
            continue
        for keyword in keywords:
            rows.append({'category': category, 'keyword': keyword})

    return rows

def rows_to_config(rows):

    config = {}
    for row in rows:
        keywords = config.setdefault(row['category'], [])
        if row['keyword'] not in keywords:
            keywords.append(row['keyword'])

    return config


class HookRuleForm(forms.Form):
    
    # La classe 'campo' e' definita in tailwind/input.css: Tailwind non legge
    # i file .py, quindi qui non si possono usare le sue utility.
    category = forms.ChoiceField(
        choices=Event.Category.choices,
        widget=forms.Select(attrs={'class': 'campo'}))
    keyword = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'campo', 'placeholder': 'es. lezione'}))


    def clean_keyword(self):
        return self.cleaned_data['keyword'].strip().lower()

class BaseHookRuleFormSet(forms.BaseFormSet):

      def clean(self):
          
          super().clean()
          if any(self.errors):
              return

          category_per_keyword = {}
          for form in self.forms:
              if not form.cleaned_data:
                  continue
              keyword = form.cleaned_data['keyword']
              category = form.cleaned_data['category']
              previous_category = category_per_keyword.get(keyword)
              if previous_category is not None and previous_category != category:
                  labels = dict(Event.Category.choices)
                  raise forms.ValidationError(
                      f"La parola chiave «{keyword}» è assegnata sia a "
                      f"«{labels[previous_category]}» sia a «{labels[category]}»: "
                      f"scegline una."
                  )
              category_per_keyword[keyword] = category

# Niente riga vuota di default (extra=0) e niente checkbox DELETE: le righe si
# aggiungono e si tolgono dal fondo con i due bottoni della pagina, e una riga
# tolta semplicemente non arriva nel POST. Vedi DECISIONS.md.
HookRuleFormSet = forms.formset_factory(
      HookRuleForm, formset=BaseHookRuleFormSet, extra=0)



class Hook_settings(LoginRequiredMixin, FormView):
      template_name = 'hooks/settings.html'
      form_class = HookRuleFormSet
      success_url = '/hooks/'

      def get_hook(self):
          hook, _ = Hook.objects.get_or_create(docente=self.request.user.docente)
          return hook

      def get_initial(self):
          return config_to_rows(self.get_hook().config)

      def form_valid(self, formset):
          rows = [form.cleaned_data for form in formset.forms if form.cleaned_data]
          hook = self.get_hook()
          hook.config = rows_to_config(rows)
          hook.save()
          return super().form_valid(formset)
