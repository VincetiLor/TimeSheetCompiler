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

## (In sospeso) React/Tailwind per il frontend

Discusso ma **non implementato**: Tailwind è già in uso (via CDN) e ritenuto
sufficiente per il solo look. React avrebbe senso solo per l'unica pagina con stato
client complesso — la selezione multi-calendario (`calendars/eventi_calendars.html`,
gestita oggi con JS vanilla) e la futura UI di revisione classificazione LLM
(§2.5 di `CLAUDE.md`, non ancora implementata). Approccio proposto se/quando si
deciderà di procedere: "isola" React montata su un solo template via bundle
separato (Vite), resto del sito resta Django template puro — nessuna riscrittura
totale a SPA. Nessun codice scritto per questo punto.
