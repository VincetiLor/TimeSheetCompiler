# Checklist pre-produzione

Cose lasciate volutamente "semplici" durante lo sviluppo (hardcoded, permissive,
o assenti) che **devono** essere sistemate prima di qualunque deploy reale.
Non è un elenco di bug — è l'elenco di scorciatoie prese apposta per andare
veloci in dev, con la nota di cosa va disfatto.

Aggiornare questo file ogni volta che si introduce una nuova scorciatoia dev-only.

---

## Variabili che oggi sono hardcoded e devono diventare env var

| Cosa | Dove | Valore attuale | Da fare prima del deploy |
|---|---|---|---|
| `SECRET_KEY` | `mysite/settings.py:23` | stringa in chiaro nel repo | spostare in env var, **ruotare la chiave** (quella attuale è compromessa perché committata) |
| `DEBUG` | `mysite/settings.py` | `True` fisso | leggere da env, default `False` in assenza della var |
| `ALLOWED_HOSTS` | `mysite/settings.py` | `[]` (vuota) | valorizzare con hostname reali di produzione, letti da env |
| `SHIBBOLETH_DEV_MODE` | `mysite/settings.py` | letta da env, ma gated a `DEBUG` | verificare che la var non sia settata `True` sull'ambiente di prod — se `DEBUG=False` è comunque no-op per costruzione, ma va tolta dall'env di prod per pulizia |
| `SHIBBOLETH_DEV_FAKE_HEADERS` (`SHIBBOLETH_DEV_NOME`/`_COGNOME`/`_ID`) | `mysite/settings.py` | default fittizi (`Mario Rossi`, id `1`) | irrilevante in prod (mai letta se `SHIBBOLETH_DEV_MODE` è off), nessuna azione se il punto sopra è verificato |
| `SHIBBOLETH_HEADER_MAP` | `mysite/settings.py` | nomi header placeholder (`HTTP_SHIB_GIVENNAME`/`_SN`/`_UNIQUE_ID`) | aggiornare con i nomi reali forniti dal container Shibboleth una volta noti |
| Credenziali Google OAuth (`SocialApp` client_id/secret) | Django admin (DB), non in `settings.py` | inserite a mano in admin locale | verificare che l'ambiente di prod abbia le proprie credenziali OAuth (redirect URI diverso da dev) |
| Credenziali PostgreSQL (`DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT`) | `mysite/settings.py` `DATABASES`, lette da env via `python-dotenv` | in dev stanno in `.env.dev` (non committato, `chmod 600`); i default nel codice sono `timesheet`/`127.0.0.1:5432` e password vuota | in prod iniettare le variabili dall'orchestratore o da un secret manager, **non** deployare un file `.env`. Verificare che `DB_PASSWORD` sia effettivamente valorizzata: il default vuoto fa fallire la connessione in modo poco esplicito |

## Dati/file che non devono finire in produzione così come sono

| Cosa | Dove | Problema | Da fare |
|---|---|---|---|
| `db.sqlite3` | root del progetto, **ancora tracciato da git** | contiene dati di sviluppo/test, inclusi token OAuth reali salvati (`SOCIALACCOUNT_STORE_TOKENS=True`) | non e' piu' il DB attivo (migrato a Postgres) ma il file resta nel repo e nella history: fare `git rm --cached`, ed valutare `git filter-repo`/BFG per ripulire la history. `.gitignore` ora lo copre, insieme a `db.sqlite3.bak` |
| `__pycache__/*.pyc` | tutte le app | committati per assenza `.gitignore` iniziale | rimuovere dal tracking, `.gitignore` presente ora — verificare copertura completa |
| ~~SQLite come DB~~ | `mysite/settings.py` `DATABASES` | ~~non regge concorrenza in scrittura a scala~~ | **FATTO** — migrato a PostgreSQL 17 (vedi `DECISIONS.md`). Resta da fare l'indice parziale su `Event`: **non applicabile oggi**, il modello non ha ancora il campo di stato (`incerto`/`classificato`/`passato`/`compilato`) su cui costruire la condizione dell'indice |

