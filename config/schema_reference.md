# Silver Layer Schema Reference

All attributes across every dataset, with canonical types, zero-padding rules, sentinel handling, and edge relationships.

---

## Type Legend

| Symbol | Meaning |
|--------|---------|
| `str` | String — never arrives as int or float in silver |
| `bool` | Native Python bool — stored as `True`/`False`, not `0`/`1` |
| `Int64` | Nullable integer (pandas `Int64`, allows `None`) |
| `int64` | Non-nullable integer |
| `float64` | 64-bit float — `None` for missing, never `"nan"` or `""` |
| `object` | Reserved/unpopulated column — always `None` in current data |

---

## 1. QCTDesignation

**File:** `silver/qct/silver_qct_{year}.parquet`
**Primary key:** `designation_id`
**Neo4j edge:** `APPLIES_TO → CensusTract` via `tract_fips`

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `designation_id` | `str` | — | `{tract_fips}_{year}` e.g. `01001020100_2025` |
| `source_pk` | `str` | — | Raw source key from HUD file (12-digit variant) |
| `tract_fips` | `str` | zfill(11) | 11-digit census tract FIPS |
| `county_fips` | `str` | zfill(5) | 5-digit county FIPS |
| `state_fips` | `str` | zfill(2) | 2-digit state FIPS |
| `cbsa_code` | `str` | zfill(5) | 5-digit CBSA code; `100000` sentinel → `None` |
| `fmr_area_code` | `str` | — | HUD FMR area code e.g. `METRO33860M33860` |
| `is_metro_tract` | `bool` | — | `True` if tract is within a metropolitan FMR area |
| `area_population` | `Int64` | — | Total population of the encompassing FMR area |
| `split_tr_flag` | `bool` | — | `True` if tract spans multiple FMR areas |
| `is_designated` | `bool` | — | `True` if officially designated as QCT this year |
| `designation_year` | `Int64` | — | Year of designation |
| `hud_notice_ref` | `str` | — | HUD Federal Register notice reference |
| `basis_boost_pct` | `Int64` | — | LIHTC basis boost: `0` or `30` |
| `qct_trigger_criterion` | `str` | — | Which HUD criterion triggered designation: `poverty_rate`, `income_ratio`, or `none` |
| `effective_date` | `str` | — | ISO date string `YYYY-MM-DD` |
| `poverty_rate_at_designation` | `float64` | — | Tract poverty rate used in designation (0.0–1.0) |
| `income_criterion_ratio` | `float64` | — | Ratio of tract median income to area VLIL threshold |
| `median_hh_income` | `float64` | — | ACS tract median household income |
| `median_hh_income_moe` | `float64` | — | Margin of error on median HH income |
| `VLIL4_current` | `Int64` | — | Very Low Income Limit for 4-person household (area-level) |
| `poverty_rate_vintage_minus1` | `float64` | — | Poverty rate from the prior ACS vintage year |
| `poverty_rate_vintage_minus2` | `float64` | — | Poverty rate from two ACS vintage years prior |
| `income_criterion_ratio_minus1` | `float64` | — | Income criterion ratio from prior year |
| `income_criterion_ratio_minus2` | `float64` | — | Income criterion ratio from two years prior |
| `poverty_rate_trend_3yr` | `float64` | — | 3-year change in poverty rate (current minus 2 years ago) |
| `poverty_trend_direction` | `str` | — | `improving`, `worsening`, or `stable` |
| `income_ratio_trend_3yr` | `float64` | — | 3-year change in income criterion ratio |
| `income_estimate_cv` | `float64` | — | Coefficient of variation for income estimate |
| `income_estimate_reliable` | `bool` | — | `True` if CV is below HUD reliability threshold |

---

## 2. SDDADesignation — Metro DDA (Area Level)

