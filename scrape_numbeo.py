#!/usr/bin/env python3
"""Scrape every selectable city on Numbeo's cost-of-living section into one CSV.

One row per city. For each city it collects:
  * every price item on the city page (55 items in 10 categories), as the
    average plus the min/max range when Numbeo shows one, in USD
  * the summary estimates (monthly cost for a family of four / single person,
    excluding rent) in USD and in the local currency
  * how many entries and contributors the city had in the past 12 months, and
    the last update date
  * the six cost-of-living indices from the current rankings page (only the
    ~570 ranked cities have these; the rest are blank)

Cities come from the "Select city in <country>" dropdown on every country page,
so the list matches what the site lets you select.

The run is resumable: parsed cities are appended to cache/cities.jsonl and
skipped on the next run, so you can stop it with Ctrl-C and start it again.

Usage:
    python3 scrape_numbeo.py                     # full run
    python3 scrape_numbeo.py --limit 10          # quick test on the first 10 cities
    python3 scrape_numbeo.py --country Canada --country Japan
    python3 scrape_numbeo.py --csv-only          # rebuild the CSV from the cache
"""

import argparse
import csv
import html
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

BASE = "https://www.numbeo.com/cost-of-living"
HOME_URL = f"{BASE}/"
COUNTRY_URL = f"{BASE}/country_result.jsp"
CITY_URL = f"{BASE}/city_result.jsp"
RANKINGS_URL = f"{BASE}/rankings_current.jsp"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

META_COLUMNS = [
    "country",
    "city",
    "url",
    "currency",
    "entries_past_12_months",
    "contributors_past_12_months",
    "last_update",
    "family_of_four_monthly_cost_usd",
    "single_person_monthly_cost_usd",
    "local_currency_symbol",
    "family_of_four_monthly_cost_local",
    "single_person_monthly_cost_local",
]

INDEX_COLUMNS = [
    ("Cost of Living Index", "cost_of_living_index"),
    ("Rent Index", "rent_index"),
    ("Cost of Living Plus Rent Index", "cost_of_living_plus_rent_index"),
    ("Groceries Index", "groceries_index"),
    ("Restaurant Price Index", "restaurant_price_index"),
    ("Local Purchasing Power Index", "local_purchasing_power_index"),
]


# ---------------------------------------------------------------- HTTP

class AccessRestricted(Exception):
    """Numbeo served its "Monthly page limit reached" page instead of the data.

    Free users get only a handful of city pages per month (the limit resets on
    the 1st), so the run stops here rather than retrying into the block.
    """


class Fetcher:
    """Rate-limited GET shared by all worker threads, with retries and backoff."""

    def __init__(self, delay, retries=5):
        self.delay = delay
        self.retries = retries
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._local = threading.local()

    def _session(self):
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
            self._local.session = s
        return self._local.session

    def _wait_turn(self):
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self.delay
        if wait > 0:
            time.sleep(wait)

    def get(self, url, params=None, expect=None):
        """Return the response. `expect` is a substring a valid page must contain."""
        last_error = None
        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self._session().get(url, params=params, timeout=30)
                if "Monthly page limit reached" in r.text:
                    raise AccessRestricted(r.url)
                if r.status_code == 200 and (expect is None or expect(r.text)):
                    return r
                last_error = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    break
            except requests.RequestException as e:
                last_error = repr(e)
            backoff = min(15 * 2 ** attempt, 300)
            log(f"  retry {attempt + 1}/{self.retries} in {backoff}s ({last_error}): {url} {params or ''}")
            time.sleep(backoff)
        raise RuntimeError(f"giving up on {url} {params or ''}: {last_error}")


# ---------------------------------------------------------------- parsing helpers

def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def to_number(s):
    """'$26,473.01' -> 26473.01, '?' or '' -> None."""
    s = re.sub(r"[^0-9.\-]", "", s or "")
    if s in ("", ".", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower().replace("%", "pct")).strip("_")


def parse_select_options(page, select_id):
    m = re.search(rf'<select[^>]*id="{select_id}"[^>]*>(.*?)</select>', page, re.S)
    if not m:
        return []
    values = re.findall(r'<option value="([^"]*)"', m.group(1))
    return [html.unescape(v) for v in values if v.strip()]


