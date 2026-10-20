from datetime import datetime
from pathlib import Path

from django.conf import settings
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .hours import hhmm_to_minutes, minutes_to_hhmm

# Seconds to wait for Esse3 to load a page after a submit
PAGE_TIMEOUT = 30

# tipo_att_cod of each diary row, keyed by Event.Category value
CATEGORY_CODES = {
    'esami_profitto': '0010',
    'esami_laurea': '0020',
    'orientamento_tutorato': '0030',
    'ricevimento_studenti': '0040',
    'assistenza_laureandi': '0050',
    'compiti_organizzativi': '0060',
    'didattica_integrativa': '0070',
    'atenei_convenzionati': '0080',
    'scuole_specializzazione': '0090',
    'dottorati': '0100',
    'master': '0110',
    'preparazione_lezioni': '0120',
    'pnrr': '0121',
}


def open_driver():
    if settings.SELENIUM_BROWSER == 'chrome':
        options = webdriver.ChromeOptions()
        if settings.SELENIUM_HEADLESS:
            options.add_argument('--headless=new')
    else:
        options = webdriver.FirefoxOptions()
        if settings.SELENIUM_HEADLESS:
            options.add_argument('-headless')

    if settings.SELENIUM_REMOTE_URL:
        return webdriver.Remote(command_executor=settings.SELENIUM_REMOTE_URL, options=options)
    if settings.SELENIUM_BROWSER == 'chrome':
        return webdriver.Chrome(options=options)
    return webdriver.Firefox(options=options)


def parse_date(text):
    return datetime.strptime(text, '%d/%m/%Y').date()


def page_period(driver):
    # First and last day shown by the diary page
    first = driver.find_element(By.ID, 'DATA_MIN').get_attribute('value')
    last = driver.find_element(By.ID, 'DATA_MAX').get_attribute('value')
    return parse_date(first), parse_date(last)


def editable_period(driver):
    # First and last day Esse3 lets us change
    first = driver.find_element(By.ID, 'MIN_DATA_MOD').get_attribute('value')
    last = driver.find_element(By.ID, 'MAX_DATA_MOD').get_attribute('value')
    return parse_date(first), parse_date(last)


def submit(driver, button_id):
    # Clicks a submit button and waits for the new page. A saved copy of the
    # diary is never submitted: its form could still post to Esse3.
    if driver.current_url.startswith('file:'):
        return
    old_page = driver.find_element(By.ID, 'DATA_MIN')
    driver.find_element(By.ID, button_id).click()
    WebDriverWait(driver, PAGE_TIMEOUT).until(EC.staleness_of(old_page))
    WebDriverWait(driver, PAGE_TIMEOUT).until(
        EC.presence_of_element_located((By.ID, 'diario_doc-tabellaOre')))


def open_period(driver, day):
    # The diary page showing day. DATA_SELEZIONATA is also the id of a hidden
    # field: the visible input is found by its own id.
    field = driver.find_element(By.ID, 'diario_doc-inputSelData')
    field.clear()
    field.send_keys(f'{day:%d/%m/%Y}')
    submit(driver, 'btnVaiData')


def save_page(driver):
    submit(driver, 'sbmDef')


def read_page(driver):
    # {day: {category: minutes}} for every cell of the page. Each cell is found
    # through its hidden fields (date and activity code) sharing the same Row[@Num=N].
    # A code we don't know keeps the code as key: its hours still fill the day.
    categories = {}
    for category, code in CATEGORY_CODES.items():
        categories[code] = category

    page = {}
    for date_field in driver.find_elements(By.CSS_SELECTOR, 'input[name$="/dett_data"]'):
        row = date_field.get_attribute('name').removesuffix('/dett_data')
        code = driver.find_element(By.NAME, row + '/tipo_att_cod').get_attribute('value')
        text = driver.find_element(By.NAME, row + '/ore').get_attribute('value')
        day = parse_date(date_field.get_attribute('value'))
        page.setdefault(day, {})[categories.get(code, code)] = hhmm_to_minutes(text.strip())
    return page


def find_cell(driver, day, category):
    xpath = (
        "//table[@id='diario_doc-tabellaOre']//td["
        f"input[contains(@name, '/dett_data') and @value='{day:%d/%m/%Y}'] and "
        f"input[contains(@name, '/tipo_att_cod') and @value='{CATEGORY_CODES[category]}']"
        "]//input[@type='text']"
    )
    return driver.find_element(By.XPATH, xpath)


def write_page(driver, cells):
    # cells: {day: {category: minutes}}, the whole new value of each cell
    # (hours already there included). Only fills the inputs: saving is sbmDef.
    for day, categories in cells.items():
        for category, minutes in categories.items():
            cell = find_cell(driver, day, category)
            cell.clear()
            cell.send_keys(minutes_to_hhmm(minutes))


def open_diary(driver, credentials):
    # Login and menu up to the diary grid: phase 4. Until then, the test page.
    if settings.ESSE3_TEST_PAGE:
        driver.get(Path(settings.BASE_DIR, settings.ESSE3_TEST_PAGE).as_uri())
        return
    raise NotImplementedError('Esse3 login is not written yet')