**File:** `silver/dda/silver_dda_{year}_metro_area.parquet`
**Primary key:** `designation_id`
**Neo4j edge:** `APPLIES_TO → MetroArea` via `fmr_area_code`

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `designation_id` | `str` | — | `{fmr_area_code}_{year}` |
| `fmr_area_code` | `str` | — | HUD FMR area code e.g. `METRO10380M10380` |
| `area_name` | `str` | — | Human-readable MSA name |
| `is_designated` | `bool` | — | `True` if designated as SDDA |
| `ranking_ratio` | `float64` | — | SAFMR-to-VLIL ratio used for DDA selection |
| `safmr_2br` | `Int64` | — | Small Area FMR for 2-bedroom unit (dollars) |
| `vlil_4person` | `Int64` | — | Very Low Income Limit for 4-person household |
| `total_population` | `Int64` | — | Total metro population |
| `population_in_qct` | `Int64` | — | Population in QCT-designated tracts |
| `effective_population` | `Int64` | — | DDA-eligible population (total minus QCT overlap) |
| `zcta_count_designated` | `int64` | — | Number of ZCTAs within the metro designated as DDAs |
| `designation_year` | `int64` | — | Year of designation |
| `basis_boost_pct` | `int64` | — | LIHTC basis boost: `30` for designated areas |
| `effective_date` | `str` | — | ISO date string `YYYY-MM-DD` |
| `lihtc_max_rent` | `float64` | — | Maximum LIHTC rent = `vlil_4person × 0.03` |
| `qct_overlap_pct` | `float64` | — | Share of metro population in a QCT (0.0–1.0) |

---

## 3. SDDADesignation — Metro DDA (ZCTA Level)

**File:** `silver/dda/silver_dda_{year}_metro_zcta.parquet`
**Primary key:** `zcta_code` (within year)
**Neo4j edge:** aggregated to area level before ingest

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `zcta_code` | `str` | zfill(5) | ZIP Code Tabulation Area code |
| `fmr_area_code` | `str` | — | HUD FMR area code for the containing metro |
| `area_name` | `str` | — | HUD metro area name |
| `is_designated` | `bool` | — | `True` if this ZCTA is designated |
| `ranking_ratio` | `float64` | — | SAFMR-to-VLIL ratio |
| `safmr_2br` | `int64` | — | Small Area FMR for 2-bedroom unit |
| `vlil_4person` | `int64` | — | Very Low Income Limit for 4-person household |
| `total_population` | `Int64` | — | ZCTA total population |
| `population_in_qct` | `Int64` | — | ZCTA population in QCT tracts |
| `effective_population` | `Int64` | — | DDA-eligible population |
| `designation_year` | `Int64` | — | Year of designation |
| `lihtc_max_rent` | `float64` | — | Maximum LIHTC rent = `vlil_4person × 0.03` |
| `qct_overlap_pct` | `float64` | — | Share of ZCTA population in a QCT |
| `is_territory` | `bool` | — | `True` if ZCTA is in a US territory (PR, GU, VI, AS, MP) |
| `effective_date` | `str` | — | ISO date string `YYYY-MM-DD` |

---

## 4. NMDDADesignation — Non-Metro DDA (County Level)

**File:** `silver/dda/silver_dda_{year}_nonmetro_county.parquet`
**Primary key:** `designation_id`
**Neo4j edge:** `APPLIES_TO → County` via `county_fips`

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `designation_id` | `str` | — | `{county_fips}_{year}` |
| `county_fips` | `str` | zfill(5) | 5-digit county FIPS; `None` if lookup failed (see gaps below) |
| `fmr_area_code` | `str` | — | HUD non-metro FMR area code e.g. `NCNTY66010N66010` |
| `area_name` | `str` | — | County or territory name as in HUD source |
| `is_designated` | `bool` | — | `True` if designated as NMDDA |
| `ranking_ratio` | `float64` | — | FMR-to-VLIL ratio for DDA selection |
| `fmr_2br` | `Int64` | — | County-level FMR for 2-bedroom (non-metro uses county FMR not SAFMR) |
| `vlil_4person` | `Int64` | — | Very Low Income Limit for 4-person household |
| `total_population` | `Int64` | — | Total county population |
| `population_in_qct` | `Int64` | — | County population in QCT tracts |
| `effective_population` | `Int64` | — | DDA-eligible population |
| `designation_year` | `Int64` | — | Year of designation |
| `basis_boost_pct` | `Int64` | — | LIHTC basis boost: `30` for designated counties |
| `is_territory` | `bool` | — | `True` if county is in a US territory |
| `effective_date` | `str` | — | ISO date string `YYYY-MM-DD` |
| `lihtc_max_rent` | `float64` | — | Maximum LIHTC rent = `vlil_4person × 0.03` |
| `qct_overlap_pct` | `float64` | — | Share of county population in a QCT |

