# ONS Explore Local Statistics (`ons_explore`)

Local-area indicators from the ONS [Explore Local Statistics](https://www.ons.gov.uk/explore-local-statistics/) service, covering UK local authorities and higher geographies from 1991. Licence: Open Government Licence v3.0.

## Refresh

Flight `ons_explore_refresh` (https://app.motherduck.com/flights/5b4eb624-b1d7-43eb-94a1-cb1948323d87). Source code and build in the `west-of-england-ons` repo (`flight/`, `sql/`, `scripts/build_flight.py`).

- **Triggered manually** (`motherduck flight run flight/ons_explore_refresh`); scheduling is not available on the current plan. ONS updates indicators irregularly, within about three weeks of the underlying release.
- **Sources** (fixed URLs, overwritten in place by ONS):
  - `all-datasets.csv`: long-format observations, all indicators -> `fact_ons_data`
  - `all-datasets.xlsx`: one sheet per indicator with description, unit, source -> `dim_ons_data`
- Skips the load if both files' `Last-Modified` are unchanged (`FORCE=1` overrides). Aborts, leaving tables untouched, if CSV columns change or row or indicator counts fall below 90% of the current tables.
- Each load replaces `fact_ons_data` and `dim_ons_data` in a single transaction, then re-applies viz columns, comments and the export views. Loads are logged in `etl_load_log`.

## Tables and views

| Object | Grain |
|---|---|
| `fact_ons_data` | indicator x area x period x sex x age (all geographies, 1991 onwards). Join to `dim_ons_data` on `indicator`. |
| `dim_ons_data` | One row per indicator that has English local authority data. `sheet` is the workbook sheet number and is not stable between releases. |
| `export_ons_values` | Four West of England authorities only, with England value and a percentile band of other English LAs per row. Built for the Huwise portal; band columns repeat across authorities, so aggregate with MIN/MAX/AVG, never SUM. |
| `export_ons_indicators` | Indicator metadata for indicators in `export_ons_values`. |
| `etl_load_log` | One row per successful load. |

Column definitions are in the database comments. Key points:

- `period` is the **start** date of the reference period; `dim_ons_data.period_type` says how to label it.
- `lci_95` / `uci_95` are VARCHAR in `fact_ons_data` (cast with `TRY_CAST`).
- English LAs are `areacd` prefixes E06/E07/E08/E09; City of London (E09000001) is excluded from comparator bands.
- The West of England authorities are E06000022 (B&NES), E06000023 (Bristol), E06000024 (North Somerset), E06000025 (South Gloucestershire).

## Hand-maintained inputs

- `category` per indicator comes from `sql/indicator_categories.csv`, because ONS provides no equivalent. A new indicator with English LA data loads with `category` NULL, is logged as a warning, and is listed in `etl_load_log.unmapped_indicators`; it will not appear on dashboard cards until mapped and the flight is rebuilt and pushed.
- `is_category_default` is a hard-coded list in `sql/01_dim_viz_metadata.sql`. The flight fails if a category ends up without exactly one default (e.g. ONS renames one).

## Known caveats

- **GDP per head has no West of England values.** "Gross domestic product per head at current market prices" and "...in chained volume measures" are still published by ONS but the values for the four West of England authorities have been removed because they are under review by ONS. Both indicators therefore have no `chart_type` and are absent from `export_ons_values` / `export_ons_indicators`. They should reappear automatically after a refresh once ONS republishes values; check `etl_load_log` and the Dive's Population and economy options.
- Backups of the pre-refresh tables (taken 2026-10-29): `fact_ons_data_backup_20261029`, `dim_ons_data_backup_20261029`. They hold the earlier GDP values. Not maintained; delete when no longer needed.
- ONS revises historic values between releases (about 66,000 rows changed or were added between the September and October 2026 files), so earlier extracts will not reconcile exactly.
