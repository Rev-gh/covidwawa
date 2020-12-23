import csv
import re
from datetime import date, timedelta
from itertools import product
from pathlib import Path

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
                download_mz(session, day)


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


def parse_mz(day):
    filepath = data_dir / f"{day:%Y-%m-%d}.csv"

    if day.date() == date.today() and not filepath.exists():
        return None

    with open(filepath) as f:
        reader = csv.DictReader(f, delimiter=';')
        data = next((row for row in reader if row['Powiat/Miasto'] == 'Warszawa'), None)

        return {
            'day': day,
            'daily': {
                'positive': int(data.get('Liczba', data.get('Liczba przypadków'))),
                'deaths': int(data.get('Wszystkie przypadki śmiertelne', data.get('Zgony')))
            }
        }


def parse_data(since, n):
    results = []

    for idx, day in enumerate(rrule(DAILY, dtstart=since or date(2020, 3, 16), until=date.today())):
        if day.date() < date(2020, 3, 16):
            raise Exception("no data available before 2020-03-16")
        elif day.date() < date(2020, 11, 22):
            result = parse_psse(day)
        elif day.date() < date(2020, 11, 24):
            result = {'day': day, 'positive': 46889, 'deaths': 439}  # missing data, taken from previous day
        else:
            result = parse_mz(day)

        if not result:
            continue

        results.append(result)

        if idx > 0:
            for parameter in ['positive', 'deaths']:
                if parameter not in result:
                    result[parameter] = results[idx - 1][parameter] + result['daily'][parameter]

            results[idx].update({
                'daily': {
                    'positive': result['positive'] - results[idx - 1]['positive'],
                    'deaths': result['deaths'] - results[idx - 1]['deaths']
                }
            })

    results = results[1:]

    chart_data = []
    for idx, result in enumerate(results[n - 1:], n):
        last_n = results[idx - n:idx]
        day = result['day']

        averages = {key: sum(result['daily'][key] for result in last_n) // n for key in ['positive']}

        chart_data.append(f"[new Date({int(day.timestamp() * 1000)}), "
                          f"{averages['positive']}, {result['daily']['deaths']}]")

    with open('template.html') as f:
        template = f.read()

    html = jinja2.Template(template).render(chart_data=f'[{",".join(chart_data)}]',
                                            table_data=results[-n:])

    with open('data/index.html', 'w') as f:
        f.write(html)


if __name__ == '__main__':
    download_data()
    parse_data(since=date.today() - timedelta(days=90), n=7)

