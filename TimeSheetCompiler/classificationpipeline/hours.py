from datetime import timedelta

from django.utils import timezone

from .models import Event


def event_hours(event, all_day_hours):
    # {day: hours}. A timed event counts on the day it starts.
    start = timezone.localtime(event.start_time)

    if not event.all_day:
        duration = event.end_time - event.start_time
        return {start.date(): duration.total_seconds() / 3600}

    # Google's all-day end date is exclusive: a one-day event ends the next midnight
    last_day = timezone.localtime(event.end_time).date()
    hours = {}
    day = start.date()
    while day < last_day:
        hours[day] = all_day_hours
        day += timedelta(days=1)
    return hours


def hours_by_category(events, all_day_hours, start, end):
    # {category: hours}, counting only the days between start and end if given
    totals = {}
    for event in events:
        for day, hours in event_hours(event, all_day_hours).items():
            if day > end or day < start:
                continue
            totals[event.category] = totals.get(event.category, 0) + hours
    return totals


def cap_row(label, max_hours, compiled, to_compile):
    used = compiled + to_compile
    return {
        'label': label,
        'max_hours': max_hours,
        'compiled': compiled,
        'to_compile': to_compile,
        'used': used,
        'excess': used - max_hours,
    }


def check_caps(timesheet, to_compile, start, end):
    # One row per cap: hours already compiled in the whole timesheet plus the
    # hours of to_compile inside start-end. excess > 0 means the cap is exceeded.
    compiled_events = timesheet.events.filter(status=Event.Status.COMPILATO)
    compiled = hours_by_category(
        compiled_events, timesheet.all_day_hours, timesheet.start_date, timesheet.end_date)
    new = hours_by_category(to_compile, timesheet.all_day_hours, start, end)

    max_hours_by_category = {}
    for cap in timesheet.hour_caps.all():
        max_hours_by_category[cap.category] = cap.max_hours

    rows = []
    for category, label in Event.Category.choices:
        if category not in max_hours_by_category:
            continue
        rows.append(cap_row(
            label,
            max_hours_by_category[category],
            compiled.get(category, 0),
            new.get(category, 0),
        ))

    if timesheet.total_hours_cap is not None:
        rows.append(cap_row(
            'Totale',
            timesheet.total_hours_cap,
            sum(compiled.values()),
            sum(new.values()),
        ))
    return rows
