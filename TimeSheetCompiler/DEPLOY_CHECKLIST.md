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

## Dati/file che non devono finire in produzione così come sono

| Cosa | Dove | Problema | Da fare |
|---|---|---|---|
| `db.sqlite3` | root del progetto, **committato in git** | contiene dati di sviluppo/test, inclusi token OAuth reali salvati (`SOCIALACCOUNT_STORE_TOKENS=True`) | rimuovere dal tracking git, `.gitignore`; in prod usare DB dedicato (vedi nota scalabilità sotto), mai lo sqlite di dev |
| `__pycache__/*.pyc` | tutte le app | committati per assenza `.gitignore` iniziale | rimuovere dal tracking, `.gitignore` presente ora — verificare copertura completa |
| SQLite come DB | `mysite/settings.py` `DATABASES` | non regge concorrenza in scrittura a scala (1000-3000 docenti, vedi CLAUDE.md §2.2) | migrare a Postgres prima di produzione, creare indice parziale su `Event` (query già annotata in CLAUDE.md) |

## Comportamenti dev-only da disattivare/verificare

- **Bypass Shibboleth** (`authentication/middleware.py`): se il container Shib non è ancora deployato in prod all'attivazione del sito, l'app resta comunque **non bloccante** senza header (nessun login forzato) — corretto per design, ma va verificato che in prod il container sia effettivamente davanti a tutte le rotte previste, altrimenti nessun utente riesce mai ad autenticarsi.
- **`requests.*` senza `timeout`** (`calendars/views.py`) — richieste verso Google API senza timeout, rischio DoS applicativo (worker bloccati). Aggiungere `timeout=(5, 10)` prima del deploy.
- **`except:` nudo** (`calendars/views.py`, `hooks/views.py`) — nasconde errori reali dietro redirect silenziosi, va sostituito con eccezioni specifiche prima che diventi difficile diagnosticare problemi in prod.

## Da configurare ex-novo per il deploy (non presente affatto oggi)

- Autenticazione Shibboleth reale (oggi solo scaffolding dev-mode, container non integrato)
- HTTPS/TLS davanti all'app (nessuna configurazione presente)
- CI/CD, health check, logging centralizzato (oggi solo `print()` sparsi, nessun `logging`)
- Gestione segreti (env file / secret manager) — oggi nessun meccanismo, tutto a mano

---

*Fonte incrociata: sezione "Aree Critiche" di `CLAUDE.md` per il dettaglio di ogni punto di sicurezza.*
