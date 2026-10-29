"""Refresh the ons_explore MotherDuck database from ONS Explore Local Statistics.

Do not edit flight/ons_explore_refresh/main.py directly: it is generated from this
template and the sql/ directory by scripts/build_flight.py.

Sources (fixed URLs, overwritten in place by ONS):
- all-datasets.csv   long-format observations for every indicator -> fact_ons_data
- all-datasets.xlsx  one sheet per indicator with description, unit and source
                     (absent from the CSV)                         -> dim_ons_data

Config (environment variables):
- FORCE=1         reload even if the ONS files have not changed
- LOCAL_DB        path to a local DuckDB file named ons_explore.duckdb (testing)
- SOURCE_DIR      directory holding all-datasets.csv and all-datasets.xlsx,
                  used instead of downloading (testing)
"""

import csv
import gzip
import io
import os
import re
import shutil
import sys
from pathlib import Path

import duckdb
import openpyxl
import requests

BASE_URL = "https://www.ons.gov.uk/explore-local-statistics/files"
CSV_URL = f"{BASE_URL}/all-datasets.csv"
XLSX_URL = f"{BASE_URL}/all-datasets.xlsx"
HEADERS = {"User-Agent": "Mozilla/5.0 (ons_explore_refresh flight)"}
WORK_DIR = Path("/tmp/ons_explore_refresh")

EXPECTED_COLUMNS = [
    "indicator", "areacd", "areanm", "period", "sex", "age",
    "value", "lci_95", "uci_95", "status",
]
# Refuse to replace the tables if the new load is much smaller than the current one
MIN_ROW_RATIO = 0.9
MIN_INDICATOR_RATIO = 0.9
ENGLISH_LA_PREFIXES = ("E06", "E07", "E08", "E09")

