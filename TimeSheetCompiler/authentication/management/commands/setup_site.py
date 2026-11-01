import os

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Creates or updates Site and the Google SocialApp from the environment"

    def add_arguments(self, parser):
        parser.add_argument('domain', help="Example: tsc.ing.unimore.it or localhost:8101")

    def handle(self, *args, **options):
        client_id = os.environ.get('GOOGLE_CLIENT_ID')
        secret = os.environ.get('GOOGLE_CLIENT_SECRET')
        if not client_id or not secret:
            raise CommandError('GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required')

        site, created = Site.objects.update_or_create(
            pk=settings.SITE_ID,
            defaults={'domain': options['domain'], 'name': 'TimeSheetCompiler'},
        )

        app = SocialApp.objects.filter(provider='google').first()
        if app is None:
            app = SocialApp(provider='google', name='Google')
        app.client_id = client_id
        app.secret = secret
        app.save()
        app.sites.add(site)

        self.stdout.write(f'Site and Google app ready for {site.domain}')
