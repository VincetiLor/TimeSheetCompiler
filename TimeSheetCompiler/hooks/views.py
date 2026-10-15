from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import FormView
from .models import Hook
from classificationpipeline.models import Event


# One row per category, always all of them, in the order of Event.Category
def config_to_rows(config):
    if not isinstance(config, dict):
        config = {}

    rows = []
    for category, label in Event.Category.choices:
        saved_keywords = config.get(category)
        if not isinstance(saved_keywords, (list, tuple)):
            saved_keywords = []

        text_keywords = []
        for keyword in saved_keywords:
            if isinstance(keyword, str):
                text_keywords.append(keyword)

        rows.append({'category': category, 'keywords': '; '.join(text_keywords)})

    return rows

# row['keywords'] is the list returned by clean_keywords
def rows_to_config(rows):

    config = {}
    for row in rows:
        row_keywords = row['keywords']
        if not row_keywords:
            continue

        saved_keywords = config.setdefault(row['category'], [])
        for keyword in row_keywords:
            if keyword not in saved_keywords:
                saved_keywords.append(keyword)

    return config


class HookRuleForm(forms.Form):
    
    category = forms.ChoiceField(
        choices=Event.Category.choices,
        widget=forms.HiddenInput())
    keywords = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={'class': 'campo', 'placeholder': 'es. lezione; appello'}))

    def category_label(self):
        category = self.initial['category']
        for code, label in Event.Category.choices:
            if category == code: return label


    def clean_keywords(self):
        keywords = self.cleaned_data['keywords']
        cleaned_keywords = []
        for keyword in keywords.split(';'):
            keyword = keyword.strip().lower()
            if keyword == '' or keyword in cleaned_keywords:
                continue
            cleaned_keywords.append(keyword)
        
        return cleaned_keywords
    


class BaseHookRuleFormSet(forms.BaseFormSet):

      def clean(self):  
          
          super().clean()
          if any(self.errors):
              return

          category_per_keyword = {}
          for form in self.forms:
              if not form.cleaned_data:
                  continue
              category = form.cleaned_data['category']
              for keyword in form.cleaned_data['keywords']:
                  previous_category = category_per_keyword.get(keyword)
                  if previous_category is not None and previous_category != category:
                      labels = dict(Event.Category.choices)
                      raise forms.ValidationError(
                          f"La parola chiave «{keyword}» è assegnata sia a "
                          f"«{labels[previous_category]}» sia a «{labels[category]}»: "
                          f"scegline una."
                      )
                  category_per_keyword[keyword] = category

# One fixed row per category, built by config_to_rows: no extra rows, no DELETE
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
