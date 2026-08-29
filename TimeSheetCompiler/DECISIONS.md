# Decisioni tecniche e appunti implementativi

Traccia delle scelte architetturali principali e dei pezzi di codice più
salienti, col relativo *perché* rispetto alle alternative scartate. Pensato
come base per l'elaborato di presentazione del software — non un changelog,
ma il ragionamento dietro ai cambiamenti che contano.

Formato per voce:
- **Cosa**: cosa è stato implementato/deciso
- **Alternative considerate**: cosa si sarebbe potuto fare invece
- **Perché questa scelta**: motivazione e tradeoff accettati
- **Codice**: estratto rilevante (non il file intero)
- **File**: dove vive il codice

---

## Autenticazione pre-Shibboleth via middleware header-based

**Cosa**: `authentication/middleware.py` (`ShibbolethAuthMiddleware`) legge attributi
(nome, cognome, id) iniettati come header HTTP dal container Shibboleth (SP) posto
davanti all'app, e su questi fa provisioning automatico di `User`+`Docente` al primo
hit, poi stabilisce una sessione Django normale (`django_login`).

**Alternative considerate**:
1. Autenticazione custom backend Django (`AUTHENTICATION_BACKENDS`) che intercetta a
   livello di backend invece che di middleware.
2. Creazione manuale di `Docente` in Django admin per ogni nuovo utente (comportamento
   attuale, pre-Shibboleth) — nessuna automazione.
3. Signal `post_save` su `User` per creare `Docente` vuoto, da compilare dopo.

**Perché questa scelta**: Shibboleth (mod_shib/Apache SP) non passa dati via
meccanismi Django-nativi (niente request.user pre-popolato, niente form di login) —
li passa come variabili d'ambiente WSGI/header HTTP sulla richiesta già "autenticata"
a monte. Un middleware è il punto naturale per intercettare *ogni* richiesta prima
che arrivi alla view, leggere quegli header una sola volta, e collassare
autenticazione container + provisioning DB + sessione Django in un solo passaggio,
senza toccare le view esistenti. Il backend custom (opzione 1) avrebbe comunque
richiesto un middleware a monte per esporre gli header in un punto leggibile dal
backend — giro più lungo per lo stesso risultato. Il signal (opzione 3) risolve solo
la creazione dell'oggetto, non popola i dati reali (nome/cognome/id) né gestisce la
sessione: sarebbe rimasto comunque il bug per cui un nuovo utente crasha su
`hooks/views.py:Hook_settings` finché non completa il profilo a mano.

**Tradeoff accettato**: il middleware si fida ciecamente degli header se presenti —
questo è sicuro *solo* se il container Shibboleth garantisce che nessun header
`Shib-*`/`REMOTE_USER` possa arrivare da un client non autenticato (compito del
container, non dell'app: deve stripparli/sovrascriverli sempre lato proxy). L'app
Django non ha modo di verificarlo autonomamente — è un'assunzione di fiducia sul
livello infrastrutturale sottostante.

**Codice**:
```python
# authentication/middleware.py
def __call__(self, request):
    if request.user.is_authenticated:
        return self.get_response(request)

    headers = self._read_headers(request)
    if headers:
        self._provision_and_login(request, headers)

    return self.get_response(request)
```

**File**: `authentication/middleware.py`, `mysite/settings.py` (`MIDDLEWARE`,
`SHIBBOLETH_HEADER_MAP`)

---

## Bypass locale del container Shibboleth (dev-mode)

**Cosa**: quando `SHIBBOLETH_DEV_MODE=True` (env var, letta solo se `DEBUG=True`) e
gli header reali non sono presenti, il middleware inietta valori finti
(`SHIBBOLETH_DEV_FAKE_HEADERS`) prima di procedere con lo stesso codice di
provisioning usato in produzione.

**Alternative considerate**:
1. Fixture DB (management command `loaddata`) che pre-crea un `Docente` di test.
2. Login view separata solo per sviluppo, che bypassa del tutto il middleware.
3. Mock a livello di test (`unittest.mock`) solo per i test automatici, nessun
   bypass utilizzabile durante `runserver` interattivo.

**Perché questa scelta**: l'obiettivo era poter sviluppare/testare l'app con
`runserver` reale, navigando nel browser, senza dover avere il container
Shibboleth in esecuzione sulla macchina di sviluppo. Iniettare header finti
*nello stesso punto* in cui arriverebbero quelli reali significa che il resto del
flusso (provisioning, sessione, redirect) è testato con lo stesso identico codice
che girerà in produzione — nessun ramo di codice "solo per i test" da tenere
sincronizzato a mano con quello reale. Le fixture DB (opzione 1) avrebbero
bypassato il middleware stesso, quindi non lo avrebbero testato. La view separata
(opzione 2) avrebbe introdotto un secondo path di login da mantenere.

**Tradeoff accettato**: il flag è un rischio di sicurezza se mai finisse acceso in
produzione — mitigato tenendolo hard-gated a `DEBUG` (`SHIBBOLETH_DEV_MODE = DEBUG
and os.environ.get(...)`), quindi anche se la env var restasse settata per errore
sul server di prod, la condizione `DEBUG=False` la disattiva comunque. Vedi
`DEPLOY_CHECKLIST.md` per la verifica da fare comunque prima del deploy.

**Codice**:
```python
# mysite/settings.py
SHIBBOLETH_DEV_MODE = DEBUG and os.environ.get('SHIBBOLETH_DEV_MODE') == 'True'
SHIBBOLETH_DEV_FAKE_HEADERS = {
    'HTTP_SHIB_GIVENNAME': os.environ.get('SHIBBOLETH_DEV_NOME', 'Mario'),
    'HTTP_SHIB_SN': os.environ.get('SHIBBOLETH_DEV_COGNOME', 'Rossi'),
    'HTTP_SHIB_UNIQUE_ID': os.environ.get('SHIBBOLETH_DEV_ID', '1'),
}
```

**File**: `mysite/settings.py` (`SHIBBOLETH_DEV_MODE`, `SHIBBOLETH_DEV_FAKE_HEADERS`)

---

## Docente come estensione OneToOne di `User` (non custom User model)

**Cosa**: `authentication/models.py` — `Docente` è un modello separato con
`OneToOneField` verso `django.contrib.auth.models.User`, invece di sostituire
`User` con un modello custom (`AUTH_USER_MODEL`).

**Alternative considerate**: custom User model (`AbstractUser` esteso) con i campi
`id_unimore`/`nome`/`cognome`/`max_calendars` direttamente su di esso.

**Perché questa scelta (dedotta)**: `User` resta quello standard di Django/allauth,
usato as-is da `django-allauth` per il login social Google — nessuna necessità di
configurare `AUTH_USER_MODEL` prima della prima migration (cambio che, se fatto
dopo, richiede di ricreare tutto lo schema auth). Il rovescio: ogni accesso ai dati
del docente passa da `request.user.docente`, che solleva
`RelatedObjectDoesNotExist` se l'oggetto non esiste ancora — la causa diretta del
bug #9 in `CLAUDE.md`, ora mitigata (non ancora eliminata alla radice per tutte le
view) dal provisioning automatico di `ShibbolethAuthMiddleware`.

