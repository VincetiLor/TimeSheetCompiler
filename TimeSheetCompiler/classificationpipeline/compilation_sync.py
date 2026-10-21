import time

from django.conf import settings
from django.db import connection, transaction
from django.views.decorators.debug import sensitive_variables
from selenium.common.exceptions import WebDriverException

from . import esse3_compilation as esse3
from .hours import check_caps, events_by_day, plan_day
from .models import Event


class CapsExceededError(Exception):

    def __init__(self, rows):
        super().__init__("Hour caps exceeded")
        self.rows = rows


class CompilationRunningError(Exception):
    pass


class Esse3PeriodError(Exception):
    # Esse3 did not open the page we asked for: nothing was written

    def __init__(self, day, first, last):
        super().__init__(f"Asked for {day}, Esse3 showed {first} - {last}")
        self.day = day
        self.first = first
        self.last = last


class Esse3SaveError(Exception):
    # After sbmDef the page is not what we expect: the hours of these days
    # may be on Esse3 while the DB still says classificato

    def __init__(self, first, last):
        super().__init__(f"Unexpected diary after saving {first} - {last}")
        self.first = first
        self.last = last


def save_day(minutes_per_event):
    # Called after each day saved on Esse3, so the DB is never behind the diary.
    # An all-day event gets called once per day: its minutes add up.
    events = []
    for event, minutes in minutes_per_event:
        if event.compile_minutes is None:
            event.compile_minutes = 0
        event.compile_minutes += minutes
        event.status = Event.Status.COMPILATO
        events.append(event)
    with transaction.atomic():
        Event.objects.bulk_update(events, ['status', 'compile_minutes'])


def check_page(driver, first, last, present, cells_to_compile, planned_compilation):
    # Reads the page again after sbmDef. A day is saved when its column is
    # present + ours, not saved when it is still present only.
    try:
        esse3.open_period(driver, first)
        after = esse3.read_page(driver, cells_to_compile)
    except WebDriverException as error:
        raise Esse3SaveError(first, last) from error

    saved = []
    not_saved = []
    for day in sorted(cells_to_compile):
        expected = dict(present[day])
        expected.update(cells_to_compile[day])
        if after.get(day) == expected:
            saved.append(day)
        elif after.get(day) == present[day]:
            not_saved.append(day)
        else:
            raise Esse3SaveError(first, last)

    # One sbmDef saves the whole page: half saved means something else happened
    if saved and not_saved:
        raise Esse3SaveError(first, last)

    for day in saved:
        save_day(planned_compilation[day])


def fill_diary(driver, days_to_compile):
    # days_to_compile: {day: [(event, minutes), ...]}, driver already on the diary.
    # Each Esse3 page holds 14 days and is saved with a single sbmDef.
    min_day, max_day = esse3.editable_period(driver)
    pending = []
    for day in sorted(days_to_compile):
        if min_day <= day <= max_day:
            pending.append(day)

    while pending:
        esse3.open_period(driver, pending[0])
        first, last = esse3.page_period(driver)
        if pending[0] < first or pending[0] > last:
            raise Esse3PeriodError(pending[0], first, last)

        page_days = []
        later = []
        for day in pending:
            if day > last:
                later.append(day)
            else:
                page_days.append(day)
        present = esse3.read_page(driver, page_days)

        cells_to_compile = {}
        planned_compilation = {}
        for day in page_days:
            day_cells, minutes_per_event = plan_day(present[day], days_to_compile[day])
            if minutes_per_event:
                cells_to_compile[day] = day_cells
                planned_compilation[day] = minutes_per_event
        pending = later

        if not cells_to_compile:
            continue
        esse3.write_page(driver, cells_to_compile)
        esse3.save_page(driver)
        check_page(driver, first, last, present, cells_to_compile, planned_compilation)


@sensitive_variables('credentials')
def write_to_esse3(timesheet, events, start, end, credentials):
    days_to_compile = events_by_day(events, timesheet.all_day_hours, start, end)
    if not days_to_compile:
        return
    driver = esse3.open_driver()
    try:
        esse3.open_diary(driver, credentials)
        fill_diary(driver, days_to_compile)
    finally:
        # Development only: time to look at the browser before it closes
        time.sleep(settings.SELENIUM_END_PAUSE)
        driver.quit()


@sensitive_variables('credentials')
def compile_events(timesheet, events, start, end, credentials):
    # Writes the events on Esse3 and marks compilato only those actually
    # written. Nothing is written if a cap would be exceeded.
    # Returns (compiled count, [(event, minutes written, minutes planned)] of cut events).
    # The Postgres lock stops a second compilation of the timesheet (double click).
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [timesheet.pk])
        locked = cursor.fetchone()[0]
    if not locked:
        raise CompilationRunningError()
    try:
        return compile_locked(timesheet, events, start, end, credentials)
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [timesheet.pk])


@sensitive_variables('credentials')
def compile_locked(timesheet, events, start, end, credentials):
    # Events are read here, after the lock: a second click finds them compilato
    events = list(events)

    exceeded = []
    for row in check_caps(timesheet, events, start, end):
        if row['excess'] > 0:
            exceeded.append(row)
    if exceeded:
        raise CapsExceededError(exceeded)

    planned_minutes = {}
    for event in events:
        planned_minutes[event.pk] = 0
    for day_events in events_by_day(events, timesheet.all_day_hours, start, end).values():
        for event, minutes in day_events:
            planned_minutes[event.pk] += minutes

    write_to_esse3(timesheet, events, start, end, credentials)

    compiled = 0
    cut = []
    for event in events:
        if event.status != Event.Status.COMPILATO:
            continue
        compiled += 1
        # Only classificato events get here, so compile_minutes starts from None
        written_minutes = event.compile_minutes
        if 0 < written_minutes < planned_minutes[event.pk]:
            cut.append((event, written_minutes, planned_minutes[event.pk]))
    return compiled, cut
