from django.db import models

# Create your models here.

class Docente(models.Model):
    id = models.AutoField(primary_key=True)
    id_unimore = models.IntegerField(unique=True)
    password = models.CharField(max_length=255)
    google_credentials = models.JSONField(blank=True, null=True)
    max_calendars = models.IntegerField(default=3)

    def __str__(self):
        return str(self.id_unimore)