**Codice**:
```python
# authentication/models.py
class Docente(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='docente',
    )
    id_unimore = models.IntegerField(unique=True)
    max_calendars = models.IntegerField(default=3)
    nome = models.CharField(max_length=100, blank=True)
    cognome = models.CharField(max_length=100, blank=True)
```

**File**: `authentication/models.py`

---

## Refresh token Google gestito a mano invece che affidato ad allauth

**Cosa**: `authentication/services.py` (`get_token_valido`) verifica manualmente se
il token Google è scaduto (chiamata di test a `calendarList`, controllo `401`) e lo
rinnova a mano via richiesta diretta a `oauth2.googleapis.com/token`, invece di
usare un eventuale meccanismo di refresh automatico di `django-allauth`.

**Perché questa scelta (dedotta)**: `django-allauth` salva i token
(`SOCIALACCOUNT_STORE_TOKENS=True`) ma non li rinnova automaticamente da solo prima
di ogni uso — serve comunque codice applicativo che decida *quando* rinnovare.
L'approccio "prova la chiamata, se 401 rinnova" evita di dover controllare a priori
la scadenza (che richiederebbe fidarsi del campo `expires_at` salvato, non sempre
affidabile), al costo di una chiamata extra a Google ad ogni utilizzo del token.

**Tradeoff**: la funzione era duplicata identica in `calendars/views.py` e
`hooks/views.py` (vedi `CLAUDE.md` punto 14) — spostata poi in
`authentication/services.py` come unica fonte di verità, rimuovendo l'accoppiamento
incrociato `hooks → calendars` per una funzione che è di autenticazione, non di
dominio calendari. `calendars/views.py:SelectCalendarsView.dispatch` la richiama
dentro un `except:` nudo generico — cattura anche errori non legati al token
(es. utente anonimo), punto ancora segnato come debito in `CLAUDE.md` punto 10-11,
non risolto da questo spostamento.

**Codice**:
```python
# authentication/services.py
def get_token_valido(user):
    token = SocialToken.objects.get(account__user=user, account__provider='google')
    test = requests.get(
        'https://www.googleapis.com/calendar/v3/users/me/calendarList',
        headers={'Authorization': f'Bearer {token.token}'},
        params={'maxResults': 1}
    )
    if test.status_code == 401:
        app = SocialApp.objects.get(provider='google')
        post = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': app.client_id,
                'client_secret': app.secret,
                'refresh_token': token.token_secret,
                'grant_type': 'refresh_token',
            }
        )
        if post.status_code == 200:
            token.token = post.json().get('access_token')
            token.save()
        else:
            raise Exception("Impossibile rinnovare il token")
    return token.token
```

**File**: `authentication/services.py`, richiamata da `calendars/views.py`,
`hooks/views.py`

---

## Appunto: `classificationpipeline` come cache locale eventi, ancora stub

Il modello dati (`Calendar`/`Event` in `classificationpipeline/models.py`) è
pensato come cache locale degli eventi Google Calendar per poterli classificare
senza richiamare l'API ad ogni step (coerente con `CLAUDE.md` §2.2: "una cache
salva temporaneamente lo stato di classificazione"). Ad oggi però `event_compare`
(`classificationpipeline/views.py`) riceve solo gli ID dei calendari selezionati
via POST e li passa al template senza mai scrivere una riga in `Event`/`Calendar`
(nessun `.objects.create` in nessuna view) — il template stesso
(`event_compare.html`) non li visualizza nemmeno. È uno scaffolding di modello dati
in attesa della pipeline vera (fetch eventi da Google → popolamento `Event` → stati
`incerto`/`classificato`/`compilato`/`passato` da `CLAUDE.md` §2.3, non ancora sul
modello). Nessuna decisione da registrare qui finché la pipeline non viene scritta
davvero — solo il punto di partenza per quando succederà.

Nota naming: il campo `Calendar.event` (`JSONField`) e il modello correlato
`Event` con FK verso `Calendar` sembrano ridondanti/residuo di refactoring (già
segnalato in `CLAUDE.md` punto 16) — da chiarire quando si scrive la pipeline reale,
non temo da toccare ora senza indicazione esplicita.

**Codice** (lo stub attuale, per intero — è tutta la view):
```python
# classificationpipeline/views.py
def event_compare(request):
    if request.method == 'POST':
        calendar_ids = request.POST.getlist('calendars')
        print(calendar_ids)
        return render(request, 'classificationpipeline/event_compare.html', {'calendar_ids': calendar_ids})
    else:
        return redirect('calendars:eventi_calendars')
```

**File**: `classificationpipeline/models.py`, `classificationpipeline/views.py`

---

## `Hook.config` come JSONField libero invece di regole relazionali

**Cosa**: `hooks/models.py` — `Hook.config` è un singolo `JSONField(default=dict)`
per docente (`OneToOneField`), contenente presumibilmente le regole
keyword→classificazione custom (§1 di `CLAUDE.md`: "regole keyword-based di
classificazione custom"), invece di un modello relazionale (es. tabella
`HookRule` con FK a `Docente`, un campo `keyword`, un campo `classificazione`).

**Perché questa scelta (dedotta)**: le regole sono per-docente, libere nella forma,
e non richiedono query/filtri lato DB sul singolo criterio (la classificazione
degli eventi presumibilmente carica l'intero `config` e lo valuta in Python, non
via query SQL) — un JSON evita migration ad ogni cambio di struttura delle regole,
a costo di perdere validazione/integrità a livello DB (nessun vincolo su forma o
duplicati delle keyword, tutto delegato al form/al codice che consuma `config`).

**Codice**:
```python
# hooks/models.py
class Hook(models.Model):
    id = models.AutoField(primary_key=True)
    docente = models.OneToOneField('authentication.Docente', on_delete=models.CASCADE)
    config = models.JSONField(default=dict)
```

**File**: `hooks/models.py`, esposto via `Hook_settings`
(`hooks/views.py`, `UpdateView` con `LoginRequiredMixin` già presente)

---

## Migrazione da SQLite a PostgreSQL

**Cosa**: il database di sviluppo e' passato da SQLite (file `db.sqlite3` nella
root del progetto) a PostgreSQL 17 in container Docker, con le credenziali lette
da variabili d'ambiente invece che hardcoded.

**Perche'**: SQLite serializza le scritture con un lock a livello di file. Con i
1000-3000 docenti previsti a regime (§2.2 di `CLAUDE.md`) la classificazione
degli eventi genera scritture concorrenti che SQLite non regge. Postgres
introduce inoltre due capacita' che servono a questo progetto in particolare:
gli indici parziali (per isolare il sottoinsieme di `Event` in stato attivo dallo
storico) e il tipo `jsonb` nativo, che rende interrogabili i campi
`Hook.config` e `Calendar.event` con lookup come `__has_key` e `__contains` —
non supportati dal backend SQLite.

**Come**: `dumpdata` su SQLite, `migrate` su Postgres vuoto, `loaddata`. Il
percorso passa interamente dall'ORM, quindi lo schema risultante e' quello
descritto dalle migrazioni Django.

