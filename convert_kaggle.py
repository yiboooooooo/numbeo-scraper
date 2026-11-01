#!/usr/bin/env python3
"""Convert the Kaggle "Global Cost of Living" snapshot into the scraper's CSV layout.

The Kaggle dataset (mvieira101/global-cost-of-living, CC0) is a December 2022
scrape of Numbeo: 4,956 cities with 55 price items each, in USD, stored as
columns x1..x55. This script renames those columns to the category__item scheme
scrape_numbeo.py uses and writes a matching column dictionary. Kaggle only has
the average for each item, so there are no __min/__max columns, and the item
labels are Numbeo's 2022 wording (a few items have changed since, e.g. x37 was
a per-minute prepaid mobile tariff, not today's monthly phone plan).

Usage:
    python3 -I convert_kaggle.py kaggle_raw/extracted/cost-of-living_v2.csv
"""

import argparse
import csv
import re
from pathlib import Path

# x1..x55 in order, from the dataset's data card. The order matches the
# categories on a Numbeo city page.
ITEMS = [
    ("Restaurants", "Meal, Inexpensive Restaurant"),
    ("Restaurants", "Meal for 2 People, Mid-range Restaurant, Three-course"),
    ("Restaurants", "McMeal at McDonalds (or Equivalent Combo Meal)"),
    ("Restaurants", "Domestic Beer (0.5 liter draught, in restaurants)"),
    ("Restaurants", "Imported Beer (0.33 liter bottle, in restaurants)"),
    ("Restaurants", "Cappuccino (regular, in restaurants)"),
    ("Restaurants", "Coke/Pepsi (0.33 liter bottle, in restaurants)"),
    ("Restaurants", "Water (0.33 liter bottle, in restaurants)"),
    ("Markets", "Milk (regular), (1 liter)"),
    ("Markets", "Loaf of Fresh White Bread (500g)"),
    ("Markets", "Rice (white), (1kg)"),
    ("Markets", "Eggs (regular) (12)"),
    ("Markets", "Local Cheese (1kg)"),
    ("Markets", "Chicken Fillets (1kg)"),
    ("Markets", "Beef Round (1kg) (or Equivalent Back Leg Red Meat)"),
    ("Markets", "Apples (1kg)"),
    ("Markets", "Banana (1kg)"),
    ("Markets", "Oranges (1kg)"),
    ("Markets", "Tomato (1kg)"),
    ("Markets", "Potato (1kg)"),
    ("Markets", "Onion (1kg)"),
    ("Markets", "Lettuce (1 head)"),
    ("Markets", "Water (1.5 liter bottle, at the market)"),
    ("Markets", "Bottle of Wine (Mid-Range, at the market)"),
    ("Markets", "Domestic Beer (0.5 liter bottle, at the market)"),
    ("Markets", "Imported Beer (0.33 liter bottle, at the market)"),
    ("Markets", "Cigarettes 20 Pack (Marlboro)"),
    ("Transportation", "One-way Ticket (Local Transport)"),
    ("Transportation", "Monthly Pass (Regular Price)"),
    ("Transportation", "Taxi Start (Normal Tariff)"),
    ("Transportation", "Taxi 1km (Normal Tariff)"),
    ("Transportation", "Taxi 1hour Waiting (Normal Tariff)"),
    ("Transportation", "Gasoline (1 liter)"),
    ("Transportation", "Volkswagen Golf 1.4 90 KW Trendline (Or Equivalent New Car)"),
    ("Transportation", "Toyota Corolla Sedan 1.6l 97kW Comfort (Or Equivalent New Car)"),
    ("Utilities (Monthly)", "Basic (Electricity, Heating, Cooling, Water, Garbage) for 85m2 Apartment"),
    ("Utilities (Monthly)", "1 min. of Prepaid Mobile Tariff Local (No Discounts or Plans)"),
    ("Utilities (Monthly)", "Internet (60 Mbps or More, Unlimited Data, Cable/ADSL)"),
    ("Sports And Leisure", "Fitness Club, Monthly Fee for 1 Adult"),
    ("Sports And Leisure", "Tennis Court Rent (1 Hour on Weekend)"),
    ("Sports And Leisure", "Cinema, International Release, 1 Seat"),
    ("Childcare", "Preschool (or Kindergarten), Full Day, Private, Monthly for 1 Child"),
    ("Childcare", "International Primary School, Yearly for 1 Child"),
    ("Clothing And Shoes", "1 Pair of Jeans (Levis 501 Or Similar)"),
    ("Clothing And Shoes", "1 Summer Dress in a Chain Store (Zara, H&M, ...)"),
    ("Clothing And Shoes", "1 Pair of Nike Running Shoes (Mid-Range)"),
    ("Clothing And Shoes", "1 Pair of Men Leather Business Shoes"),
    ("Rent Per Month", "Apartment (1 bedroom) in City Centre"),
    ("Rent Per Month", "Apartment (1 bedroom) Outside of Centre"),
    ("Rent Per Month", "Apartment (3 bedrooms) in City Centre"),
    ("Rent Per Month", "Apartment (3 bedrooms) Outside of Centre"),
    ("Buy Apartment Price", "Price per Square Meter to Buy Apartment in City Centre"),
    ("Buy Apartment Price", "Price per Square Meter to Buy Apartment Outside of Centre"),
    ("Salaries And Financing", "Average Monthly Net Salary (After Tax)"),
    ("Salaries And Financing", "Mortgage Interest Rate in Percentages (%), Yearly, for 20 Years Fixed-Rate"),
]

DATA_QUALITY_NOTE = "1 if Numbeo considered the data reliable, 0 if it said more contributors were needed"


def slug(s):
    # Same scheme as scrape_numbeo.slug, so both CSVs name columns alike.
    return re.sub(r"[^a-z0-9]+", "_", s.lower().replace("%", "pct")).strip("_")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="cost-of-living_v2.csv from the Kaggle download")
    ap.add_argument("--out-dir", default=Path(__file__).parent / "data", type=Path)
    args = ap.parse_args()

    with args.source.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    expected = {"city", "country", "data_quality"} | {f"x{i}" for i in range(1, len(ITEMS) + 1)}
    missing = expected - set(rows[0])
    if missing:
        raise SystemExit(f"{args.source} is missing columns {sorted(missing)}; is it the v2 file?")
    rows.sort(key=lambda r: (r["country"], r["city"]))

    item_cols = [f"{slug(cat)}__{slug(label)}" for cat, label in ITEMS]
    header = ["country", "city", "data_quality"] + item_cols

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "kaggle_cost_of_living_2022.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            values = [r[f"x{i}"] for i in range(1, len(ITEMS) + 1)]
            w.writerow([r["country"], r["city"], r["data_quality"]] + ["" if v == "nan" else v for v in values])

    dict_path = args.out_dir / "kaggle_columns_2022.csv"
    with dict_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["column", "category", "item", "statistic", "unit", "kaggle_column"])
        w.writerow(["country", "", "Country name", "", "", "country"])
        w.writerow(["city", "", "City name", "", "", "city"])
        w.writerow(["data_quality", "", DATA_QUALITY_NOTE, "", "", "data_quality"])
        for i, (col, (cat, label)) in enumerate(zip(item_cols, ITEMS), 1):
            w.writerow([col, cat, label, "average", "%" if "%" in label else "USD", f"x{i}"])

    print(f"Wrote {len(rows)} cities x {len(header)} columns to {csv_path}")
    print(f"Wrote column dictionary to {dict_path}")


if __name__ == "__main__":
    main()
