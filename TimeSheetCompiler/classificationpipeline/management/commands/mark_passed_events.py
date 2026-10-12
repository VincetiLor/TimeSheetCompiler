"""Promuove a 'passato' gli eventi che hanno superato il termine di consegna.
Pensato per girare dal 8 al 12 notte da uno scheduler (cron o django-crontab)
"""

from django.core.management.base import BaseCommand
from classificationpipeline.models import Event, expiry_cutoff


class Command(BaseCommand):
    help = "Promuove a 'passato' gli eventi incerto/classificato fuori tempo massimo"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Conta gli eventi che verrebbero promossi senza scrivere nulla",
        )

    def handle(self, *args, **options):
        cutoff = expiry_cutoff()
        expired = Event.objects.expired()

        if options['dry_run']:
            self.stdout.write(
                f"{expired.count()} eventi da promuovere "
                f"(cutoff {cutoff:%Y-%m-%d})"
            )
            return

        # update() e' una singola UPDATE
        updated = expired.update(status=Event.Status.PASSATO)

        self.stdout.write(self.style.SUCCESS(
            f"{updated} eventi promossi a 'passato' (cutoff {cutoff:%Y-%m-%d})"
        ))