**Alternative scartate**:

1. *Restare su SQLite con WAL mode.* Il write-ahead logging migliora la
   concorrenza in lettura ma le scritture restano serializzate: sposta il
   problema, non lo risolve, e va comunque rifatto al primo picco di carico.
2. *pgloader / conversione diretta del file SQLite.* Copia le tabelle bypassando
   Django e mappa i tipi con regole proprie: si ottiene un database che non
   corrisponde allo stato di `django_migrations`, con divergenze che emergono
   alla prima `makemigrations`. Scartata perche' il costo della coerenza si paga
   comunque, solo piu' tardi e peggio.
3. *MySQL/MariaDB.* Nessun vantaggio per questo caso d'uso e supporto `jsonb`
   inferiore; Postgres e' anche il default di fatto nell'ecosistema Django.

**Configurazione risultante**:
```python
# mysite/settings.py
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / os.environ.get('DJANGO_ENV_FILE', '.env.dev'))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'timesheet'),
        'USER': os.environ.get('DB_USER', 'timesheet'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}
```

`load_dotenv` non sovrascrive le variabili gia' presenti in ambiente: in
produzione il file `.env.dev` non esiste e comandano le variabili iniettate dal
container, quindi lo stesso `settings.py` vale in entrambi gli ambienti. La
scelta di `python-dotenv` al posto del `source` manuale di un env file e' pura
ergonomia di sviluppo — evita che una shell senza variabili esportate produca un
errore di autenticazione fuorviante.

**Driver**: `psycopg[binary]` (psycopg 3), non psycopg2. E' il driver
raccomandato da Django 6; l'extra `binary` include libpq precompilata, evitando
la dipendenza di sistema `libpq-dev` sulle macchine di sviluppo.

**File**: `mysite/settings.py`, `requirements.txt`, `.env.example`, `.gitignore`

---

## Trappole incontrate nella migrazione dei dati (e come sono state risolte)

Tre problemi concreti emersi eseguendo `dumpdata`/`loaddata`, annotati perche'
si ripresenteranno identici a chi rifara' la procedura su un altro ambiente.

**1. Content type e permessi vanno esclusi dal dump.** `migrate` ricrea da se' le
righe di `django_content_type` e `auth_permission` sul database nuovo; caricare
anche quelle serializzate viola il vincolo di unicita' e fa fallire `loaddata`.
I flag di chiave naturale servono invece a far risolvere i legami
utente-gruppo-permesso per nome anziche' per PK intera, che sul database nuovo
e' diversa:

```bash
python manage.py dumpdata \
  --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission \
  --exclude admin.logentry --exclude sessions.session \
  --indent 2 -o dump.json
```

**2. `--natural-primary` fa perdere l'identita' di `Site`, e questo rompe
allauth.** Poiche' le PK non vengono serializzate, il `Site` del progetto e'
rientrato come `pk=2`, mentre `example.com` creato da `migrate` ha occupato
`pk=1`. Con `SITE_ID = 1` allauth cercava la `SocialApp` sul sito sbagliato e il
login Google diventava irrisolvibile, pur essendo le credenziali correttamente
migrate. Risolto consolidando il sito reale su `pk=1` e rilegandoci la
`SocialApp` (`fix_site.py`). Da rifare a ogni nuovo ambiente, oppure evitare
alla radice escludendo `sites` dal dump e riconfigurando il sito a mano.

**3. Le sessioni non sopravvivono (per scelta).** `sessions.session` e' escluso
dal dump, quindi dopo la migrazione tutti gli utenti risultano disconnessi. E'
il comportamento voluto — le sessioni sono dati effimeri legati a cookie — ma
produce un sintomo confondente: si rientra come utente diverso da prima e le
view che assumono un `Docente` collegato falliscono (vedi la voce successiva).

**File**: `dump_step2.sh`, `docker_step3.sh`, `fix_site.py`

---

## Conseguenze semantiche del cambio di backend sul codice futuro

Postgres non e' un SQLite piu' grande: alcuni comportamenti su cui il codice
poteva implicitamente contare cambiano. Nessuno di questi rompe il codice
attuale — l'app non fa quasi query locali (§18 di `CLAUDE.md`) — ma tutti
diventano rilevanti quando si scrivera' la pipeline di ingestione eventi.

- **`LIKE` diventa case-sensitive.** Su SQLite `__contains` ignora il case per
  l'ASCII, su Postgres no. Un filtro scritto e testato su SQLite restituisce
  silenziosamente meno righe. Usare `__icontains`/`__iexact` quando l'intento e'
  l'insensibilita' al case.
- **Nessun ordinamento implicito.** Nessun modello del progetto definisce
  `Meta.ordering`: i queryset non ordinati cambiano ordine di riga, e i NULL
  finiscono in coda in ordinamento ascendente anziche' in testa.
- **Una query fallita aborta l'intera transazione.** Dentro un blocco `atomic()`,
  Postgres rifiuta ogni query successiva a un errore finche' non si esegue il
  rollback. Il pattern `try: create() except IntegrityError: pass` — plausibile
  per la deduplicazione degli eventi richiesta al §154 di `CLAUDE.md` — non
  funziona: servono `get_or_create` o `bulk_create(ignore_conflicts=True)`, che
  delegano il conflitto al database invece di lasciarlo emergere come eccezione.
- **`JSONField` diventa `jsonb` reale.** L'API Python e' identica (si legge e si
  scrive un `dict`), ma lo storage e' parsato: chiavi duplicate collassate,
  ordine delle chiavi non conservato, JSON malformato rifiutato. In cambio si
  ottengono lookup e indici GIN sul contenuto.
- **Il database e' ora un servizio.** Deve essere in esecuzione perche'
  `manage.py` faccia qualsiasi cosa, e il backup non e' piu' la copia di un file
  ma `pg_dump`.

**File**: nessuno modificato — vincoli da rispettare nel codice futuro

---

## Bug latenti resi visibili dalla migrazione

Non causati dal cambio di database, ma emersi perche' la perdita delle sessioni
ha fatto rientrare con un utente diverso (il superuser dell'admin, privo di
`Docente` collegato).

`hooks/views.py` accede a `self.request.user.docente` senza difese: per
qualunque utente autenticato ma privo di `Docente` — un superuser creato con
`createsuperuser`, o un utente creato dall'admin — la view solleva
`RelatedObjectDoesNotExist` e restituisce un 500 invece di un messaggio
comprensibile.

```python
# hooks/views.py
def get_object(self, queryset=None):
    hook, created = Hook.objects.get_or_create(
        docente=self.request.user.docente)
    return hook
```

`calendars/views.py` ha lo stesso problema mascherato da un `except:` nudo, che
riconduce cause diverse (utente senza account Google, token scaduto, rete
irraggiungibile) allo stesso redirect silenzioso verso `avvia_login_google`,
rendendo indistinguibile un problema di configurazione da uno di connettivita'.

```python
# calendars/views.py
def dispatch(self, request, *args, **kwargs):
    try:
        self.token = get_token_valido(self.request.user)
    except:
         return redirect(f"{reverse('avvia_login_google')}?next={request.path}")
    return super().dispatch(request, *args, **kwargs)
```

