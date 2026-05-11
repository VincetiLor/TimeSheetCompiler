from django.db import models
from django.conf import settings


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
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.cognome} {self.nome} ({self.id_unimore})".strip()
    
    class Meta:
        verbose_name = "Docente"
        verbose_name_plural = "Docenti"