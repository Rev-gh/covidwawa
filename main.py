import csv
import re
from datetime import date, timedelta
from io import BytesIO
from itertools import product
from pathlib import Path
from zipfile import ZipFile

import jinja2
import pdfquery
import pdfquery.cache
import requests
from bs4 import BeautifulSoup
from dateutil.rrule import rrule, DAILY

data_dir = Path("data")
data_dir.mkdir(exist_ok=True)
cache_dir = data_dir / "cache"
cache_dir.mkdir(exist_ok=True)


def download_psse(session, day):
    filename = data_dir / f"{day:%Y-%m-%d}.pdf"

    if filename.exists():
        return True

    urls = [
        f"https://pssewawa.pl/download/KORONAWIRUS-komunikat-PPIS-{day:%Y-%m-%d}.pdf",
        f"https://pssewawa.pl/download/KORONAWIRUS-Komunikat-PPIS-{day:%Y-%m-%d}.pdf",
        f"https://pssewawa.pl/download/KORONAWIRUS-komunikat-PPIS--{day:%Y-%m-%d}.pdf",
        f"https://pssewawa.pl/download/KORONAWIRUS-komunikat--PPIS-{day:%Y-%m-%d}.pdf",
        f"https://pssewawa.pl/download/KORONAWIRUS-komunikat-PPIS-{day:%Y-%m-%d}-.pdf",
        f"https://pssewawa.pl/download/KORONAWIRUS-komunikat-PPIS-{day:%Y-%m-%d}-k.pdf"
    ]

    for url in urls:
        response = session.get(url)

        if response.status_code == 200:
            with(open(filename, 'wb')) as f:
                f.write(response.content)
                return True

    print(f'could not download data for {day:%Y-%m-%d} from PSSE')


def download_mz(session, day):
    filename = data_dir / f"{day:%Y-%m-%d}.csv"

    if filename.exists():
        return True

    url = get_mz_archive_url(session, day)

    if not url:
        mainpage_response = session.get('https://gov.pl/web/koronawirus/mapa-zarazen-koronawirusem-sars-cov-2-powiaty')
        mainpage_soup = BeautifulSoup(mainpage_response.content, features="lxml")
        if f'{day:%d.%m.%Y}' in mainpage_soup.select_one('.global-stats > p:first-child').text:
            url = mainpage_soup.select_one('.file-download:not([v-if])')['href']

    if url:
        response = session.get(url)
        response.encoding = 'windows-1250'

        with(open(filename, 'w')) as f:
            f.write(response.text)
            return True

    print(f'could not download data for {day:%Y-%m-%d} from MZ')


def get_mz_archive_url(session, day):
    archive_response = session.get('https://gov.pl/web/koronawirus/pliki-archiwalne-powiaty')
    archive_soup = BeautifulSoup(archive_response.content, features="lxml")

    patterns = [
        f'{day:%d_%m_%y}',
        f'{day:%d_%m_%Y}',
        f'{day:%Y%m%d}',
        f'{day:%d%m%y}',
    ]

    href = next((element['href'] for element, pattern in product(archive_soup.select('#main-content a'), patterns)
                 if element.select_one('.extension').text.replace('\u200b', '').startswith(pattern)), None)
    url = f"https://gov.pl{href}"

    return url if href else None


def download_arcgis_archive(session):
    url = 'https://www.arcgis.com/sharing/rest/content/items/e16df1fa98c2452783ec10b0aea4b341/data'

    with ZipFile(BytesIO(session.get(url).content)) as arcgis_archive:
        for name in arcgis_archive.namelist():
            if match := re.search(r'^(\d{4})(\d{2})(\d{2}).*\.csv$', name):
                data = arcgis_archive.read(name).decode('windows-1250', errors='ignore')
                filename = data_dir / f'{match[1]}-{match[2]}-{match[3]}.csv'
                filename.write_text(data)


def download_arcgis_current(session, filename):
    url = 'https://www.arcgis.com/sharing/rest/content/items/6ff45d6b5b224632a672e764e04e8394/data'

    filename.write_text(session.get(url).content.decode('windows-1250'))


def download_arcgis(session, day):
    filename = data_dir / f"{day:%Y-%m-%d}.csv"

    if not filename.exists():
        if day.date() == date.today():
            download_arcgis_current(session, filename)
        else:
            download_arcgis_archive(session)

    return filename.exists()


def download_data(since=None):
    with requests.Session() as session:
        for day in rrule(DAILY, dtstart=since or date(2020, 3, 16), until=date.today()):
            if day.date() < date(2020, 3, 16):
                raise Exception("no data available before 2020-03-16")
            elif day.date() < date(2020, 11, 22):
                download_psse(session, day)
            elif day.date() < date(2020, 11, 24):
                pass  # there is no data
            else:
                download_arcgis(session, day)


