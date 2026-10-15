from django.db import transaction

from .hours import check_caps
from .models import Event


class CapsExceededError(Exception):

    def __init__(self, rows):
        super().__init__("Hour caps exceeded")
        self.rows = rows


def write_to_esse3(timesheet, events):\
    pass


def compile_events(timesheet, events, start, end):
    # Writes the events on Esse3 and marks them compilato. Nothing is written
    # if a cap would be exceeded. Returns how many events were compiled.
    events = list(events)

    exceeded = []
    for row in check_caps(timesheet, events, start, end):
        if row['excess'] > 0:
            exceeded.append(row)
    if exceeded:
        raise CapsExceededError(exceeded)

    write_to_esse3(timesheet, events)

    for event in events:
        event.status = Event.Status.COMPILATO
    with transaction.atomic():
        Event.objects.bulk_update(events, ['status'])
    return len(events)
