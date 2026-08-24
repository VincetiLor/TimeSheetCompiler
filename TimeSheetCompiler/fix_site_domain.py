#!/usr/bin/env python
"""Normalizza Site.domain togliendo lo schema URL.

Django si aspetta in `Site.domain` solo host[:porta] (es. '127.0.0.1:8000'):
lo schema viene aggiunto da Django quando costruisce URL assoluti, quindi
lasciarlo dentro produce URL tipo 'http://http://127.0.0.1:8000/...'.

Uso: python fix_site_domain.py
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.contrib.sites.models import Site

for site in Site.objects.all():
    pulito = site.domain.split('://', 1)[-1].rstrip('/')
    if pulito == site.domain:
        print(f"site {site.pk}: {site.domain!r} gia' corretto.")
        continue
    vecchio = site.domain
    site.domain = pulito
    if site.name == vecchio:
        site.name = pulito
    site.save()
    print(f"site {site.pk}: {vecchio!r} -> {site.domain!r}")

print("\n--- stato finale ---")
for s in Site.objects.all():
    print("site", s.pk, repr(s.domain), repr(s.name))
