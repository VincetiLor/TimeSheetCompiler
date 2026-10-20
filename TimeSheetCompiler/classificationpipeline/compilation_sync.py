import time

from django.conf import settings
from django.db import transaction
from selenium.common.exceptions import WebDriverException

from . import esse3_compilation as esse3
from .hours import check_caps, events_by_day, plan_day
from .models import Event


class CapsExceededError(Exception):

    def __init__(self, rows):
        super().__init__("Hour caps exceeded")
        self.rows = rows


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


def save_day(written):
    # Called after each day saved on Esse3, so the DB is never behind the diary.
    # An all-day event gets called once per day: its minutes add up.
    events = []
    for event, minutes in written:
        if event.compile_minutes is None:
            event.compile_minutes = 0
        event.compile_minutes += minutes
        event.status = Event.Status.COMPILATO
        events.append(event)
    with transaction.atomic():
        Event.objects.bulk_update(events, ['status', 'compile_minutes'])


def check_page(driver, first, last, present, cells, planned):
    # Reads the page again after sbmDef. A day is saved when its column is
    # present + ours, not saved when it is still present only.
    try:
        esse3.open_period(driver, first)
        after = esse3.read_page(driver)
    except WebDriverException as error:
        raise Esse3SaveError(first, last) from error

    saved = []
    not_saved = []
    for day in sorted(cells):
        expected = dict(present[day])
        expected.update(cells[day])
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
        save_day(planned[day])


def fill_diary(driver, days):
    # days: {day: [(event, minutes), ...]}, driver already on the diary.
    # Each Esse3 page holds 14 days and is saved with a single sbmDef.
    min_day, max_day = esse3.editable_period(driver)
    pending = []
    for day in sorted(days):
        if min_day <= day <= max_day:
            pending.append(day)

    while pending:
        esse3.open_period(driver, pending[0])
        first, last = esse3.page_period(driver)
        if pending[0] < first or pending[0] > last:
            raise Esse3PeriodError(pending[0], first, last)
        present = esse3.read_page(driver)

        cells = {}
        planned = {}
        later = []
        for day in pending:
            if day > last:
                later.append(day)
                continue
            day_cells, written = plan_day(present[day], days[day])
            if written:
                cells[day] = day_cells
                planned[day] = written
        pending = later

        if not cells:
            continue
        esse3.write_page(driver, cells)
        esse3.save_page(driver)
        check_page(driver, first, last, present, cells, planned)


def write_to_esse3(timesheet, events, start, end, credentials):
    days = events_by_day(events, timesheet.all_day_hours, start, end)
    if not days:
        return
    driver = esse3.open_driver()
    try:
        esse3.open_diary(driver, credentials)
        fill_diary(driver, days)
    finally:
        # Development only: time to look at the browser before it closes
        time.sleep(settings.SELENIUM_END_PAUSE)
        driver.quit()


def compile_events(timesheet, events, start, end, credentials):
    # Writes the events on Esse3 and marks compilato only those actually
    # written. Nothing is written if a cap would be exceeded.
    # Returns how many events were compiled.
    events = list(events)

    exceeded = []
    for row in check_caps(timesheet, events, start, end):
        if row['excess'] > 0:
            exceeded.append(row)
    if exceeded:
        raise CapsExceededError(exceeded)

    write_to_esse3(timesheet, events, start, end, credentials)

    compiled = 0
    for event in events:
        if event.status == Event.Status.COMPILATO:
            compiled += 1
    return compiled