def parse_city_page(page):
    """Pull every data point off a city's cost-of-living page."""
    rec = {"items": []}

    m = re.search(r'<table class="data_wide_table[^"]*">(.*?)</table>', page, re.S)
    if m:
        category = ""
        for row in re.split(r"<tr[ >]", m.group(1)):
            cat = re.search(r'<div class="category_title">(.*?)</div>', row, re.S)
            if cat:
                category = strip_tags(cat.group(1))
                continue
            if "priceValue" not in row:
                continue
            label = strip_tags(re.search(r"<td[^>]*>(.*?)</td>", row, re.S).group(1))
            value = re.search(r'class="first_currency">(.*?)</span>', row, re.S)
            low = re.search(r'class="barTextLeft">(.*?)</span>', row, re.S)
            high = re.search(r'class="barTextRight">(.*?)</span>', row, re.S)
            rec["items"].append({
                "category": category,
                "item": label,
                "avg": to_number(strip_tags(value.group(1))) if value else None,
                "min": to_number(strip_tags(low.group(1))) if low else None,
                "max": to_number(strip_tags(high.group(1))) if high else None,
            })

    m = re.search(r'class="inside_box_summary_content">(.*?)</div>', page, re.S)
    if m:
        summary = strip_tags(m.group(1))
        for who, key in (("family of four", "family_of_four"), ("single person", "single_person")):
            s = re.search(rf"{who} are\s*([^\s(,]*[\d.,]+)\s*(?:\(([^)]+)\))?", summary)
            if s:
                rec[f"{key}_monthly_cost_usd"] = to_number(s.group(1))
                if s.group(2):
                    rec[f"{key}_monthly_cost_local"] = to_number(s.group(2))
                    rec["local_currency_symbol"] = re.sub(r"[\d.,\s]+", "", s.group(2))

    text = strip_tags(page)
    m = re.search(r"had ([\d,]+) entr(?:y|ies) in the past 12 months by ([\d,]+) different contributors?", text)
    if m:
        rec["entries_past_12_months"] = int(m.group(1).replace(",", ""))
        rec["contributors_past_12_months"] = int(m.group(2).replace(",", ""))
    m = re.search(r"Last update: (\d{1,2} \w+ \d{4})", text)
    if m:
        try:
            rec["last_update"] = datetime.strptime(m.group(1), "%d %B %Y").date().isoformat()
        except ValueError:
            rec["last_update"] = m.group(1)
    return rec


def parse_rankings(page):
    """Map canonical city URL -> {index column: value}."""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        link = re.search(r'class="cityOrCountryInIndicesTable"><a href="([^"]+)"', row)
        if not link:
            continue
        cells = [strip_tags(c) for c in re.findall(r'<td style="text-align: right">(.*?)</td>', row, re.S)]
        out[link.group(1)] = {col: to_number(v) for (_, col), v in zip(INDEX_COLUMNS, cells)}
    return out


def looks_like_city_page(text):
    return "data_wide_table" in text or "entries in the past 12 months" in text


# ---------------------------------------------------------------- scraping phases

def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_city_list(fetcher, cache_dir, only_countries, refresh):
    path = cache_dir / "city_list.json"
    city_list = {} if refresh or not path.exists() else json.loads(path.read_text())

    countries = parse_select_options(fetcher.get(HOME_URL).text, "country")
    if only_countries:
        missing = set(only_countries) - set(countries)
        if missing:
            sys.exit(f"Unknown countries: {', '.join(sorted(missing))}")
        countries = [c for c in countries if c in only_countries]

    todo = [c for c in countries if c not in city_list]
    if todo:
        log(f"Fetching city lists for {len(todo)} of {len(countries)} countries...")
    for i, country in enumerate(todo, 1):
        page = fetcher.get(COUNTRY_URL, {"country": country}).text
        city_list[country] = parse_select_options(page, "city")
        log(f"  [{i}/{len(todo)}] {country}: {len(city_list[country])} cities")
        path.write_text(json.dumps(city_list, indent=1, ensure_ascii=False))

    return [(c, city) for c in countries for city in city_list.get(c, [])]


def scrape_city(fetcher, country, city, currency):
    r = fetcher.get(
        CITY_URL,
        {"country": country, "city": city, "displayCurrency": currency},
        expect=looks_like_city_page,
    )
    rec = parse_city_page(r.text)
    rec.update(country=country, city=city, currency=currency, url=r.url.split("?")[0])
    return rec


def scrape_cities(fetcher, cities, records_path, currency, workers):
    done = set()
    if records_path.exists():
        with records_path.open() as f:
            for line in f:
                r = json.loads(line)
                done.add((r["country"], r["city"]))
    todo = [c for c in cities if c not in done]
    log(f"{len(cities)} cities selected, {len(cities) - len(todo)} already cached, {len(todo)} to fetch.")
    if not todo:
        return []

    failures = []
    write_lock = threading.Lock()
    start = time.monotonic()
    with records_path.open("a") as out, ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(scrape_city, fetcher, country, city, currency): (country, city)
                   for country, city in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            country, city = futures[fut]
            try:
                rec = fut.result()
            except AccessRestricted:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as e:
                failures.append((country, city, str(e)))
                log(f"  FAILED {city}, {country}: {e}")
                continue
            with write_lock:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
            if n % 25 == 0 or n == len(todo):
                rate = n / (time.monotonic() - start)
                eta = (len(todo) - n) / rate / 60
                log(f"  {n}/{len(todo)} ({rate:.1f}/s, ~{eta:.0f} min left) last: {city}, {country}")
    return failures