> **Known gaps:** 10–49 rows per year (2003–2016) have `county_fips = None` due to non-standard county names in legacy PDFs that the area_name lookup could not resolve. These nodes are ingested but have no `APPLIES_TO → County` edge.

---

## 5. Section8AMILimit

**File:** `silver/ami/{year}/silver_section8_ami_limit_{year}.parquet`
**Primary key:** `limit_id`
**Neo4j edges:** `HAS_MSA_AMI ← County` via `hud_fmr_area_code` crosswalk

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `limit_id` | `str` | — | `{hud_fmr_area_code}_{year}_{program_type}` |
| `hud_fmr_area_code` | `str` | — | HUD FMR area code (renamed from `area_code` in Fix 7) |
| `area_name` | `str` | — | Human-readable HUD area name |
| `state` | `str` | zfill(2) | 2-digit state FIPS code |
| `program_type` | `str` | — | Income tier: `ELI` (30%), `VLI` (50%), `LI` (80%) |
| `mfi_value` | `int64` | — | Area Median Family Income (MFI) used as the base |
| `year` | `int64` | — | Limit effective year |
| `ami_pct` | `int64` | — | Percentage of AMI: `30`, `50`, or `80` |
| `limit_1person` | `int64` | — | Income limit for 1-person household |
| `limit_2person` | `int64` | — | Income limit for 2-person household |
| `limit_3person` | `int64` | — | Income limit for 3-person household |
| `limit_4person` | `int64` | — | Income limit for 4-person household |
| `limit_5person` | `int64` | — | Income limit for 5-person household |
| `limit_6person` | `int64` | — | Income limit for 6-person household |
| `limit_7person` | `int64` | — | Income limit for 7-person household |
| `limit_8person` | `int64` | — | Income limit for 8-person household |
| `max_income` | `int64` | — | Maximum qualifying income = `limit_4person` |
| `max_rent` | `float64` | — | Maximum allowable rent = `limit_4person × 0.03`; `None` except for `VLI` |
| `held_harmless` | `object` | — | Reserved: `None` in current data (HUD hold-harmless flag) |
| `volatility_cap_applied` | `object` | — | Reserved: `None` in current data (HUD volatility cap flag) |
| `uncapped_value` | `object` | — | Reserved: `None` in current data (raw pre-cap limit) |

---

## 6. SpecialProgramLimit

**File:** `silver/ami/{year}/silver_special_program_limit_{year}.parquet`
**Primary key:** `limit_id`
**Same schema as Section8AMILimit** except:

| Attribute | Type | Notes |
|-----------|------|-------|
| `program_type` | `str` | `SEC221_BMIR`, `SEC235`, or `SEC236` instead of ELI/VLI/LI |
| `ami_pct` | `object` | Always `None` — special programs are not a simple % of AMI |
| `max_rent` | `object` | Always `None` — max rent not applicable to special programs |

All other columns are identical to Section8AMILimit.

---

## 7. StateAMILimit