# --- SQL, inlined from sql/ by scripts/build_flight.py -----------------------
SQL_BASE_TABLES = r"""-- Base tables for ons_explore, plus table and column comments.
-- Idempotent: safe to re-run. Generated from the live database; edit freely.
-- The viz columns on dim_ons_data are populated by 01_dim_viz_metadata.sql.

CREATE TABLE IF NOT EXISTS ons_explore.main.fact_ons_data(indicator VARCHAR, areacd VARCHAR, areanm VARCHAR, period DATE, sex VARCHAR, age VARCHAR, "value" DOUBLE, lci_95 VARCHAR, uci_95 VARCHAR, status VARCHAR);
CREATE TABLE IF NOT EXISTS ons_explore.main.dim_ons_data(sheet BIGINT, indicator VARCHAR, description VARCHAR, unit VARCHAR, "source" VARCHAR, category VARCHAR, chart_type VARCHAR, period_type VARCHAR, is_category_default BOOLEAN);
CREATE TABLE IF NOT EXISTS ons_explore.main.etl_load_log(loaded_at TIMESTAMP, csv_last_modified VARCHAR, xlsx_last_modified VARCHAR, fact_rows BIGINT, n_indicators INTEGER, n_dim_indicators INTEGER, unmapped_indicators VARCHAR);

COMMENT ON TABLE ons_explore.main.fact_ons_data IS 'Long-format fact table of ONS local statistics: one row per indicator, area, time period, and demographic breakdown (sex/age). Covers all English and UK local/regional areas, 1991-2026. Join to dim_ons_data on indicator for the indicator description, unit, and source.';
COMMENT ON TABLE ons_explore.main.dim_ons_data IS 'Metadata dimension table: one row per ONS indicator (sourced from the "all-datasets_meta.xlsx" workbook, one sheet per indicator). Describes what each indicator in fact_ons_data measures, its unit, and its ONS source citation. Join to fact_ons_data on indicator.';
COMMENT ON TABLE ons_explore.main.etl_load_log IS 'One row per successful refresh by the ons_explore_refresh flight: source file timestamps, row counts and any indicators with no category mapping.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.sheet IS 'Sequential ID of the source Excel worksheet (all-datasets_meta.xlsx) the indicator was read from. Not a stable business key -- use indicator to join to fact_ons_data.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.indicator IS 'Indicator name, e.g. "Total population", "Median age". Matches fact_ons_data.indicator; the join key between the two tables.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.description IS 'One-sentence plain-English definition of what the indicator measures, taken from the metadata sheet header.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.unit IS 'Unit of measurement for fact_ons_data.value for this indicator, e.g. Persons, %, Years.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.source IS 'ONS source citation for the indicator, including publication date, as given in the metadata sheet.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.category IS 'Thematic group used to organise indicators into dashboard cards: Population and economy, Education and skills, Health and wellbeing, Place and environment, Culture and leisure.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.chart_type IS 'Chart used to present the indicator: time_series (line with comparator band, >1 period), single_period (one period; position within the range of authorities), pyramid (Population by age and sex: bars by sex and age band). Derived from the West of England data.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.period_type IS 'How to read fact_ons_data.period, which is the START date of the reference period: financial_year (1 Apr, label 2023/24), academic_year (1 Aug/1 Sep, label 2023/24), mid_year (30 Jun estimate, label Mid-2023), calendar_year (label 2023), month (label Jan 2023), quarter (label Q1 2023), date (one-off snapshot, label May 2024).';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.is_category_default IS 'TRUE for the one indicator per category shown by default when a dashboard card first loads.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.indicator IS 'Indicator name, matches dim_ons_data.indicator (join key). See dim_ons_data.description/unit for what this indicator measures and how to interpret value.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.areacd IS 'ONS GSS area code (e.g. E06000001 = Hartlepool). Prefix identifies geography type: E06/E07/E08/E09 = English unitary/district/metropolitan/London borough authorities; other prefixes cover higher-level or UK-wide geographies.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.areanm IS 'Human-readable ONS area name corresponding to areacd, e.g. "Bristol, City of".';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.period IS 'Reference date for the observation. Annual indicators use a single date per year (typically 30 June, ONS mid-year convention); monthly indicators use one date per month.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.sex IS 'Sex breakdown for indicators disaggregated by sex: Male, Female, or All. Blank/NULL where the indicator has no sex breakdown.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.age IS 'Age band for indicators disaggregated by age (e.g. "30 to 34", "85+"). Blank/NULL where the indicator has no age breakdown.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.value IS 'The indicator value for this area/period/sex/age combination, in the unit given by dim_ons_data.unit for the matching indicator.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.lci_95 IS 'Lower bound of the 95% confidence interval around value, where the indicator is a modelled/sampled estimate. Blank where not applicable or not published.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.uci_95 IS 'Upper bound of the 95% confidence interval around value, where the indicator is a modelled/sampled estimate. Blank where not applicable or not published.';
COMMENT ON COLUMN ons_explore.main.fact_ons_data.status IS 'ONS data-quality/availability flag for the row, e.g. u = unavailable, z = not applicable, c = confidential, x = not available, or free-text notes such as "Value for Cornwall and Isles of Scilly combined" where areas are merged in the source data. Blank means no caveat.';
"""
SQL_DIM_VIZ = r"""-- Add data-viz decision columns to dim_ons_data.
-- Idempotent: safe to re-run after the fact table is refreshed.
-- Run with: motherduck query --file sql/01_dim_viz_metadata.sql

ALTER TABLE ons_explore.main.dim_ons_data ADD COLUMN IF NOT EXISTS chart_type VARCHAR;
ALTER TABLE ons_explore.main.dim_ons_data ADD COLUMN IF NOT EXISTS period_type VARCHAR;
ALTER TABLE ons_explore.main.dim_ons_data ADD COLUMN IF NOT EXISTS is_category_default BOOLEAN;

-- Profile each indicator's periods for the four West of England authorities,
-- which are the only areas exported.
CREATE OR REPLACE TEMP TABLE indicator_periods AS
SELECT
    indicator,
    count(DISTINCT period) AS n_periods,
    count(DISTINCT year(period)) AS n_years,
    list(DISTINCT strftime(period, '%m-%d')) AS month_days
FROM ons_explore.main.fact_ons_data
WHERE areacd IN ('E06000022', 'E06000023', 'E06000024', 'E06000025')
  AND value IS NOT NULL
GROUP BY indicator;

UPDATE ons_explore.main.dim_ons_data AS d
SET
    chart_type = CASE
        WHEN d.indicator = 'Population by age and sex' THEN 'pyramid'
        WHEN p.n_periods = 1 THEN 'single_period'
        ELSE 'time_series'
    END,
    period_type = CASE
        -- Several periods within a year: sub-annual series
        -- (partial first/last years mean monthly series average < 12 a year)
        WHEN p.n_periods > p.n_years AND p.n_periods / p.n_years > 4 THEN 'month'
        WHEN p.n_periods > p.n_years THEN 'quarter'
        WHEN p.month_days = ['06-30'] THEN 'mid_year'
        WHEN p.month_days = ['04-01'] THEN 'financial_year'
        WHEN p.month_days IN (['08-01'], ['09-01']) THEN 'academic_year'
        -- A one-off snapshot on an arbitrary day is labelled by month
        WHEN p.n_periods = 1 AND p.month_days <> ['01-01'] THEN 'date'
        ELSE 'calendar_year'
    END,
    is_category_default = d.indicator IN (
        'Total population',
        'GCSEs in English and Maths',
        'Female healthy life expectancy',
        'Average house price',
        'Engaged with the arts'
    )
FROM indicator_periods AS p
WHERE p.indicator = d.indicator;

COMMENT ON COLUMN ons_explore.main.dim_ons_data.category IS
    'Thematic group used to organise indicators into dashboard cards: Population and economy, Education and skills, Health and wellbeing, Place and environment, Culture and leisure.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.chart_type IS
    'Chart used to present the indicator: time_series (line with comparator band, >1 period), single_period (one period; position within the range of authorities), pyramid (Population by age and sex: bars by sex and age band). Derived from the West of England data.';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.period_type IS
    'How to read fact_ons_data.period, which is the START date of the reference period: financial_year (1 Apr, label 2023/24), academic_year (1 Aug/1 Sep, label 2023/24), mid_year (30 Jun estimate, label Mid-2023), calendar_year (label 2023), month (label Jan 2023), quarter (label Q1 2023), date (one-off snapshot, label May 2024).';
COMMENT ON COLUMN ons_explore.main.dim_ons_data.is_category_default IS
    'TRUE for the one indicator per category shown by default when a dashboard card first loads.';
"""
SQL_EXPORT_VIEWS = r"""-- Views shaped for the Huwise (Opendatasoft) portal, which caps datasets at
-- 500k records and cannot join or run SQL. Only the four West of England
-- authorities are exported; every other English authority is summarised into
-- percentile band columns on each row.
-- Run with: motherduck query --file sql/02_export_views.sql

CREATE OR REPLACE VIEW ons_explore.main.export_ons_values AS
WITH la AS (
    -- English LAs (unitary, district, metropolitan, London borough), excluding
    -- the City of London, whose tiny resident population makes it an outlier
    SELECT indicator, areacd, areanm, period, sex, age, value, lci_95, uci_95, status
    FROM ons_explore.main.fact_ons_data
    WHERE substr(areacd, 1, 3) IN ('E06', 'E07', 'E08', 'E09')
      AND areacd <> 'E09000001'
      AND value IS NOT NULL
),
ranked AS (
    SELECT
        *,
        rank() OVER (w ORDER BY value DESC) AS rank,
        count(*) OVER w AS n_las
    FROM la
    WINDOW w AS (PARTITION BY indicator, period, sex, age)
),
band AS (
    SELECT
        indicator, period, sex, age,
        round(quantile_cont(value, 0.1), 6) AS other_p10,
        round(median(value), 6) AS other_median,
        round(quantile_cont(value, 0.9), 6) AS other_p90,
        min(value) AS other_min,
        max(value) AS other_max,
        count(*) AS n_other_las
    FROM la
    WHERE areacd NOT IN ('E06000022', 'E06000023', 'E06000024', 'E06000025')
    GROUP BY ALL
),
england AS (
    SELECT indicator, period, sex, age, value AS england_value
    FROM ons_explore.main.fact_ons_data
    WHERE areacd = 'E92000001'
)
SELECT
    r.indicator,
    d.category,
    d.chart_type,
    r.areacd,
    r.areanm,
    r.period,
    CASE d.period_type
        WHEN 'financial_year' THEN
            format('{}/{:02d}', year(r.period), (year(r.period) + 1) % 100)
        WHEN 'academic_year' THEN
            format('{}/{:02d}', year(r.period), (year(r.period) + 1) % 100)
        WHEN 'mid_year' THEN format('Mid-{}', year(r.period))
        WHEN 'month' THEN strftime(r.period, '%b %Y')
        WHEN 'quarter' THEN format('Q{} {}', quarter(r.period), year(r.period))
        WHEN 'date' THEN strftime(r.period, '%b %Y')
        ELSE year(r.period)::VARCHAR
    END AS period_label,
    r.sex,
    r.age,
    -- Numeric lower bound so age bands sort correctly ('5 to 9' before '10 to 14')
    TRY_CAST(regexp_extract(r.age, '^(\d+)', 1) AS INTEGER) AS age_min,
    r.value,
    CASE WHEN d.chart_type = 'pyramid' AND r.sex = 'Male' THEN -r.value ELSE r.value END
        AS value_signed,
    TRY_CAST(r.lci_95 AS DOUBLE) AS lci_95,
    TRY_CAST(r.uci_95 AS DOUBLE) AS uci_95,
    r.status,
    d.unit,
    e.england_value,
    CASE WHEN d.chart_type = 'pyramid' AND r.sex = 'Male' THEN -e.england_value ELSE e.england_value END
        AS england_value_signed,
    b.other_p10,
    b.other_median,
    b.other_p90,
    b.other_min,
    b.other_max,
    b.n_other_las,
    r.rank,
    r.n_las
FROM ranked AS r
JOIN ons_explore.main.dim_ons_data AS d USING (indicator)
LEFT JOIN band AS b
    ON b.indicator = r.indicator
   AND b.period = r.period
   AND b.sex IS NOT DISTINCT FROM r.sex
   AND b.age IS NOT DISTINCT FROM r.age
LEFT JOIN england AS e
    ON e.indicator = r.indicator
   AND e.period = r.period
   AND e.sex IS NOT DISTINCT FROM r.sex
   AND e.age IS NOT DISTINCT FROM r.age
WHERE r.areacd IN ('E06000022', 'E06000023', 'E06000024', 'E06000025');

COMMENT ON VIEW ons_explore.main.export_ons_values IS
    'Portal export of ONS local statistics for the four West of England authorities (B&NES, Bristol, North Somerset, South Gloucestershire): one row per authority, indicator, period, sex and age. Each row carries the England value and a percentile band summarising all other English local authorities (City of London excluded) for the same indicator/period/sex/age. Band, England and n_* columns repeat across the four authorities: aggregate them with MIN, MAX or AVG, never SUM.';

COMMENT ON COLUMN ons_explore.main.export_ons_values.indicator IS 'Indicator name; joins to export_ons_indicators.indicator.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.category IS 'Thematic group (dashboard card) the indicator belongs to.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.chart_type IS 'time_series, single_period or pyramid; see export_ons_indicators.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.areacd IS 'ONS GSS code of the West of England authority.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.areanm IS 'Name of the West of England authority.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.period IS 'Start date of the reference period (ONS convention). Use period_label for display.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.period_label IS 'Display label for the period, e.g. 2023/24 (financial or academic year), Mid-2023, 2023, Jan 2023, Q1 2026.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.sex IS 'Male, Female or All for Population by age and sex; NULL for indicators without a sex breakdown.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.age IS 'Five-year age band (e.g. "30 to 34", "85+") for Population by age and sex; NULL otherwise.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.age_min IS 'Lower bound of the age band in years; sort by this, not by age, to order bands correctly.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.value IS 'Indicator value for the authority, in the unit given by unit.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.value_signed IS 'value, negated for Male rows of Population by age and sex so a bar chart draws a population pyramid; equal to value everywhere else.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.lci_95 IS 'Lower 95% confidence limit where the value is a sampled or modelled estimate; NULL otherwise.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.uci_95 IS 'Upper 95% confidence limit where the value is a sampled or modelled estimate; NULL otherwise.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.status IS 'ONS quality/availability flag or note for the row; NULL means no caveat.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.unit IS 'Unit of value and all comparator columns. £ is displayed as a prefix (£1,234); other units as a suffix.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.england_value IS 'Value for England (E92000001) for the same indicator/period/sex/age, where published. Repeats across authorities.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.england_value_signed IS 'england_value, negated for Male rows of Population by age and sex, to draw the England outline on a population pyramid; equal to england_value everywhere else.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.other_p10 IS '10th percentile across all other English local authorities (excluding the four West of England authorities and City of London). Lower edge of the comparator band.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.other_median IS 'Median across all other English local authorities.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.other_p90 IS '90th percentile across all other English local authorities. Upper edge of the comparator band.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.other_min IS 'Lowest value among all other English local authorities; often an outlier, so not used for the band.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.other_max IS 'Highest value among all other English local authorities; often an outlier, so not used for the band.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.n_other_las IS 'Number of other English local authorities with a value, i.e. the population behind the band.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.rank IS 'Rank of this authority among all English local authorities with a value (including the West of England four); 1 = highest value. Says nothing about whether high is good.';
COMMENT ON COLUMN ons_explore.main.export_ons_values.n_las IS 'Number of English local authorities ranked, e.g. rank 12 of n_las 296.';

CREATE OR REPLACE VIEW ons_explore.main.export_ons_indicators AS
SELECT
    sheet AS sort_order,
    indicator,
    category,
    description,
    unit,
    source,
    chart_type,
    period_type,
    CASE period_type WHEN 'month' THEN 'month' WHEN 'quarter' THEN 'month' ELSE 'year' END
        AS timescale,
    is_category_default
FROM ons_explore.main.dim_ons_data
WHERE indicator IN (SELECT DISTINCT indicator FROM ons_explore.main.export_ons_values);

COMMENT ON VIEW ons_explore.main.export_ons_indicators IS
    'Portal export of indicator metadata: one row per indicator present in export_ons_values, with its definition, unit, source and the chart decisions used to present it.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.sort_order IS 'Order of the indicator within its category dropdown (source worksheet number).';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.indicator IS 'Indicator name; joins to export_ons_values.indicator.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.category IS 'Thematic group (dashboard card) the indicator belongs to.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.description IS 'Plain-English definition of what the indicator measures.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.unit IS 'Unit of measurement. £ is displayed as a prefix; other units as a suffix.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.source IS 'ONS source citation including publication date.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.chart_type IS 'time_series: line per authority over the p10-p90 band of other authorities; single_period: each authority placed on the band with its rank; pyramid: bars of value_signed by age band and sex.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.period_type IS 'How the period dates are to be read: financial_year, academic_year, mid_year, calendar_year, month, quarter or date.';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.timescale IS 'Value for the ods-chart-query timescale attribute (year or month).';
COMMENT ON COLUMN ons_explore.main.export_ons_indicators.is_category_default IS 'TRUE for the indicator shown when its category card first loads.';
"""
INDICATOR_CATEGORIES_CSV = r"""indicator,category
"Total population",Population and economy
"Population by age and sex",Population and economy
"Population density",Population and economy
"Five-year population change",Population and economy
"Median age",Population and economy
"Population aged 0 to 15",Population and economy
"Population aged 16 to 64",Population and economy
"Population aged 65 and over",Population and economy
"Economic inactivity rate (Great Britain)",Population and economy
"Employment rate (Great Britain)",Population and economy
"Modelled unemployment rate",Population and economy
"Claimant Count",Population and economy
"Job density",Population and economy
"Gross disposable household income per head",Population and economy
"Gross median weekly pay",Population and economy
"Children in relative poverty before housing costs",Place and environment
"Children in relative poverty after housing costs",Place and environment
"Fuel poverty (England)",Place and environment
"Gross value added per hour worked",Population and economy
"Gross value added per job filled",Population and economy
"Gross domestic product per head at current market prices",Population and economy
"Gross domestic product per head in chained volume measures",Population and economy
"Active businesses",Population and economy
"Business births",Population and economy
"Business deaths",Population and economy
"High growth businesses",Population and economy
"One-year business survival rate",Population and economy
"Two-year business survival rate",Population and economy
"Three-year business survival rate",Population and economy
"Four-year business survival rate",Population and economy
"Five-year business survival rate",Population and economy
"Food outlets",Place and environment
"Net additions to the housing stock (England)",Place and environment
"Average house price",Place and environment
"Housing affordability ratio (residence-based)",Place and environment
"First-time buyer mortgage sales",Place and environment
"Average length of short-term let stay",Place and environment
"Number of guest nights at a short-term let",Place and environment
"Further education and skills learner achievements",Education and skills
"Further education and skills participation",Education and skills
"GCSEs in English and Maths",Education and skills
"Level 3 or above qualifications (Great Britain)",Education and skills
"Pupils at the end of key stage 2 meeting the expected standard in reading, writing and maths (combined)",Education and skills
"No qualifications",Education and skills
"Communication and language skills by end of early years foundation stage",Education and skills
"Literacy skills by end of early years foundation stage",Education and skills
"Maths skills by end of early years foundation stage",Education and skills
"Persistent absences for all pupils",Education and skills
"Persistent absences for pupils eligible for free school meals",Education and skills
"Persistent absences for pupils looked after by local authorities",Education and skills
"Apprenticeship achievements (England)",Education and skills
"Apprenticeship starts (England)",Education and skills
"Cigarette smokers",Health and wellbeing
"Healthy weight prevalence in children at reception age",Health and wellbeing
"Healthy weight prevalence in children at Year 6 age",Health and wellbeing
"Obesity prevalence in children at reception age",Health and wellbeing
"Obesity prevalence in children at Year 6 age",Health and wellbeing
"Overweight prevalence in children at reception age",Health and wellbeing
"Overweight prevalence in children at Year 6 age",Health and wellbeing
"Underweight prevalence in children at reception age",Health and wellbeing
"Underweight prevalence in children at Year 6 age",Health and wellbeing
"Adult obesity prevalence",Health and wellbeing
"Cancer diagnosis at stage 1 and 2",Health and wellbeing
"Preventable cardiovascular mortality (England)",Health and wellbeing
"Female healthy life expectancy",Health and wellbeing
"Male healthy life expectancy",Health and wellbeing
"Anxiety",Health and wellbeing
"Feeling life is worthwhile",Health and wellbeing
"Happiness",Health and wellbeing
"Life satisfaction",Health and wellbeing
"Domestic electricity consumption",Place and environment
"Domestic gas consumption",Place and environment
"Greenhouse gas emissions",Place and environment
"Air pollution regulating",Place and environment
"Greenhouse gas regulating",Place and environment
"Urban heat regulating",Place and environment
"Gigabit capable broadband",Place and environment
"4G coverage",Place and environment
"5G coverage",Place and environment
"Premises below 30Mbps",Place and environment
"Motor vehicle flow",Place and environment
"Public electric vehicle chargers",Place and environment
"Travel time to employment centre by bicycle",Place and environment
"Travel time to employment centre by car",Place and environment
"Travel time to employment centre by public transport and walking",Place and environment
"Supermarkets",Place and environment
"Sports facilities",Culture and leisure
"Museums",Culture and leisure
"Residents within a 30 minute walk of their nearest library",Place and environment
"Residents within a 30 minute walk of their nearest railway station",Place and environment
"Engaged with the arts",Culture and leisure
"Visited a heritage site",Culture and leisure
"Visited a museum or gallery",Culture and leisure
"Visited a public library",Culture and leisure
"Superfast broadband",Place and environment
"Ultrafast broadband",Place and environment
"Premises with access to full fibre",Place and environment
"Male life expectancy",Health and wellbeing
"Female life expectancy",Health and wellbeing
"""
# -----------------------------------------------------------------------------


