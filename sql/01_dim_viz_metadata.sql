-- Add data-viz decision columns to dim_ons_data.
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