Entrambi gia' segnalati in `DEPLOY_CHECKLIST.md`; qui si registra solo che la
migrazione ne ha reso concreto l'impatto.

**File**: `hooks/views.py`, `calendars/views.py`

---

## (In sospeso) React/Tailwind per il frontend

Discusso ma **non implementato**: Tailwind è già in uso (via CDN) e ritenuto
sufficiente per il solo look. React avrebbe senso solo per l'unica pagina con stato
client complesso — la selezione multi-calendario (`calendars/eventi_calendars.html`,
gestita oggi con JS vanilla) e la futura UI di revisione classificazione LLM
(§2.5 di `CLAUDE.md`, non ancora implementata). Approccio proposto se/quando si
deciderà di procedere: "isola" React montata su un solo template via bundle
separato (Vite), resto del sito resta Django template puro — nessuna riscrittura
totale a SPA. Nessun codice scritto per questo punto.

---

## Test automatici: test runner Django + `unittest.mock`, niente pytest

**Data**: 2026-08-26

**Decisione**: la suite di test vive nei `tests.py` di ciascuna app e gira con
`python manage.py test`. Le dipendenze di rete verso Google vengono sostituite
con `unittest.mock.patch`. Nessuna nuova dipendenza aggiunta a
`requirements.txt` (niente `pytest`, `pytest-django`, `responses`,
`factory_boy`).

**Alternative scartate**:

- **`pytest` + `pytest-django`**: sintassi delle fixture piu' ergonomica e
  output piu' leggibile, ma introduce due dipendenze e un file di
  configurazione per un progetto che non ha ancora ne' CI ne' linter. Il
  runner integrato copre gia' il 100% di quello che serve qui (client HTTP,
  transazioni per test, `override_settings`).
- **`responses` / `requests-mock`** per le chiamate HTTP: sostituibili da
  `mock.patch` sul modulo che effettua la chiamata, senza aggiungere librerie.
  Il punto chiave e' *dove* si applica la patch: sul simbolo come e' importato
  dal modulo sotto test, non dove e' definito.

```python
# authentication/tests.py — Google non viene mai contattato davvero
@mock.patch('authentication.services.requests.post')
@mock.patch('authentication.services.requests.get')
def test_expired_token_is_refreshed_and_persisted(self, mock_get, mock_post):
    mock_get.return_value = self._response(401)
    mock_post.return_value = self._response(200, {'access_token': 'access-new'})

    self.assertEqual(get_token_valido(self.user), 'access-new')

    self.token.refresh_from_db()
    self.assertEqual(self.token.token, 'access-new')
```

**Cosa si e' scelto di NON testare**: l'handshake OAuth di Google (codice di
`allauth` piu' un server esterno — testarlo significherebbe testare librerie di
terzi) e il container Shibboleth. Quest'ultimo e' irrilevante ai fini del test:
il suo unico compito e' iniettare header nella richiesta, e il test client di
Django li inietta direttamente. La suite del middleware gira quindi senza
container, con `SHIBBOLETH_DEV_MODE=False` forzato via `override_settings` per
non dipendere dall'ambiente.

```python
# authentication/tests.py — il container SP non serve, bastano gli header
def test_provisions_user_and_docente_on_first_hit(self):
    response = self.client.get('/auth/', headers=SHIB_HEADERS)

    docente = Docente.objects.get(id_unimore=4242)
    self.assertEqual(docente.user.username, 'shib-4242')
```

**Bug noti come test verdi, non come test rossi**: i difetti gia' censiti in
`CLAUDE.md` §4 (crash di `Hook_settings` senza `Docente`, `LoginRequiredMixin`
mancante su `SelectCalendarsView`, assenza di chiave di deduplicazione degli
eventi) sono coperti da test che asseriscono il comportamento *attuale*, con
nome e docstring che lo dichiarano esplicitamente (`..._known_bug`,
`..._known_gap`). Alternativa scartata: `@unittest.expectedFailure`, che
nasconde le regressioni nella direzione opposta. Cosi' invece la suite resta
verde, e quando qualcuno corregge il bug il test fallisce e va aggiornato:
il fallimento e' il promemoria.

### Test significativi, per esteso

Quattro test che valgono la pena di essere citati per l'elaborato, perche'
ciascuno documenta un comportamento non ovvio del software piu' che una
funzione banale.

**1. Il middleware Shibboleth si testa senza container.** E' il punto che
sembrava piu' ostico: il provisioning dipende da un SP esterno che in sviluppo
non gira. Ma il container ha un solo compito osservabile — mettere attributi
negli header della richiesta — e il test client di Django puo' fare esattamente
la stessa cosa. Il test passa dallo stack reale (`/auth/`, non `RequestFactory`)
perche' `django_login` ha bisogno di una sessione vera:

```python
# authentication/tests.py
SHIB_HEADERS = {
    'shib-givenname': 'Mario',
    'shib-sn': 'Rossi',
    'shib-unique-id': '4242',
}

def test_provisions_user_and_docente_on_first_hit(self):
    response = self.client.get('/auth/', headers=SHIB_HEADERS)

    self.assertEqual(response.status_code, 200)
    docente = Docente.objects.get(id_unimore=4242)
    self.assertEqual(docente.nome, 'Mario')
    self.assertEqual(docente.user.username, 'shib-4242')
    self.assertTrue(response.wsgi_request.user.is_authenticated)
```

**2. In dev-mode gli header finti battono quelli reali parziali.** Difetto
emerso proprio scrivendo i test. `_read_headers` fonde i due dizionari come
`{**meta, **FAKE}`: se anche un solo header Shib manca, i valori finti vincono
sull'intera identita', non solo sul campo mancante. Con un SP configurato a
meta' ogni utente entrerebbe come lo stesso docente fittizio. Il test asserisce
il comportamento attuale, in modo che l'ordine della fusione non possa cambiare
inosservato:

```python
# authentication/tests.py
def test_dev_headers_override_partially_present_real_headers(self):
    self.client.get('/auth/', headers={'shib-unique-id': '999'})

    self.assertFalse(Docente.objects.filter(id_unimore=999).exists())
    self.assertTrue(Docente.objects.filter(id_unimore=1).exists())
```

**3. Il JSON degli hook non ha alcuna validazione di schema.** `Hook_settings`
espone il `JSONField` grezzo tramite `fields = ['config']`: il form accetta
qualunque JSON sintatticamente valido, scalari compresi. `default=dict` sul
modello non e' un vincolo, e' solo un valore iniziale. Il test lo dimostra
salvando un intero come configurazione degli hook:

```python
# hooks/tests.py
def test_any_valid_json_scalar_is_accepted_known_gap(self):
    self.client.force_login(self.user)

    response = self.client.post('/hooks/', {'config': '42'})

    self.assertEqual(response.status_code, 302)
    self.assertEqual(Hook.objects.get(docente=self.docente).config, 42)
```

Non e' stato aggiunto un validatore: sarebbe una feature, non un test. Il test
serve a rendere esplicito che la validazione oggi non esiste, cosi' che venga
progettata insieme alla pipeline di classificazione (che di quel JSON e' l'unico
consumatore).

