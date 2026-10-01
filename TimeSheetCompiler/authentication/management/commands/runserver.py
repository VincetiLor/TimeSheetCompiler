"""runserver che apre /index/ nel browser all'avvio, per comodita' in sviluppo.

Estende il runserver di staticfiles (non quello di core) cosi' i file statici
continuano a essere serviti. Per avere la precedenza, l'app authentication deve
stare prima di django.contrib.staticfiles in INSTALLED_APPS.

Il browser si apre solo nel processo padre: con l'autoreload il figlio
(RUN_MAIN=true) viene riavviato a ogni modifica e riaprirebbe una scheda ogni
volta. Si disattiva con --nobrowser.
"""

import os
import threading
import webbrowser

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)


class Command(StaticfilesRunserverCommand):
    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--nobrowser',
            action='store_true',
            help="Non aprire l'index nel browser all'avvio.",
        )

    def run(self, **options):
        if not options['nobrowser'] and os.environ.get('RUN_MAIN') != 'true':
            # 0.0.0.0 / :: non sono raggiungibili da browser: uso il loopback.
            if self.addr in ('0', '0.0.0.0'):
                host = '127.0.0.1'
            elif self.addr == '::':
                host = '[::1]'
            elif self._raw_ipv6:
                host = f'[{self.addr}]'
            else:
                host = self.addr
            url = f'http://{host}:{self.port}/index/'
            # Ritardo per dare tempo al server di mettersi in ascolto.
            threading.Timer(1.5, webbrowser.open, args=(url,)).start()
        super().run(**options)
