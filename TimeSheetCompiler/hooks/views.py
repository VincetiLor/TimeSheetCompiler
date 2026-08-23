from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import UpdateView
from .models import Hook


class Hook_settings(LoginRequiredMixin, UpdateView):
    model = Hook
    fields = ['config']
    template_name = 'hooks/settings.html'
    success_url = '/hooks/'

    def get_object(self, queryset=None):
        hook, created = Hook.objects.get_or_create(
            docente=self.request.user.docente)
        return hook