**4. La pipeline non persiste niente — e il test lo dice.** La POST che
dovrebbe avviare la classificazione non scrive una riga. Il test asserisce zero
righe: fallira' non appena l'ingestione verra' implementata, ed e' quello il
momento in cui va sostituito con asserzioni vere sulle righe create.

```python
# classificationpipeline/tests.py
def test_no_calendar_or_event_row_is_persisted_yet_known_gap(self):
    self.client.force_login(self.user)

    self.client.post('/events/', {'calendars': ['cal-a@google.com']})

    self.assertEqual(Calendar.objects.count(), 0)
    self.assertEqual(Event.objects.count(), 0)
```

### Isolamento dall'ambiente locale

`SHIBBOLETH_DEV_MODE` viene calcolato all'import di `settings.py`, quindi il suo
valore dipende dal `.env.dev` del singolo sviluppatore — file non versionato.
Senza precauzioni la suite passava o falliva a seconda della macchina: con il
flag attivo il middleware autenticava anche il client "anonimo", e i test che
verificano i redirect di accesso negato fallivano. Tutte le classi di test che
asseriscono comportamento anonimo forzano quindi il flag:

```python
# hooks/tests.py
@override_settings(SHIBBOLETH_DEV_MODE=False)
class HookSettingsTests(TestCase):
```

Regola generale che ne discende: un test che asserisce il comportamento di una
view rispetto all'autenticazione deve fissare esplicitamente lo stato del
middleware, altrimenti non sta testando la view.

**File**: `authentication/tests.py`, `hooks/tests.py`, `calendars/tests.py`,
`classificationpipeline/tests.py`

---

## Provisioning automatico di `Hook` via signal `post_save` su `Docente`

