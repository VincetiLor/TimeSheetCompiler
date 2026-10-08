from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth.models import User
from django.shortcuts import render

from authentication.models import Docente


class ShibbolethAuthMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            return self.get_response(request)

        headers = self._read_headers(request)
        if headers:
            if not self._is_allowed(headers):
                return render(
                    request,
                    'authentication/accesso_negato.html',
                    {'affiliazione': headers['affiliation']},
                    status=403,
                )
            self._provision_and_login(request, headers)

        return self.get_response(request)

    def _read_headers(self, request):
        header_map = settings.SHIBBOLETH_HEADER_MAP
        meta = request.META

        if settings.SHIBBOLETH_DEV_MODE and not all(
            meta.get(key) for key in header_map.values()
        ):
            meta = {**meta, **settings.SHIBBOLETH_DEV_FAKE_HEADERS}

        values = {field: meta.get(key) for field, key in header_map.items()}
        if not all(values.values()):
            return None
        return values

    def _is_allowed(self, headers):
        if headers['id_unimore'] in settings.SHIBBOLETH_UID_IN_DEROGA:
            return True
        
        ruoli = {
            valore.split('@')[0].strip().lower()
            for valore in headers['affiliation'].split(';')
        }
        return not ruoli.isdisjoint(settings.SHIBBOLETH_RUOLI_ABILITATI)

    def _provision_and_login(self, request, headers):
        try:
            id_unimore = int(headers['id_unimore'])
        except (TypeError, ValueError):
            return

        docente = Docente.objects.filter(id_unimore=id_unimore).first()
        if docente is None:
            user = User.objects.create_user(username=f'shib-{id_unimore}')
            docente = Docente.objects.create(
                user=user,
                id_unimore=id_unimore,
                nome=headers['nome'],
                cognome=headers['cognome'],
            )
        else:
            user = docente.user
            if docente.nome != headers['nome'] or docente.cognome != headers['cognome']:
                docente.nome = headers['nome']
                docente.cognome = headers['cognome']
                docente.save(update_fields=['nome', 'cognome', 'updated_at'])

        django_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
