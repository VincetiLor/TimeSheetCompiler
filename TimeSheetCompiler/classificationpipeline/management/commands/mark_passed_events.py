"""Promuove a 'passato' gli eventi che hanno superato il termine di consegna.

Pensato per girare ogni notte da uno scheduler (cron o django-crontab): il
command e' idempotente, quindi le run nei giorni in cui non scade nulla non
fanno niente e non costa tenerlo giornaliero invece che mensile. Girare solo il
giorno 8 significherebbe perdere il giro se quella notte la macchina e' spenta.
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

        # update() e' una singola UPDATE ... WHERE: nessun oggetto Python
        # istanziato e nessun signal post_save per riga.
        updated = expired.update(status=Event.Status.PASSATO)

        self.stdout.write(self.style.SUCCESS(
            f"{updated} eventi promossi a 'passato' (cutoff {cutoff:%Y-%m-%d})"
        ))
