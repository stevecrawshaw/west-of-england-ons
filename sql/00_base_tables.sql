-- Base tables for ons_explore, plus table and column comments.
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