**File:** `silver/ami/{year}/silver_state_ami_limit_{year}.parquet`
**Primary key:** `limit_id`
**Neo4j edge:** `APPLIES_TO → State` via `state`

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `limit_id` | `str` | — | `{state_fips}_{year}_{program_type}` |
| `state` | `str` | zfill(2) | 2-digit state FIPS code |
| `program_type` | `str` | — | `ELI`, `VLI`, or `LI` |
| `state_mfi` | `int64` | — | State-level Median Family Income |
| `year` | `int64` | — | Limit effective year |
| `ami_pct` | `int64` | — | `30`, `50`, or `80` |
| `limit_1person` | `int64` | — | State income limit for 1-person household |
| `limit_2person` | `int64` | — | State income limit for 2-person household |
| `limit_3person` | `int64` | — | State income limit for 3-person household |
| `limit_4person` | `int64` | — | State income limit for 4-person household |
| `limit_5person` | `int64` | — | State income limit for 5-person household |
| `limit_6person` | `int64` | — | State income limit for 6-person household |
| `limit_7person` | `int64` | — | State income limit for 7-person household |
| `limit_8person` | `int64` | — | State income limit for 8-person household |
| `max_income` | `int64` | — | Maximum qualifying income = `limit_4person` |
| `max_rent` | `float64` | — | Max rent = `limit_4person × 0.03`; `None` for ELI |
| `held_harmless` | `object` | — | Reserved: `None` |
| `volatility_cap_applied` | `object` | — | Reserved: `None` |
| `uncapped_value` | `object` | — | Reserved: `None` |

---

## 8. County-Area Crosswalk (internal, not a Neo4j node)

**File:** `silver/ami/{year}/silver_county_area_crosswalk_{year}.parquet`
Used internally by `ingest_datasets.py` to create `HAS_MSA_AMI` and `HAS_STATE_AMI` edges.

| Attribute | Type | Notes |
|-----------|------|-------|
| `fips` | `str` | 10-digit county FIPS (state + county + 99999 suffix for non-metro) |
| `state` | `str` | 2-digit state FIPS |
| `county` | `str` | 3-digit county FIPS |
| `hud_area_code` | `str` | HUD FMR area code — matched against `Section8AMILimit.hud_fmr_area_code` |
| `hud_area_name` | `str` | HUD area name |
| `metro` | `int64` | `1` = metropolitan, `0` = non-metropolitan |
| `year` | `int64` | Reference year |

---

## 9. LenderBehaviorRisk

**File:** `silver/hmda/{year}/silver_lender_behavior_risk_{year}.parquet`
**Primary key:** `risk_id`
**Neo4j edge:** `APPLIES_TO → MetroArea` via `cbsa_code`

| Attribute | Type | Padding | Notes |
|-----------|------|---------|-------|
| `risk_id` | `str` | — | `{cbsa_code}_{assessment_year}` |
| `cbsa_code` | `str` | zfill(5) | 5-digit CBSA code; `None` for rural county-level rows (no CBSA) |
| `assessment_year` | `int64` | — | HMDA assessment year |
| `msa_code` | `str` | — | Source county FIPS used as MSA proxy in aggregation |
| `total_applications` | `int64` | — | Total mortgage applications (originated + denied) |
| `total_originated` | `int64` | — | Total loans originated |
| `total_denied` | `int64` | — | Total loans denied |
| `multifamily_applications` | `int64` | — | Applications for 5+ unit multifamily properties |
| `multifamily_denied` | `int64` | — | Multifamily applications denied |
| `lender_count` | `object` | — | Distinct lender count (currently `None` — Panel file required) |
| `avg_loan_amount` | `float64` | — | Average origination loan amount (dollars) |
| `gse_sold_pct` | `float64` | — | Share of originated loans sold to Fannie Mae or Freddie Mac |
| `denial_rate_overall` | `float64` | — | Overall denial rate = `denied / (originated + denied)` |
| `denial_rate_minority` | `float64` | — | Denial rate for minority applicants (race or Hispanic ethnicity) |
| `denial_rate_white` | `float64` | — | Denial rate for non-Hispanic white applicants; `None` if <5 applicants |
| `multifamily_denial_rate` | `float64` | — | Denial rate for multifamily loan applications |
| `denial_rate_disparity_ratio` | `float64` | — | Minority denial rate ÷ white denial rate; `None` if white rate unavailable |
| `fair_lending_risk_score` | `float64` | — | Composite score: `0.5×disparity + 0.3×overall + 0.2×mf_rate` |
| `risk_tier` | `str` | — | `low` (<0.75), `moderate` (0.75–1.0), `elevated` (1.0–1.5), `high` (>1.5), `unknown` |
| `top_denial_reason` | `str` | — | Most common HMDA denial reason code |
| `is_high_disparity` | `bool` | — | `True` if `denial_rate_disparity_ratio > 2.0` |
| `is_multifamily_constrained` | `bool` | — | `True` if multifamily denial rate > (mean + 1 std) across all CBSAs |