## Comportamenti dev-only da disattivare/verificare

- **Bypass Shibboleth** (`authentication/middleware.py`): se il container Shib non è ancora deployato in prod all'attivazione del sito, l'app resta comunque **non bloccante** senza header (nessun login forzato) — corretto per design, ma va verificato che in prod il container sia effettivamente davanti a tutte le rotte previste, altrimenti nessun utente riesce mai ad autenticarsi.
- **`requests.*` senza `timeout`** (`classificationpipeline/views.py`, `SelectCalendarsView`) — richieste verso Google API senza timeout, rischio DoS applicativo (worker bloccati). Aggiungere `timeout=(5, 10)` prima del deploy.
- **`except:` nudo** (`classificationpipeline/views.py`, `hooks/views.py`) — nasconde errori reali dietro redirect silenziosi, va sostituito con eccezioni specifiche prima che diventi difficile diagnosticare problemi in prod.

## PostgreSQL — punti aperti dopo la migrazione

- **Il ruolo applicativo e' superuser.** Il container di dev crea `timesheet` come
  superuser del cluster (comportamento di default dell'immagine `postgres`). In
  produzione l'applicazione deve connettersi con un ruolo che possieda solo il
  database dell'app, senza privilegi di superuser.
- **Versione major da allineare.** In dev gira `postgres:17`. Postgres non
  consente il downgrade della datadir e un cambio di major version richiede
  `pg_upgrade` o dump+restore: fissare la versione di produzione **prima** di
  accumulare dati reali, e allineare quella di sviluppo.
- **Password nella history della shell e in `docker inspect`.** In dev la
  password del container e' visibile via `docker inspect`. In produzione usare
  `POSTGRES_PASSWORD_FILE` o i secret dell'orchestratore.
- **Backup.** Non esiste piu' il "copio il file": serve un `pg_dump` schedulato e
  una procedura di restore provata. Oggi non c'e' nulla.
- **`docker-compose.yml` assente.** Il container Postgres oggi nasce da un
  `docker run` (`docker_step3.sh`). Quando arrivera' il container Shibboleth
  serviranno due servizi coordinati: convertire in compose, versionato nel repo.
- **`fix_site.py` e' uno script una-tantum.** Ripara il duplicato di `Site`
  introdotto da `loaddata --natural-primary`. Va rieseguito su ogni ambiente in
  cui si ripete la procedura di migrazione dei dati, oppure il sito va
  configurato a mano.
- **Script di migrazione da rimuovere.** `dump_step2.sh`, `docker_step3.sh` e
  `fix_site.py` sono utility di sviluppo: decidere se versionarli come
  documentazione della procedura o eliminarli a migrazione consolidata.
- **`dump.json` va cancellato.** Contiene i token OAuth in chiaro. Ora e'
  in `.gitignore`, ma il file va rimosso dal disco a migrazione conclusa.

## Da configurare ex-novo per il deploy (non presente affatto oggi)

- **Static files**: `STATIC_ROOT` non è definito, quindi `collectstatic` oggi
  fallisce. Va definito e i file vanno serviti da nginx o WhiteNoise. Il CSS
  Tailwind (`static/css/app.css`) è già compilato e committato: sul server non
  serve Tailwind. Verificare solo che sia stato ricompilato con `--minify` dopo
  l'ultima modifica ai template.
- Autenticazione Shibboleth reale (oggi solo scaffolding dev-mode, container non integrato)
- HTTPS/TLS davanti all'app (nessuna configurazione presente)
- CI/CD, health check, logging centralizzato (oggi solo `print()` sparsi, nessun `logging`)
- Gestione segreti (env file / secret manager) — oggi nessun meccanismo, tutto a mano
- ~~**Scheduler per `mark_passed_events`**~~ — **risolto** (2026-08-29): cron
  utente con schedule `0 3 8-12 * *`, installato da `install_cron_step4.sh`, che
  invoca il wrapper `cron_mark_passed_events.sh` (fissa `.venv/bin/python` e i
  percorsi, redirige su `logs/mark_passed_events.log`). Cinque run dopo il
  confine di scadenza del giorno 8 danno ridondanza contro il downtime senza le
  20+ run mensili inutili di una cadenza giornaliera — vedi `DECISIONS.md`.
  **Da rifare sul server di produzione**: la crontab e' per utente e per
  macchina, non vive nel repo. Sul server verificare (a) che l'utente che lancia
  lo script abbia accesso al database e al file `.env` di produzione, (b) che il
  percorso del virtualenv coincida con quello di deploy, (c) che il fuso del
  sistema operativo non sposti il giorno rispetto a `TIME_ZONE` di Django, e (d)
  che i log abbiano una rotazione (`logrotate`) o crescano indefinitamente.
  Prima di attivarlo lanciarlo una volta con `--dry-run` e guardare il numero.
- ~~**`TIME_ZONE = 'UTC'` sfasa il cutoff di `mark_passed_events`**~~ —
  **risolto** (2026-08-26): `TIME_ZONE = 'Europe/Rome'` in `mysite/settings.py`.
  Resta da verificare al deploy che il **fuso del sistema operativo** del server
  non contraddica quello di Django: `USE_TZ = True` fa salvare tutto in UTC nel
  database, quindi Django e' coerente da solo, ma l'orario con cui cron decide
  quando lanciare `mark_passed_events` viene dal sistema, non da Django.


## Test

- **La suite gira contro Postgres reale.** `manage.py test` crea e distrugge
  `test_timesheet`: il ruolo DB usato dall'applicazione deve avere il permesso
  `CREATEDB` (in dev il ruolo `timesheet` ce l'ha gia'). In CI, quando ci sara',
  serve un servizio Postgres, non SQLite: la suite non e' stata verificata su
  altri backend.
- **Test che documentano bug aperti.** I test con suffisso `_known_bug` /
  `_known_gap` asseriscono il comportamento sbagliato attuale. Quando il bug
  corrispondente viene corretto **il test fallisce di proposito**: va
  aggiornato, non cancellato. Elenco: `Hook_settings` senza `Docente`,
  nessuna validazione di schema su `Hook.config`,
  nessun enforcement delle transizioni di `Event.status` (una stringa
  arbitraria e un passo all'indietro `compilato -> incerto` passano entrambi).
  Il vincolo di unicita' su `(calendar, event_id)` era in questo elenco: e'
  stato aggiunto il 2026-08-26 e il test relativo e' gia' stato riscritto.
- **`SHIBBOLETH_DEV_MODE` e' valutato all'import di `settings.py`**
  (`DEBUG and os.environ.get(...)`): impostare la variabile d'ambiente a runtime
  non ha effetto, ne' nei test ne' in produzione. Di conseguenza il "gate su
  DEBUG" e' piu' debole di quanto sembri — dipende solo dal valore di `DEBUG` al
  momento dell'avvio del processo. Verificare a mano, prima del deploy, che in
  produzione risulti `False`.
- **In dev-mode gli header finti vincono su quelli reali parziali.**
  `_read_headers` fonde come `{**meta, **FAKE}`: se anche un solo header Shib
  manca, l'intera identita' diventa quella finta. Con un SP configurato a meta'
  tutti gli utenti entrerebbero come lo stesso docente. Comportamento pinnato da
  un test; da rivedere quando il container SP viene integrato davvero.
- **CI assente.** La suite oggi si lancia solo a mano
  (`../.venv/bin/python manage.py test`). Da agganciare a una GitHub Action
  insieme al linter quando la CI verra' introdotta.

---

*Fonte incrociata: sezione "Aree Critiche" di `CLAUDE.md` per il dettaglio di ogni punto di sicurezza.*
