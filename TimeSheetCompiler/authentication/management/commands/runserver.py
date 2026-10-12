"""runserver che apre /index/ nel browser all'avvio, per comodita' in sviluppo.

Estende il runserver di staticfiles (non quello di core) cosi' i file statici
continuano a essere serviti. Per avere la precedenza, l'app authentication deve
stare prima di django.contrib.staticfiles in INSTALLED_APPS.

Il browser si apre solo nel processo padre: con l'autoreload il figlio
(RUN_MAIN=true) viene riavviato a ogni modifica e riaprirebbe una scheda ogni
volta. Si disattiva con --nobrowser.
"""

import os
import socket
import threading
import time
import webbrowser

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)


def open_when_ready(host, port, url):
    for attempt in range(300):
        try:
            connection = socket.create_connection((host, port), timeout=0.5)
        except OSError:
            time.sleep(0.1)
            continue
        connection.close()
        webbrowser.open(url)
        return


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
            threading.Thread(
                target=open_when_ready,
                args=(host.strip('[]'), int(self.port), url),
                daemon=True,
            ).start()
        super().run(**options)
