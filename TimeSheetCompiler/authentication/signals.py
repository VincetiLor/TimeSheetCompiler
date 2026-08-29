from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Docente
from hooks.models import Hook


@receiver(post_save, sender=Docente)
def create_hook_for_docente(sender, instance, created, raw=False, **kwargs):
    # raw=True means loaddata is replaying a fixture: the dump already carries
    # the Hook rows, so creating one here would collide with the row loaddata
    # is about to insert.
    if raw or not created:
        return
    Hook.objects.get_or_create(docente=instance)
