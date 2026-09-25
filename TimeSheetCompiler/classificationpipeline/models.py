from datetime import timedelta
from django.db import models
from django.db.models import Q
from django.utils import timezone

# Create your models here.


def expiry_cutoff(at=None):
    """Primo istante ancora "in tempo": tutto cio' che inizia prima e' scaduto.

    La regola di consegna e' che il timesheet di un mese va consegnato entro il
    5-6 del mese successivo; il giorno 8 e' il margine oltre il quale un evento
    non compilato si considera fuori tempo. Un evento del mese M scade quindi
    al giorno 8 del mese M+1.

    `at` serve ai test e alle simulazioni: senza, si usa l'ora corrente.
    """
    now = timezone.localtime(at) if at is not None else timezone.localtime()
    first_of_this_month = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)

    if now.day >= 8:        
        return first_of_this_month

    # Non siamo ancora all'8: il mese scorso ha ancora tempo per essere compilato 
    return (first_of_this_month - timedelta(days=1)).replace(day=1)


class Calendar(models.Model):

    docente = models.ForeignKey('authentication.Docente', on_delete=models.CASCADE, related_name='calendars')
    calendar_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    event = models.JSONField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['docente', 'calendar_id'],
                name='unique_calendar_to_professor',
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.calendar_id})"


class EventQuerySet(models.QuerySet):

    def active(self):
        """Eventi ancora in lavorazione, cioe' non ancora usciti dalla pipeline.

        Corrisponde al sottoinsieme coperto dall'indice parziale
        `idx_event_active`.
        """
        return self.filter(status__in=Event.ACTIVE_STATUSES)

    def expired(self, at=None):
        #Eventi attivi il cui termine di consegna e' gia' passato.

        return self.active().filter(start_time__lt=expiry_cutoff(at))


class Event(models.Model):


    class Category(models.TextChoices):
        RICERCA_FINANZIATA = 'ricerca_finanziata', 'Ricerca Finanziata'
        DIDATTICA = 'didattica', 'Didattica'
        RICERCA_BASE = 'ricerca_base', 'Ricerca Base'


    class Status(models.TextChoices):
        """
        incerto      -> non ancora classificato, o scartato temporaneamente
        classificato -> ha una classificazione (hook, LLM sopra soglia, manuale)
        compilato    -> la classificazione e' finita nel timesheet esterno
        passato      -> fuori dalla finestra di validita' senza essere compilato
        """

        INCERTO = 'incerto', 'Incerto'
        CLASSIFICATO = 'classificato', 'Classificato'
        COMPILATO = 'compilato', 'Compilato'
        PASSATO = 'passato', 'Passato'

    # Sottoinsieme "attivo": gli stati su cui lavora la pipeline. 
    ACTIVE_STATUSES = (Status.INCERTO, Status.CLASSIFICATO)

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name='events')
    event_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INCERTO,
    )
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        blank=True,
        default='',      
    )

    objects = EventQuerySet.as_manager()

    class Meta:
        indexes = [
                    models.Index(
                        fields=['calendar', 'start_time'],
                        condition=Q(status__in=['incerto', 'classificato']),
                        name='idx_event_active',
                    ),
                ]
        
        constraints = [
            models.UniqueConstraint(
                fields=['calendar', 'event_id'],
                name='unique_event_to_calendar',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=['classificato', 'compilato']) | ~Q(category='')
                ),
                name='category_required_when_classified',
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.event_id}) [{self.status}]"
