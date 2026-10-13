from django.db import models
from django.db.models import F, Q


class Timesheet(models.Model):

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    docente = models.ForeignKey('authentication.Docente', on_delete=models.CASCADE, related_name='timesheets')
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F('start_date')),
                name='timesheet_end_not_before_start',
            ),
        ]

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def __str__(self):
        return f"{self.start_date} - {self.end_date} [{self.status}]"


class EventQuerySet(models.QuerySet):

    def active(self):
        """Eventi ancora in lavorazione, cioe' non ancora usciti dalla pipeline.

        Corrisponde al sottoinsieme coperto dall'indice parziale
        `idx_event_active`.
        """
        return self.filter(status__in=Event.ACTIVE_STATUSES)


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
        """

        INCERTO = 'incerto', 'Incerto'
        CLASSIFICATO = 'classificato', 'Classificato'
        COMPILATO = 'compilato', 'Compilato'

    # Sottoinsieme "attivo": gli stati su cui lavora la pipeline.
    ACTIVE_STATUSES = (Status.INCERTO, Status.CLASSIFICATO)

    timesheet = models.ForeignKey(Timesheet, on_delete=models.CASCADE, related_name='events')
    # Google calendar id: an event id is unique only inside its calendar
    calendar_id = models.CharField(max_length=255)
    calendar_title = models.CharField(max_length=255, blank=True)
    event_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    all_day = models.BooleanField(default=False)
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
                        fields=['timesheet', 'start_time'],
                        condition=Q(status__in=['incerto', 'classificato']),
                        name='idx_event_active',
                    ),
                ]

        constraints = [
            models.UniqueConstraint(
                fields=['timesheet', 'calendar_id', 'event_id'],
                name='unique_event_to_timesheet',
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