Fino a questo punto la riga `Hook` di un docente veniva creata pigramente, alla
prima visita di `/hooks/`, dal `get_or_create` dentro `Hook_settings.get_object`.
Funziona, ma sposta un fatto di dominio ("ogni docente ha esattamente una
configurazione di hook") dentro una view: qualunque altro consumatore futuro
della configurazione — la pipeline di classificazione, un management command, un
job schedulato — deve o ripetere il `get_or_create` o gestire il caso "hook
assente". Con la relazione dichiarata `OneToOneField`, "assente" è uno stato che
lo schema promette non esistere e che invece esiste davvero.

La creazione è stata quindi agganciata a un signal `post_save` sul modello
`Docente`, registrato in `AuthenticationConfig.ready()`:

```python
# authentication/signals.py
@receiver(post_save, sender=Docente)
def create_hook_for_docente(sender, instance, created, **kwargs):
    if created:
        Hook.objects.get_or_create(docente=instance)
```

```python
# authentication/apps.py
class AuthenticationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'authentication'

    def ready(self):
        from . import signals  # noqa: F401
```

L'import dentro `ready()` non è stilistico: importare `signals` a livello di
modulo in `apps.py` significherebbe importare i modelli prima che il registry
delle app sia popolato (`AppRegistryNotReady`).

`get_or_create` invece di `create` mantiene il receiver idempotente rispetto a
un provisioning bulk che creasse `Docente` e `Hook` nella stessa transazione.

Non basta però per `loaddata`. Se il fixture contiene sia `authentication.Docente`
sia `hooks.Hook` e i `Docente` vengono caricati per primi, il signal crea un
`Hook` con una PK nuova presa dalla sequenza, e subito dopo `loaddata` inserisce
il `Hook` dumpato con lo stesso `docente_id`: violazione del vincolo di unicità
della OneToOne. Il round-trip `dumpdata`/`loaddata` è un percorso reale in questo
progetto (è stato usato per la migrazione a Postgres), quindi il receiver ignora
esplicitamente i salvataggi in modalità `raw`, che è come Django segnala
"questo save viene da un fixture":

```python
def create_hook_for_docente(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    Hook.objects.get_or_create(docente=instance)
```

**Alternative scartate**

- *Lasciare il `get_or_create` nella view*. È lo stato precedente: il
  `get_or_create` in `Hook_settings` è stato **mantenuto** come rete di sicurezza
  per i `Docente` creati prima di questo cambiamento, ma non è più l'unico punto
  di creazione.
- *Override di `Docente.save()`*. Accoppia il modello `authentication` all'app
  `hooks` in modo diretto e rende `Docente` non testabile in isolamento; il
  signal lascia la dipendenza a senso unico e disattivabile.
- *`Hook` creato dal `ShibbolethAuthMiddleware`*, accanto al `Docente`. Copre
  solo il percorso Shibboleth, non `createsuperuser` + creazione manuale, non i
  test, non i fixture.

**Effetto sui test**: tre test in `hooks/tests.py` assumevano che nessun `Hook`
esistesse dopo la creazione di un `Docente` e lo creavano a mano. Sono stati
riscritti per aggiornare la riga già esistente. Il test
`test_anonymous_is_redirected_to_login` ora asserisce che la richiesta anonima
lasci la config *intatta*, non che l'hook non esista.

**File**: `authentication/signals.py`, `authentication/apps.py`, `hooks/tests.py`

---

## Vincoli di unicità su `Calendar` ed `Event` (chiave di deduplicazione)

`classificationpipeline/models.py` dichiarava i vincoli con una sintassi che non
è Python valido come vincolo — un assegnamento a un attributo dentro un letterale
di lista:

```python
# PRIMA - non fa quello che sembra
class Meta:
    constraints = [
        models.UniqueConstraint = (fields['docente', 'calendar_id'], name='...'),
    ]
```

Il risultato non era un errore silenzioso ma un `SyntaxError` all'import del
modulo: l'app non partiva affatto. La forma corretta è una chiamata al
costruttore, con `fields` come argomento keyword:

```python
# DOPO
class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=['docente', 'calendar_id'],
            name='unique_calendar_to_professor',
        ),
    ]
```

Il vincolo su `Event` è analogo, su `('calendar', 'event_id')`.

Al di là della sintassi, i due vincoli sono la prima metà concreta della
**deduplicazione in classificazione** descritta in `CLAUDE.md` §2.4: la chiave
naturale di un evento è la coppia (calendario locale, id evento Google), non
l'id Google da solo — lo stesso evento può comparire in due calendari diversi e
lì è legittimamente due righe. Analogamente, un docente non può selezionare due
volte lo stesso Google Calendar. Il vincolo vive nel database, quindi vale anche
per i percorsi che non passano dall'ORM applicativo (import, script, job).

Migrazione generata: `classificationpipeline/0003_calendar_unique_calendar_to_professor_and_more.py`.

**Effetto sui test**: `test_duplicate_event_id_is_currently_allowed_known_gap`
asseriva il comportamento sbagliato precedente (due righe duplicate consentite).
Come previsto dalla convenzione `_known_gap`, chiudere il gap lo fa fallire: è
stato sostituito da tre test che verificano il rifiuto del duplicato nello stesso
calendario, l'accettazione dello stesso `event_id` in calendari diversi, e il
rifiuto del calendario duplicato per lo stesso docente.

**File**: `classificationpipeline/models.py`, `classificationpipeline/tests.py`

---

## Ciclo di vita dell'evento: campo `status` e indice parziale

`Event` non aveva alcuna nozione di avanzamento: una riga in tabella significava
solo "questo evento esiste", non "questo evento è stato compilato". Senza quel
dato nessuna delle due deduplicazioni della specifica è esprimibile — quella in
compilazione ha bisogno di sapere *quali* eventi Selenium ha già inserito — e
nessun evento può essere congelato quando il suo periodo di validità scade.

Il campo è una `TextChoices` con i quattro stati di `CLAUDE.md` §2.3:

```python
class Event(models.Model):

    class Status(models.TextChoices):
        INCERTO = 'incerto', 'Incerto'
        CLASSIFICATO = 'classificato', 'Classificato'
        COMPILATO = 'compilato', 'Compilato'
        PASSATO = 'passato', 'Passato'

    ACTIVE_STATUSES = (Status.INCERTO, Status.CLASSIFICATO)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INCERTO,
    )
```

Tre scelte da motivare.

**`CharField` con `TextChoices`, non `IntegerField` con costanti numeriche.** Un
`status = 2` in un dump o in una query di debug non dice niente; `'compilato'`
sì. Il costo (qualche byte per riga, confronti su stringa) è irrilevante al
volume di questa applicazione, e i valori memorizzati sono già i letterali che
la specifica usa a testo — inclusa la condizione dell'indice parziale qui
sotto e il futuro job `mark_passed_events`. Cambiarli significherebbe cambiarli
in tre posti scollegati fra loro.

**`default=INCERTO`.** L'ingestione da Google Calendar non classifica: scarica e
basta. Lo stato iniziale corretto è quindi "non ancora classificato", ed è anche
l'unico default che non mente se qualcuno crea una riga da shell o da fixture.

**`ACTIVE_STATUSES` come costante di modello.** Il sottoinsieme "eventi ancora in
lavorazione" comparirà in ogni query della pipeline, nella UI degli eventi non
compilati e nel job di scadenza. Definirlo una volta accanto agli stati evita
che tre punti diversi elenchino a mano gli stessi due valori e divergano.

### Indice parziale

`CLAUDE.md` §2.2 prevedeva l'indice parziale ma lo dava per non realizzabile,
mancando il campo di stato. Ora è realizzabile, e con colonne diverse da quelle
ipotizzate lì:

```python
indexes = [
    models.Index(
        fields=['calendar', 'start_time'],
        condition=Q(status__in=['incerto', 'classificato']),
        name='idx_event_active',
    ),
]
```

La bozza originale scriveva `(docente_id, data_evento)`. Nessuna delle due
colonne esiste su `Event`: il docente si raggiunge attraverso `calendar`
(`Event -> Calendar -> Docente`), e il campo temporale si chiama `start_time`.
`calendar_id` è quindi la colonna di scoping corretta — ed è anche più selettiva
di `docente_id`, dato che un docente può avere più calendari.

Quello che l'indice compra è indipendenza dalla crescita dello storico: le righe
`compilato`/`passato` si accumulano per anni ma non entrano nell'indice, che
resta piccolo e in RAM. Verificato sul DB di sviluppo:

```sql
CREATE INDEX idx_event_active ON public.classificationpipeline_event
USING btree (calendar_id, start_time)
WHERE ((status)::text = ANY (ARRAY['incerto', 'classificato']::text[]))
```

Nessun `db_index=True` su `status`: quattro valori distinti su tutta la tabella
sono una selettività troppo bassa perché un indice normale serva a qualcosa, e
l'indice parziale copre già l'unico accesso che conta.

**Alternative scartate**

- *Tabella di transizioni / macchina a stati esplicita*. Utile se servisse un
  audit trail ("chi ha portato questo evento a compilato e quando"), che al
  momento non è nei requisiti. Aggiungerebbe una join a ogni query della
  pipeline per un dato che nessuna schermata mostra.
- *Booleani separati* (`is_compilato`, `is_passato`). Rappresentano stati
  mutuamente esclusivi come flag indipendenti, quindi ammettono combinazioni
  impossibili (`is_compilato and is_passato`) che poi vanno escluse a mano.
- *`status` su `Calendar` invece che su `Event`*. Lo stato è per-evento: dentro
  lo stesso calendario convivono eventi compilati e incerti.

**Gap noto e accettato**: `choices` è validato dai form, non dal database né da
`Model.save()`. Nulla impedisce quindi né una stringa arbitraria né una
transizione all'indietro (`compilato -> incerto`). È la stessa scelta già fatta
per `Hook.config` (JSONField senza schema): la validazione arriverà quando
arriverà il codice che effettua le transizioni, non prima. Il test
`test_an_invalid_status_is_not_rejected_by_the_database_known_gap` lo fissa.

**Nota per chi scriverà `mark_passed_events`**: la bozza in `CLAUDE.md` §2.3
filtra su `data_evento__lt=cutoff` con un delta fisso di 30 giorni. Sono
entrambi sbagliati. Il campo `data_evento` non esiste — il filtro va scritto su
`start_time`. Scelta chiusa: gli eventi del dominio sono giornalieri, quindi
`start_time` ed `end_time` cadono nello stesso mese e i due filtri darebbero lo
stesso risultato; `start_time` è comunque la regola corretta nell'unico caso in
cui divergono (evento che sfora la mezzanotte dell'ultimo giorno del mese),
perché l'attività appartiene al mese in cui è stata svolta, non a quello in cui
è finita. E il cutoff non è
un delta: la regola definita dall'utente è **giorno 8 del mese successivo a
quello dell'evento** (un evento di luglio scade l'8 agosto), perché la consegna
del timesheet è attesa entro il 5–6 e l'8 è il margine. Un delta di 30 giorni
darebbe date diverse per eventi dello stesso mese, che è esattamente ciò che la
regola non vuole.

**Fuori scope, ancora aperto**: i campi per la classificazione LLM (score di
confidenza, classificazione proposta vs confermata) di `CLAUDE.md` §4 punto 17
restano da progettare — questo cambiamento copre solo lo stato.

Migrazione: `classificationpipeline/0004_event_status_event_idx_event_active.py`.

**File**: `classificationpipeline/models.py`, `classificationpipeline/tests.py`

---

## `mark_passed_events`: la regola di scadenza vive nel manager, non nel command

Il command che promuove a `passato` gli eventi fuori tempo massimo è banale; la
decisione interessante è *dove* mettere la regola che decide cosa è scaduto.

La regola è: un evento del mese M scade al **giorno 8 del mese M+1**. La consegna
del timesheet è attesa entro il 5–6 del mese successivo, l'8 è il margine. Un
evento del 13/07 non ancora `compilato` diventa `passato` l'8 agosto.

È stata scritta come funzione di modulo più un `QuerySet` su `Event`, non dentro
`handle()`:

```python
def expiry_cutoff(at=None):
    now = timezone.localtime(at) if at is not None else timezone.localtime()
    first_of_this_month = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)

    if now.day >= 8:
        return first_of_this_month

    return (first_of_this_month - timedelta(days=1)).replace(day=1)


class EventQuerySet(models.QuerySet):

    def active(self):
        return self.filter(status__in=Event.ACTIVE_STATUSES)

    def expired(self, at=None):
        return self.active().filter(start_time__lt=expiry_cutoff(at))
```

Il command si riduce a due righe di lavoro vero:

```python
expired = Event.objects.expired()
updated = expired.update(status=Event.Status.PASSATO)
```

**Perché non dentro `handle()`.** La stessa regola serve ad almeno due
chiamanti: il job schedulato, e il controllo pre-compilazione previsto per il
caso limite di `CLAUDE.md` §2.3 (utente che il 5/08 compila luglio mentre il job
dell'8 sta per congelargli le righe). Quel secondo chiamante deve sapere *quali
eventi il job considererebbe scaduti* per decidere se riammetterli. Con la
regola dentro il command dovrebbe riscriverla, e due implementazioni della
stessa data prima o poi divergono. In un queryset si scrive una volta e si testa
direttamente, senza passare da `call_command`.

**Il calcolo del mese precedente senza aritmetica sui mesi.** Dal primo del mese,
`- timedelta(days=1)` porta sempre all'ultimo giorno del mese precedente
qualunque sia la sua lunghezza, e `.replace(day=1)` torna al suo primo giorno.
Attraversa febbraio e dicembre→gennaio senza casi speciali e senza `calendar.
monthrange`, che sarebbe l'alternativa ovvia e più verbosa.

**Il parametro `at`.** Serve a rendere la regola testabile: senza, ogni test
dovrebbe mockare `timezone.now()` o — peggio — passare o fallire a seconda del
giorno in cui gira la suite. Gli otto test di `ExpiryCutoffTests` passano date
fisse e coprono i bordi che contano: il 7 contro l'8, il salto d'anno da gennaio
a dicembre, febbraio corto.

**`update()` e non un loop con `save()`.** È una singola `UPDATE ... WHERE`: nessun
oggetto Python istanziato, nessun `post_save` sparato per riga. Su qualche
migliaio di eventi è la differenza fra istantaneo e minuti.

**`self.stdout.write` e non `print()`.** È ciò che rende il command testabile
(`call_command(..., stdout=StringIO())` cattura l'output, `print` no) ed evita
di aggiungere un altro `print` al debito già segnato in `CLAUDE.md` §4 punto 12.

**`--dry-run`.** È un job che scrive su righe che nessun utente sta guardando in
quel momento. La prima esecuzione in produzione deve poter mostrare il numero
prima di applicarlo.

**Cadenza giornaliera, non mensile.** Il command è idempotente per costruzione —
il filtro `active()` esclude già `passato` e `compilato` — quindi le run nei
giorni in cui non scade nulla non costano niente. Schedularlo solo il giorno 8
significherebbe perdere il giro se quella notte la macchina è spenta.

**Alternative scartate per lo scheduler**: Celery beat richiede Redis come
dipendenza infrastrutturale, sproporzionato per un command che gira una volta al
giorno; ha senso solo se Celery entra comunque nel progetto per le chiamate LLM
asincrone. Fra cron di sistema e `django-crontab`, il secondo tiene la
schedulazione in `settings.py`, quindi versionata nel repo invece che in una
crontab sul server. Nessuno dei due è ancora configurato — vedi
`DEPLOY_CHECKLIST.md`.

**Fuso orario**: la scadenza è una data di calendario, quindi dipende da quale
mezzanotte si intende. `TIME_ZONE` è stato portato da `'UTC'` a `'Europe/Rome'`
(2026-08-26) proprio per questo: con UTC il confine sarebbe stato sfasato di 1–2
ore e un evento dell'1/08 alle 00:30 ora italiana sarebbe risultato del 31/07,
scadendo un mese prima del dovuto. `USE_TZ = True` resta invariato, quindi il
database continua a memorizzare tutto in UTC: `TIME_ZONE` cambia solo come le
date vengono interpretate e mostrate, non come sono salvate. La toppa alternativa
— convertire a mano dentro `expiry_cutoff` — avrebbe lasciato il resto
dell'applicazione (form, template, admin) a ragionare in UTC.

### Perche' `compilato` e' uno stato terminale (domanda gia' emersa una volta)

La domanda naturale guardando il job e': "prima o poi anche gli eventi
`compilato` dovrebbero diventare `passato`, no?". La risposta e' no, e vale la
pena scriverlo perche' la domanda si ripresenta.

`passato` non significa "vecchio", significa **"scaduto senza essere mai stato
compilato"**. E' il complemento di `compilato`, non il suo successore: serve a
congelare gli eventi che non ce l'hanno fatta, cosi' che non restino `incerto`
per sempre e non vengano riproposti a ogni ciclo.

Siccome `status` e' un campo solo, una transizione `compilato -> passato`
**cancellerebbe** l'informazione precedente, e due cose previste dalla specifica
smetterebbero di funzionare:

- la **deduplicazione in compilazione** (§2.4): il controllo che impedisce a
  Selenium di reinserire un evento gia' consegnato e' `status == compilato`.
  Dopo la transizione un evento consegnato a luglio e uno abbandonato a luglio
  sarebbero indistinguibili;
- lo **storico delle compilazioni passate** (§2.1): non e' interrogabile se lo
  stato che diceva "questo l'abbiamo consegnato" e' stato sovrascritto.

La preoccupazione legittima dietro la domanda — "gli eventi vecchi non devono
pesare sulle query" — e' gia' risolta altrove: `compilato` e' gia' fuori
dall'indice parziale `idx_event_active`, esattamente come `passato`. Dal punto di
vista delle query attive i due stati si comportano gia' allo stesso modo.

Se in futuro servisse davvero una nozione di "archiviato", non e' uno stato ma un
**secondo asse**: un `archived_at` nullable accanto a `status`, che risponde a
"e' roba vecchia?" senza distruggere la risposta a "era stato compilato?".
Scartato oggi perche' nessuna query esistente ne avrebbe bisogno.

**File**: `classificationpipeline/models.py`,
`classificationpipeline/management/commands/mark_passed_events.py`,
`classificationpipeline/tests.py`

---

## Schedulazione di `mark_passed_events`: cron di sistema, giorni 8-12

**Cosa**: il comando `mark_passed_events` viene lanciato da cron utente con
schedule `0 3 8-12 * *` (ore 03:00 dei giorni dall'8 al 12 di ogni mese) tramite
il wrapper `cron_mark_passed_events.sh`; l'installazione della voce di crontab e'
automatizzata da `install_cron_step4.sh`.

**Alternative considerate**:
1. Esecuzione giornaliera (`0 3 * * *`).
2. Esecuzione singola il giorno 8 (`0 3 8 * *`).
3. `django-crontab` o Celery Beat come scheduler applicativo.

**Perche' questa scelta**:

Il confine di scadenza si muove **una volta al mese**: `expiry_cutoff` salta dal
primo del mese precedente al primo del mese corrente alle 00:00 del giorno 8. La
query di `Event.objects.expired()` non e' incrementale — e' un filtro completo su
`start_time < cutoff` — quindi una run al giorno 12 promuove tutto cio' che
avrebbe promosso la run del giorno 8. Ne segue che ripetere l'esecuzione dopo il
confine e' pura ridondanza contro il downtime, mentre le 20+ run mensili prima
del confine non aggiungono copertura.

Il giorno singolo (alternativa 2) e' invece troppo fragile: se la macchina e'
spenta o il servizio giu' proprio quella notte, gli eventi restano `incerto` per
un mese intero. Cinque run coprono una finestra di downtime di quattro giorni al
costo di quattro `UPDATE` a zero righe su colonne indicizzate.

Una precisazione, perche' l'intuizione naturale e' "mettiamo anche una run il
giorno 7, nel caso l'8 sia down": non funziona. Una run prima del giorno 8 usa
ancora il cutoff vecchio, quindi non puo' coprire il confine che non e' ancora
scattato. La ridondanza ha senso solo **dopo** il confine. (Una run pre-8 non e'
del tutto inutile in assoluto — promuove eventi di mesi ancora precedenti
importati in ritardo — ma non e' la copertura che si stava cercando.)

Scartati `django-crontab` e Celery (alternativa 3): entrambi aggiungono una
dipendenza, e Celery anche un broker e un processo residente, per un job che gira
cinque volte al mese e non ha bisogno di code, retry distribuiti o concorrenza.
Cron di sistema e' gia' presente e non ha parti in movimento.

**Nota sul timezone**: cron usa l'ora di sistema, `expiry_cutoff` usa
`timezone.localtime()` cioe' `TIME_ZONE = 'Europe/Rome'`. Oggi coincidono; su una
macchina di produzione impostata a UTC la run delle 03:00 cadrebbe alle 05:00
locali — comunque dentro la finestra, ma va verificato che le due non divergano
al punto da spostare il *giorno*. `install_cron_step4.sh` stampa il timezone di
sistema proprio per rendere la discrepanza visibile al momento dell'installazione.

**Perche' un wrapper invece della sola riga di crontab**: cron gira con ambiente
minimo (nessun virtualenv attivo, `PATH` ridotto, cwd `$HOME`). Il wrapper fissa
interprete (`.venv/bin/python`) e percorsi in modo esplicito e redirige stdout e
stderr su `logs/mark_passed_events.log`, che altrimenti cron scarterebbe o
manderebbe via mail locale. Il caricamento di `.env.dev` non e' un problema:
`settings.py` lo risolve via `BASE_DIR`, non dalla cwd.

**File**: `cron_mark_passed_events.sh`, `install_cron_step4.sh`,
`classificationpipeline/management/commands/mark_passed_events.py`

---

## Assorbimento dell'app `calendars` dentro `classificationpipeline`

**Cosa**: l'app Django `calendars` e' stata rimossa. Il suo contenuto residuo
(`SelectCalendarsView`, il template `eventi_calendars.html`, le rotte e i test)
vive ora in `classificationpipeline`. Nessuna URL pubblica e' cambiata:
`/calendars/` e `/events/` rispondono esattamente come prima.

**Alternative considerate**:
1. Lasciare le due app separate.
2. Spostare `SelectCalendarsView` in `authentication` invece che in
   `classificationpipeline`.
3. Rinominare l'app `classificationpipeline` in `calendars` (l'operazione
   inversa).

**Perche' questa scelta**:

L'app `calendars` era gia' un guscio vuoto. `models.py` e `admin.py` non
contenevano niente, e la migration
`calendars/0002_remove_event_calendar_delete_calendar_delete_event.py`
documentava che `Calendar` ed `Event` erano stati portati via tempo prima. Ne
restavano una view, un template, `urls.py` e i test — un'app intera per un
singolo endpoint.

Il motivo decisivo non e' pero' la dimensione, e' l'accoppiamento: **il form e il
suo handler erano spaccati tra due app**. `calendars/views.py` renderizzava il
select multi-calendario, e `classificationpipeline/views.py` ne consumava il POST
(`request.POST.getlist('calendars')`) rimandando indietro con
`redirect('calendars:eventi_calendars')`. Un solo ciclo request/response che
attraversava due app senza che la divisione corrispondesse a un confine reale.

Il rischio dell'operazione era basso per una ragione precisa, verificata prima di
toccare il codice: **non c'era piu' nessuna componente di schema**.
`classificationpipeline/0001_initial` dipende solo da
`authentication/0001_initial`, mai da `calendars`; le tabelle `calendars_*` erano
gia' state droppate (confermato con `\dt` sul database di sviluppo: esistono solo
`classificationpipeline_calendar` e `classificationpipeline_event`). Lo
spostamento e' quindi puro movimento di codice Python, senza migrazione di dati.

Alternativa 2 (`authentication`) era difendibile: la view e' OAuth-adjacent e
`authentication` possiede gia' `get_token_valido`. Scartata perche'
l'accoppiamento col POST handler e' piu' forte del legame con l'autenticazione:
la view produce l'input della pipeline, non gestisce credenziali — quelle le
delega gia' a `authentication.services`.

Alternativa 3 (rinominare al contrario) avrebbe dato all'app il nome della sua
struttura dati anziche' quello della sua responsabilita', e avrebbe comunque
richiesto di rinominare le tabelle esistenti — cioe' una migrazione vera, contro
zero.

**Il punto che richiedeva attenzione: le URL.** `classificationpipeline` occupava
gia' la rotta `''` ed era montata sotto il prefisso `events/`, mentre
`calendars.urls` stava sotto `calendars/`. Spostare la view e basta avrebbe
provocato una collisione. La soluzione e' spostare i prefissi dentro l'app e
includerla senza prefisso:

```python
# mysite/urls.py
path('', include('classificationpipeline.urls')),

# classificationpipeline/urls.py
app_name = 'classificationpipeline'
urlpatterns = [
    path('calendars/', views.SelectCalendarsView.as_view(), name='eventi_calendars'),
    path('events/', views.event_compare, name='event_compare'),
]
```

Le due alternative erano peggiori: includere due volte lo stesso modulo di URL
con prefissi diversi crea due istanze dello stesso namespace; annidare sotto
`/events/calendars/` avrebbe cambiato una URL pubblica e rotto i test esistenti.
Cosi' invece gli assert dei test su `/calendars/` e `/events/` sono rimasti
intatti, e l'unico cambiamento di codice chiamante e' stato
`redirect('calendars:eventi_calendars')` ->
`redirect('classificationpipeline:eventi_calendars')`.

**Residuo accettato**: in `django_migrations` restano due righe per l'app
`calendars`. Sono inerti — Django ignora le righe registrate per app che non
sono in `INSTALLED_APPS` — e cancellarle sarebbe puramente cosmetico, con il
rischio non nullo di una `DELETE` scritta male su una tabella di sistema. Il
criterio di accettazione e' stato `manage.py check` pulito, `makemigrations
--check` senza differenze, `migrate` senza nulla da applicare e la suite verde
(59 test).

**File**: `classificationpipeline/views.py`, `classificationpipeline/urls.py`,
`classificationpipeline/tests.py`,
`classificationpipeline/templates/classificationpipeline/eventi_calendars.html`,
`mysite/urls.py`, `mysite/settings.py`
