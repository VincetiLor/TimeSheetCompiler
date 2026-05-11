from django.db import models

# Create your models here.


class Hook(models.Model):
    id = models.AutoField(primary_key=True)
    docente = models.OneToOneField('authentication.Docente', on_delete=models.CASCADE)
    config = models.JSONField(default=dict)

    def __str__(self):
        return f"Hook for Docente {self.docente.id_unimore} - Config: {self.config}"