def parse_psse(day):
    pdf = pdfquery.PDFQuery(data_dir / f"{day:%Y-%m-%d}.pdf", parse_tree_cacher=pdfquery.cache.FileCache(f"{cache_dir}/"))
    pdf.load()
    text = ''.join([element.text for element in pdf.pq('LTTextLineHorizontal,LTTextBoxHorizontal') if element.text])

    def extract(patterns, default=None):
        for pattern in patterns:
            if matches := re.search(pattern, text):
                return list(map(int, matches.groups()))
            else:
                continue

        if default is not None:
            return [default, ]

        raise Exception(f"extraction failed on {day}")

    quarantined, quarantined_daily = extract([r'kwarantanną domową / \(ostatnia doba\): (\d+) / \((\d+)\)',
                                              r'kwarantanną domową na podstawie decyzji inspektora sanitarnego: (\d+)'])
    isolated, isolated_daily = extract([r'izolacją domową / \(ostatnia doba\): (\d+) / \((\d+)\)'], default=0)
    positive, = extract([r'z wynikiem dodatnim / \(ostatnia doba\): (\d+) /',
                         r'wynikiem dodatnim: (\d+)'])
    deaths, = extract([r'zgonów związanych z COVID-19 / \(ostatnia doba\): (\d+) /',
                       r'zgonów powiązanych z COVID-19: (\d+)'], default=0)
    recovered, = extract(['ozdrowieńców / \(ostatnia doba\): (\d+)'], default=0)

    return {
        'day': day,
        'quarantined': quarantined,
        'positive': positive,
        'deaths': deaths,
        'recovered': recovered,
        'isolated': isolated,
        'daily': {
            'quarantined': quarantined_daily,
            'isolated': isolated_daily
        }
    }


def parse_mz_and_arcgis(day):
    filepath = data_dir / f"{day:%Y-%m-%d}.csv"

    if day.date() == date.today() and not filepath.exists():
        return None

    with open(filepath) as f:
        reader = csv.DictReader(f, delimiter=';')
        data = next((row for row in reader if row.get('Powiat/Miasto', row.get('powiat_miasto', row.get('powiat'))) == 'Warszawa'), None)

        return {
            'day': day,
            'daily': {
                'positive': int(float(data.get('liczba_przypadkow', data.get('liczba_wszystkich_zakazen')))),
                'deaths': int(float(data['zgony'])),
                'tests': int(float(data['liczba_wykonanych_testow'])) if 'liczba_wykonanych_testow' in data else None
            }
        }


def parse_data(since, n):
    results = []

    for idx, day in enumerate(rrule(DAILY, dtstart=since or date(2020, 3, 16), until=date.today())):
        if day.date() < date(2020, 3, 16):
            raise Exception("no data available before 2020-03-16")
        elif day.date() < date(2020, 11, 23):
            result = parse_psse(day)
        elif day.date() == date(2020, 11, 23):
            # missing data, average between previous and next day
            result = {'day': day, 'daily': {'positive': 617, 'deaths': 7}}
        elif day.date() == date(2021, 10, 20):
            result = {'day': day, 'daily': {'positive': 386, 'deaths': 0, 'tests': 3025}}
        else:
            result = parse_mz_and_arcgis(day)

        if not result:
            continue

        results.append(result)

        if idx > 0:
            for parameter in ['positive', 'deaths']:
                if parameter not in result:
                    result[parameter] = results[idx - 1][parameter] + result['daily'][parameter]

            daily_positive = result['positive'] - results[idx - 1]['positive']

            results[idx].update({
                'daily': {
                    'positive': daily_positive,
                    'deaths': result['deaths'] - results[idx - 1]['deaths'],
                    'tests': result['daily'].get('tests')
                }
            })

    results = results[1:][-365:]

    viewport_y = 0
    chart_data = []
    for idx, result in enumerate(results[n - 1:], n):
        last_n = results[idx - n:idx]
        day = result['day']

        averages = {key: sum(result['daily'][key] for result in last_n) // n for key in ['positive']}
        viewport_y = max(viewport_y, result['daily']['positive'])

        chart_data.append({
            'timestamp': int(day.timestamp() * 1000),
            'positive_average': averages['positive'],
            'positive': result['daily']['positive'],
            'deaths': result['daily']['deaths'],
            'tests': result['daily']['tests']
        })

    viewport_y += 100

    with open('template.html') as f:
        template = f.read()

    html = jinja2.Template(template).render(chart_data=chart_data, table_data=results[-n:], viewport_y=viewport_y)

    with open('data/index.html', 'w') as f:
        f.write(html)


if __name__ == '__main__':
    since = date(2022, 7, 18)

    download_data(since)
    parse_data(since, n=14)
