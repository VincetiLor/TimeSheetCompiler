from datetime import timezone as dt_timezone
from functools import partial

import httplib2
from allauth.socialaccount.models import SocialToken
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build


GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'


GOOGLE_REFRESH_TIMEOUT = (5, 10)
GOOGLE_API_TIMEOUT = 10

# Marks an encrypted value, so a token is never encrypted twice
ENCRYPTED_PREFIX = 'fernet:'

CALENDAR_LIST_FIELDS = 'nextPageToken,items(id,summary)'
EVENTS_FIELDS = (
    'nextPageToken,summary,description,timeZone,'
    'items(id,summary,description,start,end)'
)


class GoogleCredentialsError(Exception):
    """L'utente non ha credenziali Google utilizzabili: va rimandato al consenso.

    Copre l'account mai collegato, il refresh token assente e il refresh
    rifiutato da Google (accesso revocato). Gli errori che non dipendono dalle
    credenziali dell'utente restano le eccezioni originali.
    """


def encrypt_token(value):
    if not value or value.startswith(ENCRYPTED_PREFIX):
        return value
    encrypted = Fernet(settings.GOOGLE_TOKEN_KEY).encrypt(value.encode())
    return ENCRYPTED_PREFIX + encrypted.decode()


def decrypt_token(value):
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    encrypted = value.removeprefix(ENCRYPTED_PREFIX)
    try:
        return Fernet(settings.GOOGLE_TOKEN_KEY).decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise GoogleCredentialsError('Token Google non decifrabile') from exc


def get_credentials(user):

    token = (
        SocialToken.objects
        .filter(account__user=user, account__provider='google')
        .select_related('app')
        .order_by('-account__last_login')
        .first()
    )
    if token is None:
        raise GoogleCredentialsError('Nessun account Google collegato')
    if token.app is None:
        raise GoogleCredentialsError('Token Google senza applicazione associata')

    expiry = token.expires_at
    if expiry is not None:
        expiry = expiry.astimezone(dt_timezone.utc).replace(tzinfo=None)

    credentials = Credentials(
        token=decrypt_token(token.token),
        refresh_token=decrypt_token(token.token_secret) or None,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=token.app.client_id,
        client_secret=token.app.secret,
        expiry=expiry,
    )

    if expiry is None or credentials.expired:
        if not credentials.refresh_token:
            raise GoogleCredentialsError('Refresh token Google assente')
        try:
            credentials.refresh(partial(Request(), timeout=GOOGLE_REFRESH_TIMEOUT))
        except GoogleAuthError as exc:
            raise GoogleCredentialsError('Impossibile rinnovare il token') from exc

        token.token = credentials.token
        token.token_secret = credentials.refresh_token
        token.expires_at = credentials.expiry.replace(tzinfo=dt_timezone.utc)
        token.save(update_fields=['token', 'token_secret', 'expires_at'])

    return credentials


def get_calendar_service(user):
    """Client Google Calendar v3 autenticato come `user`."""
    http = AuthorizedHttp(
        get_credentials(user),
        http=httplib2.Http(timeout=GOOGLE_API_TIMEOUT),
    )
    
    return build('calendar', 'v3', http=http, cache_discovery=False)


def _execute(request):
    try:
        return request.execute()
    except RefreshError as exc:
        # Token revocato prima della scadenza: Google risponde 401 e il
        # tentativo di refresh automatico della libreria fallisce.
        raise GoogleCredentialsError('Accesso Google revocato') from exc


def list_calendars(service):
    """Tutti i calendari dell'account, come lista di `{'id', 'summary'}`."""
    resource = service.calendarList()
    request = resource.list(maxResults=250, fields=CALENDAR_LIST_FIELDS)
    items = []
    while request is not None:
        page = _execute(request)
        items.extend(page.get('items', []))
        request = resource.list_next(request, page)
    return items


def list_events(service, calendar_id, time_min, time_max):
    """Eventi di un calendario in [time_min, time_max).

    Restituisce la forma della risposta di Google (`summary`, `description`,
    `timeZone`, `items`), con `items` che raccoglie tutte le pagine.
    Solleva `googleapiclient.errors.HttpError` se Google rifiuta il calendario.
    """
    resource = service.events()
    request = resource.list(
        calendarId=calendar_id,
        singleEvents=True,
        orderBy='startTime',
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        maxResults=2500,
        fields=EVENTS_FIELDS,
    )
    data = None
    while request is not None:
        page = _execute(request)
        if data is None:
            data = page
            data['items'] = list(page.get('items', []))
        else:
            data['items'].extend(page.get('items', []))
        request = resource.list_next(request, page)
    data.pop('nextPageToken', None)
    return data