# ---------------------------------------------------------------- output

def write_outputs(records_path, rankings, out_dir, cities):
    wanted = set(cities) if cities is not None else None
    records = []
    with records_path.open() as f:
        for line in f:
            r = json.loads(line)
            if wanted is None or (r["country"], r["city"]) in wanted:
                records.append(r)
    records.sort(key=lambda r: (r["country"], r["city"]))

    # Item columns in page order; the same item name can appear in two categories
    # (e.g. imported beer at a restaurant vs. in a market), so the category is part of the key.
    item_cols = {}
    for r in records:
        for it in r["items"]:
            key = f"{slug(it['category'])}__{slug(it['item'])}"
            item_cols.setdefault(key, it)

    header = META_COLUMNS + [col for _, col in INDEX_COLUMNS]
    for key in item_cols:
        header += [key, f"{key}__min", f"{key}__max"]

    csv_path = out_dir / "numbeo_cost_of_living.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = {k: r.get(k) for k in META_COLUMNS}
            row.update(rankings.get(r["url"], {}))
            for it in r["items"]:
                key = f"{slug(it['category'])}__{slug(it['item'])}"
                row[key], row[f"{key}__min"], row[f"{key}__max"] = it["avg"], it["min"], it["max"]
            w.writerow(row)

    dict_path = out_dir / "numbeo_columns.csv"
    with dict_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["column", "category", "item", "statistic", "unit"])
        for col in META_COLUMNS:
            w.writerow([col, "", "", "", "USD" if col.endswith("_usd") else ""])
        for label, col in INDEX_COLUMNS:
            w.writerow([col, "Rankings (current)", label, "index (NYC = 100)", ""])
        for key, it in item_cols.items():
            unit = "%" if "%" in it["item"] else "USD"
            for suffix, stat in (("", "average"), ("__min", "range low"), ("__max", "range high")):
                w.writerow([key + suffix, it["category"], it["item"], stat, unit])

    log(f"Wrote {len(records)} cities x {len(header)} columns to {csv_path}")
    log(f"Wrote column dictionary to {dict_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=Path(__file__).parent / "data", type=Path)
    ap.add_argument("--country", action="append", help="only scrape this country (repeatable)")
    ap.add_argument("--limit", type=int, help="only scrape the first N cities")
    ap.add_argument("--currency", default="USD", help="display currency for prices (default USD)")
    ap.add_argument("--workers", type=int, default=3, help="parallel requests (default 3)")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="minimum seconds between any two requests (default 0.5)")
    ap.add_argument("--refresh-cities", action="store_true", help="re-download the city lists")
    ap.add_argument("--csv-only", action="store_true", help="skip scraping, rebuild CSV from cache")
    args = ap.parse_args()

    out_dir = args.out_dir
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    records_path = cache_dir / f"cities_{args.currency}.jsonl"
    fetcher = Fetcher(args.delay)

    restricted = False
    cities = None
    if not args.csv_only:
        try:
            cities = load_city_list(fetcher, cache_dir, args.country, args.refresh_cities)
            if args.limit:
                cities = cities[: args.limit]
            failures = scrape_cities(fetcher, cities, records_path, args.currency, args.workers)
            if failures:
                log(f"{len(failures)} cities failed; run the script again to retry them.")
        except AccessRestricted as e:
            restricted = True
            log(f"Numbeo says the monthly page limit for free users is reached ({e}).")
            log("Stopping. Everything fetched so far is cached; the limit resets on the 1st of the month.")
        if not args.country and not args.limit:
            cities = None  # full run: write everything in the cache

    rankings_path = cache_dir / "rankings_current.html"
    if not restricted and (not rankings_path.exists() or not args.csv_only):
        try:
            rankings_path.write_text(fetcher.get(RANKINGS_URL).text)
        except AccessRestricted:
            restricted = True
    rankings = parse_rankings(rankings_path.read_text()) if rankings_path.exists() else {}

    if records_path.exists():
        write_outputs(records_path, rankings, out_dir, cities)
    else:
        log("No cities cached yet, so no CSV was written.")
    if restricted:
        sys.exit(2)


if __name__ == "__main__":
    main()
