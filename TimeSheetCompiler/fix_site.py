#!/usr/bin/env python
"""Consolida le righe Site dopo la migrazione a Postgres.

`loaddata` con --natural-primary non conserva le PK: il Site reale del progetto
entra come pk=2 mentre `migrate` ha gia' creato example.com come pk=1. Con
SITE_ID=1 allauth cerca la SocialApp sul sito sbagliato e il login Google non si
risolve.

Uso: python fix_site.py
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.conf import settings
from django.contrib.sites.models import Site
from django.db import transaction
from allauth.socialaccount.models import SocialApp

target_pk = settings.SITE_ID

print("--- prima ---")
for s in Site.objects.all():
    print("site", s.pk, s.domain)
for a in SocialApp.objects.all():
    print("app", a.pk, a.provider, "sites", [x.pk for x in a.sites.all()])

reale = Site.objects.exclude(domain='example.com').order_by('pk').first()
if reale is None:
    sys.exit("ERRORE: nessun Site diverso da example.com. Configura il dominio in admin.")

with transaction.atomic():
    if reale.pk == target_pk:
        canonico = reale
        print(f"\nSite reale gia' su pk={target_pk}, nessuno spostamento.")
    else:
        domain, name = reale.domain, reale.name
        canonico, _ = Site.objects.get_or_create(
            pk=target_pk, defaults={'domain': f'placeholder-{target_pk}.invalid', 'name': 'placeholder'}
        )
        # Site.domain e' unique: il duplicato va rimosso PRIMA di riassegnare il
        # dominio al sito canonico, altrimenti i due coesistono e violano il vincolo.
        for app in SocialApp.objects.all():
            app.sites.set([canonico])
        reale.delete()
        canonico.domain, canonico.name = domain, name
        canonico.save()
        print(f"\nSite consolidato su pk={target_pk} ({canonico.domain}).")

    for app in SocialApp.objects.all():
        app.sites.set([canonico])
        print(f"SocialApp {app.provider} rilegata al sito {canonico.pk}.")

print("\n--- dopo ---")
for s in Site.objects.all():
    print("site", s.pk, s.domain)
for a in SocialApp.objects.all():
    print("app", a.pk, a.provider, "sites", [x.pk for x in a.sites.all()])
