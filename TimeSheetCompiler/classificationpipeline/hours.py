from datetime import timedelta

from django.utils import timezone

from .models import Event

DAY_MINUTES = 8 * 60


def minutes_to_hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_to_minutes(text):
    if text == '':
        return 0
    hours, minutes = text.split(':')
    return int(hours) * 60 + int(minutes)


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


def events_by_day(events, all_day_hours, start, end):
    # {day: [(event, minutes), ...]}, only the days between start and end
    days = {}
    for event in events:
        for day, hours in event_hours(event, all_day_hours).items():
            if day < start or day > end:
                continue
            if day not in days:
                days[day] = []
            days[day].append((event, round(hours * 60)))
    return days


def event_start(pair):
    return pair[0].start_time


def complete_priority(codes):
    # The known categories in the given order, then the missing ones
    priority = []
    for code in codes:
        if code in Event.Category.values and code not in priority:
            priority.append(code)
    for code in Event.Category.values:
        if code not in priority:
            priority.append(code)
    return priority


def category_priority(timesheet):
    return complete_priority(timesheet.category_priority)


def plan_day(present, day_events, priority):
    # present: {category: minutes} already in the diary that day.
    # priority: every category code, highest first.
    # Returns ({category: minutes to write in the cell}, [(event, minutes written), ...])
    free = DAY_MINUTES - sum(present.values())

    ordered = []
    for category in priority:
        for event, minutes in sorted(day_events, key=event_start):
            if event.category == category:
                ordered.append((event, minutes))

    day_cells = {}
    minutes_per_event = []
    for event, minutes in ordered:
        if free <= 0:
            break
        minutes = min(minutes, free)
        if event.category not in day_cells:
            day_cells[event.category] = present.get(event.category, 0)
        day_cells[event.category] += minutes
        minutes_per_event.append((event, minutes))
        free -= minutes
    return day_cells, minutes_per_event


def compiled_hours_by_category(events):
    # {category: hours} from the minutes actually written on Esse3
    totals = {}
    for event in events:
        if event.compile_minutes is None:
            continue
        totals[event.category] = totals.get(event.category, 0) + event.compile_minutes / 60
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
    compiled = compiled_hours_by_category(compiled_events)
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
