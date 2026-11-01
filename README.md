# TimeSheetCompiler

Applicazione Django che legge gli eventi da Google Calendar, li classifica
(parole chiave, scelta manuale o AI) e compila il diario delle attività su Esse3
con un bot Selenium.

Questa guida serve a farla girare in locale. Serve solo Docker con il plugin
Compose: database, browser e dipendenze Python stanno nei container. Provata solo
su Linux x86-64.

## Avvio

```bash
git clone https://github.com/VincetiLor/TimeSheetCompiler.git
cd TimeSheetCompiler/TimeSheetCompiler     # la cartella con manage.py
cp .env.example .env
```

Genera i tre valori obbligatori:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"     # DJANGO_SECRET_KEY
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"     # GOOGLE_TOKEN_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"     # DB_PASSWORD
```

Incollali in `.env`. `DEV_PORT` può essere una qualsiasi porta libera (qui 8101);
il resto del file può restare com'è. `.env` non va mai committato.

```bash
docker compose up -d --build
docker compose logs -f tsc-dev     # Ctrl+C esce dai log, non ferma l'app
```

Apri `http://localhost:8101/index/`. Usa sempre `localhost`: il consenso Google
funziona solo con quell'indirizzo.

Per fermare tutto: `docker compose down`. Con `-v` cancella anche il database.

## Login senza Shibboleth

In locale non c'è il container Shibboleth, e non serve: `docker-compose.yml` attiva
da solo `SHIBBOLETH_DEV_MODE`, che simula gli header `X-Shib-*`. Non c'è codice da
cambiare e si entra senza login, con l'identità scritta in `.env`:

| Variabile | Significato |
|---|---|
| `SHIBBOLETH_DEV_ID` | Identificativo Unimore. Deve essere **numerico**: altrimenti il login fallisce senza errori. È anche lo username usato su Esse3 |
| `SHIBBOLETH_DEV_NOME` | Nome |
| `SHIBBOLETH_DEV_COGNOME` | Cognome |
| `SHIBBOLETH_DEV_AFFILIATION` | Ruoli separati da `;`. Serve almeno `faculty`, `staff` o `employee`: senza, la pagina risponde 403 |

Dopo aver cambiato `.env` lancia `docker compose up -d`: `restart` non rilegge il
file. Un `SHIBBOLETH_DEV_ID` diverso crea un nuovo docente.

La modalità finta funziona solo con `DJANGO_DEBUG=True`, quindi mai in produzione.

## Collegare Google Calendar

Serve un client OAuth di Google. Ci sono due strade.

**Usare quello del progetto.** Chiedi a Lorenzo `GOOGLE_CLIENT_ID` e
`GOOGLE_CLIENT_SECRET`. Lui deve aggiungere il tuo account Google tra gli utenti di
test e la tua redirect URI, se usi una porta diversa da 8101.

**Crearne uno tuo** su [Google Cloud Console](https://console.cloud.google.com/):

1. abilita Google Calendar API;
2. configura la schermata di consenso, aggiungi lo scope `calendar.readonly` e il
   tuo account tra gli utenti di test;
3. crea credenziali OAuth di tipo "Applicazione web", con redirect URI
   `http://localhost:8101/accounts/google/login/callback/`.

In entrambi i casi metti i due valori in `.env`, poi:

```bash
docker compose up -d
docker compose exec tsc-dev python manage.py setup_site localhost:8101
```

La porta deve essere la stessa di `DEV_PORT` e della redirect URI. Finché l'app
Google è in stato "Testing" il collegamento scade dopo 7 giorni: basta rifarlo.

## Classificazione con AI

Facoltativa. Per il bottone "Classifica con AI" metti `OPENAI_API_KEY` in `.env`.
I titoli e le descrizioni degli eventi vengono inviati a OpenAI.

## Compilazione su Esse3

> **Attenzione:** "Compila" scrive sul **vero Esse3**, con il tuo account. Il bot
> non è ancora mai stato provato sul sito reale: login e salvataggio funzionano solo
> sulla carta. Fai la prima prova con pochi eventi e controlla il diario a mano.

Username: `SHIBBOLETH_DEV_ID`. Password e seed OTP si inseriscono nel form a ogni
compilazione e non vengono salvati.

Per lavorare su una pagina salvata invece che su Esse3, in
`classificationpipeline/esse3_compilation.py`:

1. in `open_diary` commenta `login` e `go_to_diary`, e togli il commento da
   `driver.get`;
2. in `open_period` commenta la chiamata a `submit`;
3. in `save_page` sostituisci la chiamata a `submit` con `pass`: è l'unica riga
   della funzione, commentarla e basta dà `IndentationError`.

La pagina si apre come file locale, quindi non funziona con il browser nel container
Selenium: serve l'app fuori da Docker, con Firefox installato. Copia `.env.example`
in `.env.dev`, con gli stessi valori di `.env` e in più:

```
DJANGO_DEBUG=True
SHIBBOLETH_DEV_MODE=True
ESSE3_TEST_PAGE=classificationpipeline/test_pages/esse3_diary.html
SELENIUM_HEADLESS=False
SELENIUM_END_PAUSE=20
```

Poi, dalla cartella con `manage.py`

```bash
./create_postgres_container.sh     # usa la stessa DB_PASSWORD che scrivi in .env.dev
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

La pagina salvata copre il periodo dal 01/10/2026 al 14/10/2026: servono eventi
classificati in quelle date, compilati con quell'intervallo. Password e seed OTP
vengono ignorati. Le ore finiscono solo nel browser, il file non cambia.

## Test

```bash
docker compose exec tsc-dev python manage.py test
```

I test del bot che usano il browser vengono saltati se nel container non c'è
Firefox. Nessun test tocca Esse3.
