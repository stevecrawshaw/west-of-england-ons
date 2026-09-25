-- Views shaped for the Huwise (Opendatasoft) portal, which caps datasets at
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
