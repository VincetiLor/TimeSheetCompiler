from django.db import models

# Create your models here.


class Calendar(models.Model):
    id = models.AutoField(primary_key=True)
    docente = models.ForeignKey('authentication.Docente', on_delete=models.CASCADE, related_name='calendars')
    calendar_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    event = models.JSONField(blank=True, null=True)
    

    def __str__(self):
        return f"{self.title} ({self.calendar_id})"
    


class Event(models.Model):
    id = models.AutoField(primary_key=True)
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name='events')
    event_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    def __str__(self):
        return f"{self.title} ({self.event_id})"