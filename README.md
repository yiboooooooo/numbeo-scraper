# Numbeo cost-of-living data

City-level cost-of-living prices from [Numbeo](https://www.numbeo.com/cost-of-living/), one row per city.

## The dataset

[`data/kaggle_cost_of_living_2022.csv`](data/kaggle_cost_of_living_2022.csv): 4,956 cities in 215 countries, 55 price items each (USD; the mortgage rate is in %). [`data/kaggle_columns_2022.csv`](data/kaggle_columns_2022.csv) gives each column's category, full item name and unit.

It comes from the Kaggle dataset [Global Cost of Living](https://www.kaggle.com/datasets/mvieira101/global-cost-of-living) by Miguel Piedade (CC0), a December 2022 scrape of Numbeo, with its `x1`–`x55` columns renamed to readable `category__item` names.

Things to know:

- **December 2022 prices.** Fine for comparing cities, not for current prices.
- **`data_quality`** is 1 for the 923 cities Numbeo considered reliable and 0 for the rest. 50 cities have no prices at all.
- **Smaller cities have gaps** (blank cells), e.g. tennis court rental is missing for 2,400 cities.
- **Averages only**, no min/max ranges.
- **Item wording is Numbeo's from 2022.** A few items have changed since, e.g. the mobile item is the price of one minute of prepaid calls, not a monthly plan.

To rebuild it from the source:

```bash
mkdir -p kaggle_raw && curl -L -o kaggle_raw/global-cost-of-living.zip https://www.kaggle.com/api/v1/datasets/download/mvieira101/global-cost-of-living
unzip -o kaggle_raw/global-cost-of-living.zip -d kaggle_raw/extracted
python3 -I convert_kaggle.py kaggle_raw/extracted/cost-of-living_v2.csv
```

## Live scraper

`scrape_numbeo.py` scrapes current data straight from Numbeo. It finds every city through the "Select city" dropdown on each country page and collects, per city:

- all 55 items with the average and the min/max range, in USD
- the estimated monthly costs for a family of four and a single person
- entry and contributor counts, and the last update date
- the six cost-of-living indices for ranked cities

The output is a CSV with one row per city. The scraper is resumable and stops cleanly when Numbeo blocks it.

**Limit:** Numbeo's free tier allows only about 10 city pages per month (it resets on the 1st), so a full run of about 10,000 cities needs paid access (Numbeo Premium or its API). It's still handy for a few specific cities:

```bash
pip install -r requirements.txt
python3 scrape_numbeo.py --country Canada --limit 5
```

Output goes to `data/numbeo_cost_of_living.csv` with a matching `data/numbeo_columns.csv`.