> **Known gaps:** ~1,300–1,320 rows per year have `cbsa_code = None` (rural counties with no CBSA affiliation). These are ingested as nodes without a `APPLIES_TO → MetroArea` edge.

---

## Global Normalisation Rules

### Zero-padding (always applied before parquet write)
| Field | Rule |
|-------|------|
| `fips_code` / `tract_fips` | `zfill(11)` |
| `county_fips` | `zfill(5)` |
| `state_fips` / `state` | `zfill(2)` |
| `cbsa_code` | strip `.0` float artifact, then `zfill(5)` |
| `zcta_code` | `zfill(5)` |

### Sentinel values → `None`
| Value | Field | Action |
|-------|-------|--------|
| `100000` / `100000.0` | `cbsa_code` | → `None` |
| `""` | all string fields | → `None` |
| `"nan"` / `"NA"` / `"None"` | all string fields | → `None` |
| `NaN` | all numeric fields | → `None` (parquet null) |

### Boolean fields (always native `bool`, not `0`/`1`)
`is_designated`, `is_territory`, `is_high_disparity`, `is_multifamily_constrained`,
`income_estimate_reliable`, `split_tr_flag`, `is_metro_tract`

### Integer fields (always `Int64` or `int64`, never float-encoded)
`designation_year`, `year`, `assessment_year`, `basis_boost_pct`, `ami_pct`,
`mfi_value`, `state_mfi`, `max_income`, `max_rent`-derived base,
`limit_1person` through `limit_8person`,
`total_applications`, `total_originated`, `total_denied`,
`multifamily_applications`, `multifamily_denied`,
`area_population`, `total_population`, `population_in_qct`, `effective_population`

### Float fields (always `float64`, never int-encoded)
`poverty_rate_at_designation`, `poverty_rate_vintage_minus1`, `poverty_rate_vintage_minus2`,
`income_criterion_ratio`, `income_criterion_ratio_minus1`, `income_criterion_ratio_minus2`,
`poverty_rate_trend_3yr`, `income_ratio_trend_3yr`, `income_estimate_cv`,
`ranking_ratio`, `qct_overlap_pct`, `lihtc_max_rent`, `max_rent`,
`denial_rate_overall`, `denial_rate_minority`, `denial_rate_white`,
`multifamily_denial_rate`, `denial_rate_disparity_ratio`,
`fair_lending_risk_score`, `gse_sold_pct`, `avg_loan_amount`

---

## Neo4j Node & Edge Summary

| Node Label | PK | APPLIES_TO target | Edge type |
|---|---|---|---|
| `QCTDesignation` | `designation_id` | `CensusTract.fips_code` | `APPLIES_TO` |
| `SDDADesignation` | `designation_id` | `MetroArea.fmr_area_code` | `APPLIES_TO` |
| `NMDDADesignation` | `designation_id` | `County.county_fips` | `APPLIES_TO` |
| `Section8AMILimit` | `limit_id` | ← `County` via crosswalk | `HAS_MSA_AMI` |
| `SpecialProgramLimit` | `limit_id` | — | — |
| `StateAMILimit` | `limit_id` | `State.state_fips` | `APPLIES_TO` |
| `LenderBehaviorRisk` | `risk_id` | `MetroArea.cbsa_code` | `APPLIES_TO` |
| `CensusTract` | `fips_code` | `County`, `State`, `MetroArea` | `IN_COUNTY`, `IN_STATE`, `IN_METRO` |
| `County` | `county_fips` | `State`, `MetroArea` | `IN_STATE`, `IN_METRO` |
| `MetroArea` | `cbsa_code` | `State` | `IN_STATE` |
| `State` | `state_fips` | — | — |
