import re
from datetime import date, timedelta
from pathlib import Path

import jinja2
import pdfquery
import pdfquery.cache
import requests
from dateutil.rrule import rrule, DAILY

data_dir = Path("data")
data_dir.mkdir(exist_ok=True)
cache_dir = data_dir / "cache"
cache_dir.mkdir(exist_ok=True)


def download_day(session, day):
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

    return False


def download_data(since=None):
    with requests.Session() as session:
        for day in rrule(DAILY, dtstart=since or date(2020, 3, 16), until=date.today()):
            if not download_day(session, day):
                print(f'could not download data for {day:%Y-%m-%d}')


def parse_data(since=None):
    results = []

    for idx, day in enumerate(rrule(DAILY, dtstart=since or date(2020, 3, 16), until=date.today())):
        filename = f"{day:%Y-%m-%d}.pdf"
        file_path = data_dir / filename

        if not file_path.exists():
            if filename == '2020-03-26.pdf':
                results.append({'day': day, 'positive': 127})
                continue
            elif day.date() == date.today():
                break

        pdf = pdfquery.PDFQuery(file_path, parse_tree_cacher=pdfquery.cache.FileCache(f"{cache_dir}/"))
        pdf.load()
        text = ''.join([element.text for element in pdf.pq('LTTextLineHorizontal,LTTextBoxHorizontal') if element.text])

        def extract(patterns, default=None):
            for pattern in patterns:
                if matches := re.search(pattern, text):
                    return list(map(int, matches.groups()))
                else:
                    continue

            if default is not None:
                return [default,]

            raise Exception(f"extraction failed on {day}")

        quarantined, quarantined_daily = extract([r'kwarantanną domową / \(ostatnia doba\): (\d+) / \((\d+)\)',
                                                  r'kwarantanną domową na podstawie decyzji inspektora sanitarnego: (\d+)'])
        positive, = extract([r'z wynikiem dodatnim / \(ostatnia doba\): (\d+) /',
                             r'wynikiem dodatnim: (\d+)'])
        deaths, = extract([r'zgonów związanych z COVID-19 / \(ostatnia doba\): (\d+) /',
                           r'zgonów powiązanych z COVID-19: (\d+)'], default=0)
        recovered, = extract(['ozdrowieńców / \(ostatnia doba\): (\d+)'], default=0)

        results.append({
            'day': day,
            'quarantined': quarantined,
            'positive': positive,
            'deaths': deaths,
            'recovered': recovered
        })

        if idx > 0:
            results[idx].update({
                'daily': {
                    'quarantined': quarantined_daily,
                    'positive': positive - results[idx - 1]['positive'],
                    'deaths': deaths - results[idx - 1]['deaths'],
                    'recovered': recovered - results[idx - 1]['recovered']
                }
            })

    results = results[1:]

    n = 7

    chart_data = []
    for idx, result in enumerate(results[n - 1:], n):
        last_n = results[idx - n:idx]
        day = result['day']

        averages = {key: sum(result['daily'][key] for result in last_n) // n for key in ['positive', 'recovered']}

        chart_data.append(f"[new Date({day.year}, {day.month - 1}, {day.day}), "
                          f"{averages['positive']}, {averages['recovered']}, {result['daily']['deaths']}]")

    with open('template.html') as f:
        template = f.read()

    html = jinja2.Template(template).render(chart_data=f'[{",".join(chart_data)}]',
                                            table_data=results[-7:])

    with open('data/index.html', 'w') as f:
        f.write(html)


if __name__ == '__main__':
    download_data()
    parse_data(since=date.today() - timedelta(days=90))