def log(message: str) -> None:
    print(message, flush=True)


def split_statements(script: str) -> list[str]:
    """Split a SQL script on semicolons, ignoring those in strings and comments."""
    statements: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    while i < len(script):
        ch = script[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                if script[i + 1 : i + 2] == "'":  # escaped quote
                    buf.append("'")
                    i += 1
                else:
                    in_string = False
        elif ch == "'":
            in_string = True
            buf.append(ch)
        elif script.startswith("--", i):
            while i < len(script) and script[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_script(con: duckdb.DuckDBPyConnection, script: str) -> None:
    for statement in split_statements(script):
        con.execute(statement)


def connect() -> duckdb.DuckDBPyConnection:
    local_db = os.environ.get("LOCAL_DB")
    if local_db:
        return duckdb.connect(local_db)
    con = duckdb.connect("md:")
    con.execute("CREATE DATABASE IF NOT EXISTS ons_explore")
    con.close()
    return duckdb.connect("md:ons_explore?attach_mode=single")


def remote_stamp(url: str) -> str:
    """Last-Modified (falling back to ETag) of a source file; '' if neither is sent."""
    response = requests.head(url, headers=HEADERS, timeout=60, allow_redirects=True)
    response.raise_for_status()
    return response.headers.get("Last-Modified") or response.headers.get("ETag") or ""


def download(url: str, dest: Path) -> None:
    log(f"Downloading {url}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        with dest.open("wb") as f:
            shutil.copyfileobj(response.raw, f, length=1 << 20)
    # ONS labels the CSV application/gzip; guard against it ever being gzipped.
    with dest.open("rb") as f:
        magic = f.read(2)
    if magic == b"\x1f\x8b":
        plain = dest.with_suffix(dest.suffix + ".plain")
        with gzip.open(dest, "rb") as src, plain.open("wb") as out:
            shutil.copyfileobj(src, out, length=1 << 20)
        plain.replace(dest)
    log(f"  {dest.stat().st_size / 1e6:.1f} MB")


def fetch_sources() -> tuple[Path, Path]:
    source_dir = os.environ.get("SOURCE_DIR")
    if source_dir:
        return Path(source_dir) / "all-datasets.csv", Path(source_dir) / "all-datasets.xlsx"
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    WORK_DIR.mkdir(parents=True)
    csv_path, xlsx_path = WORK_DIR / "all-datasets.csv", WORK_DIR / "all-datasets.xlsx"
    download(CSV_URL, csv_path)
    download(XLSX_URL, xlsx_path)
    return csv_path, xlsx_path


def read_indicator_metadata(xlsx_path: Path) -> list[dict]:
    """Parse title, description, source and unit from each numbered indicator sheet.

    Each sheet starts with: A1 "<Indicator> [note n]", A2 description, a
    "Source: ..." row, then a header row beginning "Area code" whose period
    columns end in the unit, e.g. "2023-06-30 (Persons)".
    """
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    rows = []
    for name in wb.sheetnames:
        if not re.fullmatch(r"\d+", name):
            continue
        head = list(wb[name].iter_rows(min_row=1, max_row=10, values_only=True))
        first_col = [r[0] if r else None for r in head]
        indicator = re.sub(r"\s*\[note\s*\d+\]$", "", str(first_col[0]))
        sources = [str(c) for c in first_col if isinstance(c, str) and c.startswith("Source")]
        unit = None
        for i, cell in enumerate(first_col):
            if cell == "Area code":
                for header in head[i]:
                    match = re.search(r"\(([^)]+)\)$", str(header or ""))
                    if match:
                        unit = match.group(1)
                        break
                break
        rows.append(
            {
                "sheet": int(name),
                "indicator": indicator,
                "description": first_col[1],
                "unit": unit,
                "source": " | ".join(sources),
            }
        )
    wb.close()
    return rows


def validate_csv(con: duckdb.DuckDBPyConnection, csv_path: Path) -> tuple[int, int]:
    with csv_path.open(encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    if header != EXPECTED_COLUMNS:
        sys.exit(f"Unexpected CSV columns: {header}")
    rows, indicators = con.execute(
        "SELECT count(*), count(DISTINCT indicator) FROM stage_fact"
    ).fetchone()
    return rows, indicators


def main() -> None:
    force = os.environ.get("FORCE") == "1"
    con = connect()
    run_script(con, SQL_BASE_TABLES)

    if not os.environ.get("SOURCE_DIR"):
        csv_stamp, xlsx_stamp = remote_stamp(CSV_URL), remote_stamp(XLSX_URL)
        last = con.execute(
            "SELECT csv_last_modified, xlsx_last_modified FROM ons_explore.main.etl_load_log "
            "ORDER BY loaded_at DESC LIMIT 1"
        ).fetchone()
        if not force and last == (csv_stamp, xlsx_stamp) and csv_stamp and xlsx_stamp:
            log(f"ONS files unchanged since last load ({csv_stamp}); nothing to do.")
            return
    else:
        csv_stamp = xlsx_stamp = "local"

    csv_path, xlsx_path = fetch_sources()

    # Stage and validate before touching the live tables.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE stage_fact AS
        SELECT * FROM read_csv(
            '{csv_path}', header = true,
            columns = {{
                'indicator': 'VARCHAR', 'areacd': 'VARCHAR', 'areanm': 'VARCHAR',
                'period': 'DATE', 'sex': 'VARCHAR', 'age': 'VARCHAR',
                'value': 'DOUBLE', 'lci_95': 'VARCHAR', 'uci_95': 'VARCHAR',
                'status': 'VARCHAR'
            }}
        )
        """
    )
    new_rows, new_indicators = validate_csv(con, csv_path)
    old_rows, old_indicators = con.execute(
        "SELECT count(*), count(DISTINCT indicator) FROM ons_explore.main.fact_ons_data"
    ).fetchone()
    log(f"Staged {new_rows:,} rows / {new_indicators} indicators "
        f"(current: {old_rows:,} / {old_indicators})")
    if old_rows and (
        new_rows < MIN_ROW_RATIO * old_rows
        or new_indicators < MIN_INDICATOR_RATIO * old_indicators
    ):
        sys.exit("New load is much smaller than the current tables; aborting.")

    # Dimension: metadata for indicators with English local authority data.
    la_filter = " OR ".join(f"areacd LIKE '{p}%'" for p in ENGLISH_LA_PREFIXES)
    english = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT indicator FROM stage_fact WHERE {la_filter}"
        ).fetchall()
    }
    meta = [m for m in read_indicator_metadata(xlsx_path) if m["indicator"] in english]
    categories = dict(csv.reader(io.StringIO(INDICATOR_CATEGORIES_CSV)))
    for m in meta:
        m["category"] = categories.get(m["indicator"])
    missing_meta = english - {m["indicator"] for m in meta}
    if missing_meta:
        sys.exit(f"No metadata sheet for indicators with LA data: {sorted(missing_meta)}")
    unmapped = sorted(m["indicator"] for m in meta if m["category"] is None)
    if unmapped:
        log(f"WARNING: no category for {unmapped}; add them to sql/indicator_categories.csv "
            "and rebuild the flight. They are loaded but will not appear in category cards.")

    dim_csv = (WORK_DIR if not os.environ.get("SOURCE_DIR") else Path("/tmp")) / "dim_stage.csv"
    dim_csv.parent.mkdir(parents=True, exist_ok=True)
    with dim_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, ["sheet", "indicator", "description", "unit", "source", "category"]
        )
        writer.writeheader()
        writer.writerows(meta)

    # Swap data in one transaction; views are dropped first because DuckDB
    # blocks ALTER/COMMENT on tables that views depend on.
    con.execute("DROP VIEW IF EXISTS ons_explore.main.export_ons_values")
    con.execute("DROP VIEW IF EXISTS ons_explore.main.export_ons_indicators")
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM ons_explore.main.fact_ons_data")
        con.execute("INSERT INTO ons_explore.main.fact_ons_data SELECT * FROM stage_fact")
        con.execute("DELETE FROM ons_explore.main.dim_ons_data")
        con.execute(
            f"""
            INSERT INTO ons_explore.main.dim_ons_data (sheet, indicator, description, unit, source, category)
            SELECT sheet, indicator, description, unit, source, category
            FROM read_csv('{dim_csv}', header = true, all_varchar = false,
                          types = {{'sheet': 'BIGINT'}})
            """
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    run_script(con, SQL_DIM_VIZ)
    run_script(con, SQL_EXPORT_VIEWS)

    # Every category must still have its default indicator.
    defaults = con.execute(
        "SELECT count(*) FILTER (WHERE is_category_default), "
        "count(DISTINCT category) FILTER (WHERE category IS NOT NULL) "
        "FROM ons_explore.main.dim_ons_data"
    ).fetchone()
    if defaults[0] != defaults[1]:
        sys.exit(
            f"{defaults[0]} default indicators for {defaults[1]} categories: "
            "an indicator named in sql/01_dim_viz_metadata.sql has been renamed or removed."
        )

    con.execute(
        "INSERT INTO ons_explore.main.etl_load_log VALUES "
        "(current_timestamp::TIMESTAMP, ?, ?, ?, ?, ?, ?)",
        [csv_stamp, xlsx_stamp, new_rows, new_indicators, len(meta), ", ".join(unmapped)],
    )
    log(f"Loaded {new_rows:,} fact rows, {len(meta)} dim indicators.")
    shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
