# LIHTC Graph Database Schema

> **Database**: Neo4j Enterprise 2026.05.0
> **URI**: `neo4j://127.0.0.1:7687`
> **Total nodes**: ~2,054,916 | **Total relationships**: ~2,150,769
> **Last updated**: June 2026 (post-gap-fix audit)

---

## Table of Contents

1. [Graph Overview](#1-graph-overview)
2. [Node Types](#2-node-types)
3. [Relationship Types](#3-relationship-types)
4. [Year Coverage](#4-year-coverage)
5. [Traversal Patterns](#5-traversal-patterns)
6. [Indexes & Constraints](#6-indexes--constraints)
7. [Known Gaps](#7-known-gaps)
8. [Data Pipeline](#8-data-pipeline)
9. [Scripts Reference](#9-scripts-reference)
10. [Final Audit](#10-final-audit)

---

## 1. Graph Overview

```
                         State (56)
                        ^     ^
              IN_STATE /       \ IN_STATE
                      /         \
             County (3,251)   MetroArea (443)
            ^    ^    ^         ^    ^     ^
  IN_COUNTY/   HAS_   HAS_  IN_METRO  APPLIES_TO
          /   MSA_AMI STATE_AMI  |        |
CensusTract  Section8 StateAMI  SDDA   HMDA
(111,603)    AMILimit  Limit   Desig   Risk
    ^         (124,425)(2,412) (1,880)(25,831)
    |
APPLIES_TO
    |
QCTDesignation (1,657,416)

Also:
  NMDDADesignation (3,174) --APPLIES_TO--> County
  LenderBehaviorRisk       --APPLIES_TO--> County (non-metro, 15,993 edges)
  SpecialProgramLimit (124,425) -- standalone, no edges (by design)
```

---

## 2. Node Types

### Geographic Nodes (anchor layer)

#### State
| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `state_fips` | STRING **PK** | `"48"` | 2-digit FIPS, zero-padded |
| `state_abbr` | STRING | `"TX"` | |
| `state_name` | STRING | `"Texas"` | |

**Count**: 56 (50 states + DC + 5 territories)

#### County
| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `county_fips` | STRING **PK** | `"48113"` | 5-digit FIPS (state + county) |
| `state_fips` | STRING FK→State | `"48"` | |

**Count**: 3,251
**Note**: `county_name` is NULL — TIGER tract file doesn't carry county names. Load from Census gazetteer if display names are needed.

#### CensusTract
| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `fips_code` | STRING **PK** | `"48113017819"` | 11-digit FIPS (state2 + county3 + tract6) |
| `state_fips` | STRING FK→State | `"48"` | |
| `county_fips` | STRING FK→County | `"48113"` | |
| `cbsa_code` | STRING FK→MetroArea | `"19100"` | NULL for non-metro tracts |
| `fmr_area_code` | STRING | `"METRO19100M19100"` | HUD FMR area code |
| `is_metro` | BOOLEAN | `true` | From QCT enrichment |

**Count**: 111,603
- 85,529 from 2025 TIGER (2020 Census boundaries)
- 13,798 added from QCT silver data across all years 2003–2024 (2000 + 2010 Census vintage tracts)
- 138 Connecticut planning region tracts (2022 restructure) — connected via IN_COUNTY + IN_STATE

**Note**: All 111,603 CensusTract nodes have at least one relationship (0 orphans).

#### MetroArea
| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `cbsa_code` | STRING **PK** | `"19100"` | OMB CBSA code (string, no `.0` suffix) |
| `fmr_area_code` | STRING | `"METRO19100M19100"` | HUD FMR area code |
| `state_fips` | STRING | `"48"` | Primary state |
| `area_population` | INTEGER | `7637387` | From QCT |
| `cbsa_title` | STRING | `"Dallas-Fort Worth..."` | From OMB delineation file |

**Count**: 443 (441 from OMB 2023 crosswalk + Dayton OH `19380` + Prescott AZ `39140` added to resolve SDDA gaps)

---

### Data Nodes (one per designation/limit/year)

#### QCTDesignation
Qualified Census Tract designation status per tract per year.

| Attribute | Type | Notes |
|-----------|------|-------|
| `designation_id` | STRING **PK** | `"{tract_fips}_{year}"` |
| `designation_year` | INTEGER | 2003–2025 |
| `is_designated` | INTEGER | 1 = QCT, 0 = not |
| `qct_trigger_criterion` | STRING | `"poverty"` or `"income"` |
| `poverty_rate_at_designation` | FLOAT | |
| `income_criterion_ratio` | FLOAT | |
| `median_hh_income` | INTEGER | |
| `VLIL4_current` | INTEGER | Very Low Income Limit, 4-person |
| `basis_boost_pct` | INTEGER | 30 (130% basis boost if QCT) |
| `effective_date` | STRING | |
| `split_tr_flag` | INTEGER | 1 if tract split across FMR areas |
| `is_metro_tract` | INTEGER | |
| `area_population` | INTEGER | |
| `cbsa_code` | STRING | |
| `fmr_area_code` | STRING | |
| `county_fips` | STRING | |
| `state_fips` | STRING | |
| `source_pk` | STRING | Original HUD identifier |
| `hud_notice_ref` | STRING | Federal Register notice |
| `poverty_rate_vintage_minus1` | STRING | |
| `poverty_rate_vintage_minus2` | STRING | |
| `income_criterion_ratio_minus1` | STRING | |
| `income_criterion_ratio_minus2` | STRING | |
| `poverty_rate_trend_3yr` | STRING | |
| `poverty_trend_direction` | STRING | |
| `income_ratio_trend_3yr` | STRING | |
| `income_estimate_cv` | FLOAT | ACS estimate coefficient of variation |
| `income_estimate_reliable` | BOOLEAN | |
| `median_hh_income_moe` | FLOAT | ACS margin of error |

**Count**: 1,657,416 (23 years × ~72k tracts/year, 2003–2025)
**Edge coverage**: 100% — all 1,657,416 nodes have an APPLIES_TO→CensusTract edge.

#### SDDADesignation
Small Difficult Development Area — metro-level designation.

| Attribute | Type | Notes |
|-----------|------|-------|
| `designation_id` | STRING **PK** | `"{fmr_area_code}_{year}"` |
| `designation_year` | INTEGER | 2019–2025 |
| `is_designated` | INTEGER | Always 1 |
| `fmr_area_code` | STRING | HUD FMR area code |
| `area_name` | STRING | |
| `ranking_ratio` | FLOAT | SAFMR-to-VLIL ratio |
| `safmr_2br` | FLOAT | Small Area FMR, 2-bedroom |
| `vlil_4person` | INTEGER | |
| `total_population` | INTEGER | |
| `population_in_qct` | INTEGER | |
| `effective_population` | INTEGER | |
| `basis_boost_pct` | INTEGER | 30 |
| `effective_date` | STRING | |
| `lihtc_max_rent` | FLOAT | |
| `qct_overlap_pct` | FLOAT | |
| `zcta_count_designated` | INTEGER | |

**Count**: 1,880
**Edge coverage**: 98.2% (1,846 / 1,880 connected)
**Unconnected 34**: PR territories (12) + county-level HUD FMR areas (22) — no CBSA mapping possible.

#### NMDDADesignation
Non-Metropolitan Difficult Development Area — county-level designation.

| Attribute | Type | Notes |
|-----------|------|-------|
| `designation_id` | STRING **PK** | `"{county_fips}_{year}"` |
| `designation_year` | INTEGER | 2017–2025 |
| `is_designated` | INTEGER | Always 1 |
| `county_fips` | STRING | |
| `fmr_area_code` | STRING | |
| `area_name` | STRING | |
| `ranking_ratio` | FLOAT | FMR-to-VLIL ratio |
| `fmr_2br` | FLOAT | Fair Market Rent, 2-bedroom |
| `vlil_4person` | INTEGER | |
| `total_population` | INTEGER | |
| `population_in_qct` | INTEGER | |
| `effective_population` | INTEGER | |
| `basis_boost_pct` | INTEGER | |
| `effective_date` | STRING | |
| `lihtc_max_rent` | FLOAT | |
| `qct_overlap_pct` | FLOAT | |
| `is_territory` | BOOLEAN | |

**Count**: 3,174
**Edge coverage**: 54.4% (1,726 / 3,174 connected)
**Unconnected 1,448**: HUD source gap 2017–2020 — `county_fips` was NULL in HUD's published data for those years. Not fixable.

#### Section8AMILimit
HUD Section 8 income limits per area, program type, and year.

| Attribute | Type | Notes |
|-----------|------|-------|
| `limit_id` | STRING **PK** | `"{area_code}_{year}_{program_type}"` |
| `area_code` | STRING | HUD area code (e.g. `"METRO19100M19100"` or `"NCNTY48113N48113"`) |
| `area_name` | STRING | |
| `state` | STRING | 2-digit state FIPS |
| `program_type` | STRING | `"ELI"`, `"VLI"`, or `"LI"` |
| `mfi_value` | INTEGER/FLOAT | Median Family Income |
| `year` | INTEGER | 2010–2025 |
| `ami_pct` | FLOAT | AMI percentage (30%, 50%, 80%) |
| `limit_1person`–`limit_8person` | INTEGER | Income limit by household size |
| `max_income` | INTEGER | |
| `max_rent` | FLOAT | Maximum gross rent (30% of VLI / 12) |

**Count**: 124,425 (16 years × ~2,600 areas × 3 programs)
**Edge coverage**: 99.3% (123,510 connected — both metro METRO* and non-metro NCNTY* area codes)
**Unconnected 915**: Territories (Guam, USVI, PR) and special program areas with no county mapping.
**Receives**: HAS_MSA_AMI in-edges from County (154,785 total — metro + non-metro combined).

#### SpecialProgramLimit
HUD Section 221 BMIR / Section 235 / Section 236 limits.

| Attribute | Type | Notes |
|-----------|------|-------|
| `limit_id` | STRING **PK** | Same pattern as Section8AMILimit |
| `area_name` | STRING | |
| `state` | STRING | |
| `program_type` | STRING | `"SEC236"`, `"SEC221_BMIR"`, `"SEC235"` |
| `mfi_value` | INTEGER/FLOAT | |
| `year` | INTEGER | 2010–2025 |
| `ami_pct` | FLOAT | |
| `limit_1person`–`limit_8person` | INTEGER | |
| `max_income` | INTEGER | |

**Count**: 124,425
**Edges**: 0 — not used in LITHC rent calculations. No geographic crosswalk exists. Retrievable only by direct `limit_id` lookup. Intentional by design.

#### StateAMILimit
State-level income limits (ELI/VLI/LI) per state per year.

| Attribute | Type | Notes |
|-----------|------|-------|
| `limit_id` | STRING **PK** | `"{state}_{year}_{program_type}"` |
| `state` | STRING | 2-digit state FIPS |
| `program_type` | STRING | `"ELI"`, `"VLI"`, `"LI"` |
| `state_mfi` | INTEGER/FLOAT | State median family income |
| `year` | INTEGER | 2010–2025 |
| `ami_pct` | FLOAT | |
| `limit_1person`–`limit_8person` | INTEGER | |
| `max_income` | INTEGER | |
| `max_rent` | FLOAT | |

**Count**: 2,412 (16 years × ~50 states × 3 programs)
**Edge coverage**: 100%
- Receives HAS_STATE_AMI in-edges from County (95,865 — non-metro counties only)
- Sends APPLIES_TO out-edges to State (2,412 — one per node, geographic anchoring)

#### LenderBehaviorRisk
HMDA-derived fair lending risk assessment per county per year.

| Attribute | Type | Notes |
|-----------|------|-------|
| `risk_id` | STRING **PK** | `"{county_fips}_{year}"` |
| `msa_code` | STRING | County FIPS 5-digit (field name is historical misnomer) |
| `assessment_year` | STRING | `"2018"`–`"2025"` — **STRING type**, use `toString()` in Cypher |
| `cbsa_code` | STRING | Resolved CBSA via OMB 2023 crosswalk (NULL for non-metro) |
| `total_applications` | INTEGER | |
| `total_originated` | INTEGER | |
| `total_denied` | INTEGER | |
| `multifamily_applications` | INTEGER | |
| `multifamily_denied` | INTEGER | |
| `lender_count` | INTEGER | Unique top-holder RSSDIDs (NULL for 2018–2021 — Panel not downloaded; NULL for 2025 — not yet published) |
| `avg_loan_amount` | FLOAT | |
| `gse_sold_pct` | FLOAT | Fannie/Freddie purchase percentage |
| `denial_rate_overall` | FLOAT | |
| `denial_rate_minority` | FLOAT | |
| `denial_rate_white` | FLOAT | White non-Hispanic denial rate |
| `multifamily_denial_rate` | FLOAT | |
| `denial_rate_disparity_ratio` | FLOAT | minority / white denial rate |
| `fair_lending_risk_score` | FLOAT | 0.50×disparity + 0.30×denial + 0.20×mf_denial |
| `risk_tier` | STRING | `"low"`, `"moderate"`, `"elevated"`, `"high"`, `"unknown"` |
| `top_denial_reason` | STRING | Most frequent denial reason code |
| `is_high_disparity` | BOOLEAN | disparity_ratio > 2.0 |
| `is_multifamily_constrained` | BOOLEAN | mf_denial_rate > mean + 1 std dev |

**Count**: 25,831 (8 years × ~3,224 counties, 2018–2025)
**Edge coverage**: 99.8% (25,773 connected)
- Metro counties: APPLIES_TO→MetroArea (9,780 edges)
- Non-metro counties: APPLIES_TO→County (15,993 edges)
- Unconnected 58: `msa_code = "NA"` records (unknown county at source)

---

## 3. Relationship Types

### Geographic Hierarchy

| Relationship | From | To | Count | Description |
|-------------|------|----|------:|-------------|
| `IN_STATE` | County | State | 3,251 | County belongs to this state |
| `IN_STATE` | MetroArea | State | 441 | Metro area's primary state |
| `IN_STATE` | CensusTract | State | 111,603 | Tract belongs to this state (added during gap fix) |
| `IN_COUNTY` | CensusTract | County | 111,597 | Tract is within this county |
| `IN_METRO` | CensusTract | MetroArea | 85,000 | Tract is within this metro area |
| `IN_METRO` | County | MetroArea | 3,194 | County is within this metro area |

### Designation Edges

| Relationship | From | To | Count | Description |
|-------------|------|----|------:|-------------|
| `APPLIES_TO` | QCTDesignation | CensusTract | 1,657,416 | QCT status for this tract-year |
| `APPLIES_TO` | SDDADesignation | MetroArea | 1,846 | SDDA designation for this metro-year |
| `APPLIES_TO` | NMDDADesignation | County | 1,726 | Non-metro DDA for this county-year |
| `APPLIES_TO` | StateAMILimit | State | 2,412 | AMI limit anchored to this state |
| `APPLIES_TO` | LenderBehaviorRisk | MetroArea | 9,780 | Metro county HMDA risk |
| `APPLIES_TO` | LenderBehaviorRisk | County | 15,993 | Non-metro county HMDA risk |

### AMI Edges

| Relationship | From | To | Count | Description |
|-------------|------|----|------:|-------------|
| `HAS_MSA_AMI` | County | Section8AMILimit | 154,785 | County's AMI limit (metro + non-metro NCNTY codes) |
| `HAS_STATE_AMI` | County | StateAMILimit | 95,865 | Non-metro county falls back to state AMI |

**Total relationships**: ~2,150,769

---

## 4. Year Coverage

| Dataset | Node Label | Years | Rows/Year | Source |
|---------|-----------|-------|----------:|--------|
| QCT | QCTDesignation | 2003–2025 | ~72,000 | HUD QCT lists |
| SDDA | SDDADesignation | 2019–2025 | ~270 | HUD DDA metro |
| NMDDA | NMDDADesignation | 2017–2025 | ~350 | HUD DDA non-metro |
| Section 8 AMI | Section8AMILimit | 2010–2025 | ~7,800 | HUD income limits |
| Special Program | SpecialProgramLimit | 2010–2025 | ~7,800 | HUD Sec 221/235/236 |
| State AMI | StateAMILimit | 2010–2025 | ~150 | HUD state limits |
| HMDA | LenderBehaviorRisk | **2018–2025** | ~3,224 | FFIEC LAR (snapshot 2018–2021, modified LAR 2022+) |

**Active agent window**: 2018–2025 for HMDA, 2003–2025 for QCT, 2010–2025 for AMI.

---

## 5. Traversal Patterns

### Tract QCT status
```cypher
MATCH (t:CensusTract {fips_code: $fips})
OPTIONAL MATCH (q:QCTDesignation)-[:APPLIES_TO]->(t)
WHERE q.designation_year = $year
RETURN q.is_designated, q.qct_trigger_criterion, q.basis_boost_pct
```

### Tract AMI rent limit (metro)
```cypher
MATCH (t:CensusTract {fips_code: $fips})-[:IN_COUNTY]->(c:County)
      -[:HAS_MSA_AMI]->(a:Section8AMILimit)
WHERE a.program_type = 'VLI' AND a.year = $year
RETURN a.limit_4person, a.max_rent
```

### Tract AMI rent limit (non-metro fallback)
```cypher
MATCH (t:CensusTract {fips_code: $fips})-[:IN_COUNTY]->(c:County)
      -[:HAS_STATE_AMI]->(a:StateAMILimit)
WHERE a.program_type = 'VLI' AND a.year = $year
RETURN a.limit_4person, a.max_rent
```

### Tract DDA status
```cypher
MATCH (t:CensusTract {fips_code: $fips})-[:IN_COUNTY]->(c:County)
      -[:IN_METRO]->(m:MetroArea)
OPTIONAL MATCH (sd:SDDADesignation)-[:APPLIES_TO]->(m)
WHERE sd.designation_year = $year
RETURN sd.is_designated, sd.ranking_ratio, sd.basis_boost_pct
```

### Tract HMDA lending risk (metro)
```cypher
MATCH (t:CensusTract {fips_code: $fips})-[:IN_COUNTY]->(c:County)
      -[:IN_METRO]->(m:MetroArea)
      <-[:APPLIES_TO]-(r:LenderBehaviorRisk)
WHERE r.assessment_year = toString($year)
RETURN r.risk_tier, r.denial_rate_disparity_ratio, r.fair_lending_risk_score
```

### Tract HMDA lending risk (non-metro)
```cypher
MATCH (t:CensusTract {fips_code: $fips})-[:IN_COUNTY]->(c:County)
      <-[:APPLIES_TO]-(r:LenderBehaviorRisk)
WHERE r.assessment_year = toString($year)
RETURN r.risk_tier, r.denial_rate_disparity_ratio, r.fair_lending_risk_score
```

### Full tract profile (single query)
```cypher
MATCH (t:CensusTract {fips_code: $fips})-[:IN_COUNTY]->(c:County)-[:IN_STATE]->(s:State)
OPTIONAL MATCH (q:QCTDesignation {designation_year: $year})-[:APPLIES_TO]->(t)
OPTIONAL MATCH (c)-[:IN_METRO]->(m:MetroArea)
OPTIONAL MATCH (c)-[:HAS_MSA_AMI]->(ami:Section8AMILimit)
  WHERE ami.program_type = 'VLI' AND ami.year = $year
OPTIONAL MATCH (c)-[:HAS_STATE_AMI]->(sami:StateAMILimit)
  WHERE sami.program_type = 'VLI' AND sami.year = $year
OPTIONAL MATCH (sd:SDDADesignation {designation_year: $year})-[:APPLIES_TO]->(m)
OPTIONAL MATCH (r:LenderBehaviorRisk)-[:APPLIES_TO]->(m)
  WHERE r.assessment_year = toString($year)
OPTIONAL MATCH (rn:LenderBehaviorRisk)-[:APPLIES_TO]->(c)
  WHERE rn.assessment_year = toString($year)
RETURN t.fips_code,
       s.state_name,
       c.county_fips,
       q.is_designated AS is_qct,
       q.basis_boost_pct AS qct_boost,
       coalesce(ami.max_rent, sami.max_rent) AS ami_max_rent,
       coalesce(ami.limit_4person, sami.limit_4person) AS ami_vli_4person,
       sd.is_designated AS is_dda,
       sd.basis_boost_pct AS dda_boost,
       coalesce(r.risk_tier, rn.risk_tier) AS hmda_risk_tier,
       coalesce(r.fair_lending_risk_score, rn.fair_lending_risk_score) AS hmda_risk_score
```

---

## 6. Indexes & Constraints

All primary keys have uniqueness constraints (which also create indexes):

| Label | Property | Type |
|-------|----------|------|
| State | `state_fips` | UNIQUE |
| County | `county_fips` | UNIQUE |
| CensusTract | `fips_code` | UNIQUE |
| MetroArea | `cbsa_code` | UNIQUE |
| QCTDesignation | `designation_id` | UNIQUE |
| SDDADesignation | `designation_id` | UNIQUE |
| NMDDADesignation | `designation_id` | UNIQUE |
| Section8AMILimit | `limit_id` | UNIQUE |
| SpecialProgramLimit | `limit_id` | UNIQUE |
| StateAMILimit | `limit_id` | UNIQUE |
| LenderBehaviorRisk | `risk_id` | UNIQUE |

Additional performance indexes:

| Label | Property | Purpose |
|-------|----------|---------|
| Section8AMILimit | `area_code` | Fast HAS_MSA_AMI edge traversal (was 120s/year without, 0.4s with) |

---

## 7. Known Gaps

### Unfixable source gaps

| Gap | Nodes affected | Cause |
|-----|---------------|-------|
| **NMDDA 2017–2020** | 1,448 orphans | HUD did not publish `county_fips` for non-metro DDA in these years |
| **HMDA 2015–2017** | Not ingested | Pre-reform HMDA uses a different schema (SNAPSHOT_LAR, different column positions). Separate pipeline needed. |
| **HMDA lender_count 2018–2021** | NULL on all 2018–2021 nodes | FFIEC Panel files not downloaded for these years |
| **HMDA lender_count 2025** | NULL | FFIEC Panel for 2025 not yet published |

### Acceptable structural gaps

| Gap | Nodes affected | Reason |
|-----|---------------|--------|
| **SDDA unconnected 34** | 34 nodes | PR territories (12) + county-level HUD FMR areas (22) — no CBSA mapping exists |
| **Section8AMILimit unconnected 915** | 915 nodes | Territories (Guam, USVI, PR) and special program areas with no county mapping |
| **LenderBehaviorRisk unconnected 58** | 58 nodes | `msa_code = "NA"` in source data — county unknown |
| **SpecialProgramLimit 0 edges** | 124,425 nodes | Sec 221/235/236 not used in LIHTC calculations — intentional |
| **county_name NULL** | All County nodes | TIGER tract file doesn't carry county names — load from Census gazetteer if needed |

### Schema quirks (important for agent Cypher)

- `assessment_year` on LenderBehaviorRisk is **STRING** (`"2022"`), not INTEGER. Always use `toString($year)` in comparisons.
- `area_code` on Section8AMILimit uses HUD FMR format (`"METRO19100M19100"` or `"NCNTY48113N48113"`), not raw CBSA numeric codes.
- `msa_code` on LenderBehaviorRisk is actually **county FIPS** (5-digit), despite the name. The `cbsa_code` field is the true CBSA (resolved via OMB crosswalk, NULL for non-metro).
- `MetroArea.cbsa_code` is stored as a clean string (e.g. `"19100"`) — the `.0` float suffix bug was fixed via Cypher in a one-time patch.
- Metro AMI and non-metro AMI both use `HAS_MSA_AMI` as the relationship type (historical — NCNTY area codes were added later). Use `coalesce()` with `HAS_STATE_AMI` as fallback for non-metro tracts.

---

## 8. Data Pipeline

### Architecture
```
Bronze (raw files)  ->  Silver (cleaned parquet)  ->  Neo4j (graph)
                                                        ^
                                                 graph_state.db
                                                 (DuckDB -- tracks
                                                  CREATE/MERGE/SKIP)
```

### Bronze sources

| Dataset | Source | Format | Location |
|---------|--------|--------|----------|
| QCT | HUD | Excel/CSV | `bronze_files/qct/` |
| DDA | HUD | Excel/CSV | `bronze_files/dda/` |
| AMI | HUD | Excel | `bronze_files/ami/` |
| HMDA 2022+ | FFIEC/CFPB (combined modified LAR) | Pipe-delimited TXT in ZIP | `bronze_files/hmda/{year}/` |
| HMDA 2018–2021 | FFIEC/CFPB (snapshot LAR) | Pipe-delimited TXT in ZIP | `bronze_files/hmda/{year}/` |
| TIGER | Census Bureau | CSV (tract reference) | `bronze_files/Geographic/` |
| OMB delineation | Census Bureau | Excel | `bronze_files/omb/list1_2023.xlsx` |

### Silver outputs

| File | Location |
|------|----------|
| `silver_qct_{year}.parquet` | `silver/qct/` |
| `silver_dda_{year}_metro_area.parquet` | `silver/dda/` |
| `silver_dda_{year}_nonmetro_county.parquet` | `silver/dda/` |
| `silver_section8_ami_limit_{year}.parquet` | `silver/ami/{year}/` |
| `silver_special_program_limit_{year}.parquet` | `silver/ami/{year}/` |
| `silver_state_ami_limit_{year}.parquet` | `silver/ami/{year}/` |
| `silver_county_area_crosswalk_{year}.parquet` | `silver/ami/{year}/` |
| `silver_hmda_{year}_lar.parquet` | `silver/hmda/{year}/` |
| `silver_lender_behavior_risk_{year}.parquet` | `silver/hmda/{year}/` |

### Ingestion performance

| Method | Speed | Notes |
|--------|------:|-------|
| Python driver UNWIND | ~162/s | Initial approach — too slow |
| LOAD CSV IN TRANSACTIONS OF 5000 ROWS | 5,000–8,000/s | Final approach — server-side batch |
| State DB update (bulk DataFrame) | 66k rows in <1s | DuckDB registered DataFrame DELETE+INSERT |

### Re-run behavior

`ingest_datasets.py` uses `graph_state.db` (DuckDB) to track ingestion state:

- **Fresh run**: PK sample finds nothing → skip hashing → all CREATE
- **Re-run, no changes**: PK sample hits → hash compare → all SKIP (~2s/year)
- **Re-run, data changed**: hash mismatch → only changed rows MERGE

```bash
# Full ingestion
python ingest_scripts/neo4j_ingest/ingest_datasets.py --dataset qct dda ami hmda

# Single dataset, specific years
python ingest_scripts/neo4j_ingest/ingest_datasets.py --dataset hmda --year 2024 2025

# Bootstrap geography first (one-time)
python ingest_scripts/bootstrap/bootstrap_geography.py
```

---

## 9. Scripts Reference

### Bronze Ingest
| Script | Purpose |
|--------|---------|
| `ingest_scripts/bronze_ingest/ingest_HMDA.py` | Downloads HMDA LAR, Panel, TS from CFPB (2018–2025) |
| `ingest_scripts/bronze_ingest/ingest_QCTDDA.py` | Downloads QCT/DDA/NMDDA from HUD |
| `ingest_scripts/bronze_ingest/ingest_AMI.py` | Downloads Section 8 AMI limits from HUD |

### Silver Transform
| Script | Purpose |
|--------|---------|
| `ingest_scripts/silver_transform/silver_hmda.py` | HMDA LAR -> silver parquet + Excel. Handles both snapshot (2018–2021) and modified LAR (2022+) formats. |
| `ingest_scripts/silver_transform/build_risk_from_parquet.py` | Builds `silver_lender_behavior_risk_{year}.parquet` from silver LAR using named columns (fixes the column-position bug in pre-2022 data) |
| `ingest_scripts/silver_transform/silver_qct_dda.py` | QCT/DDA -> silver |
| `ingest_scripts/silver_transform/silver_ami.py` | AMI -> silver |

### Bootstrap (one-time)
| Script | Purpose |
|--------|---------|
| `ingest_scripts/bootstrap/bootstrap_geography.py` | Creates all State, County, CensusTract, MetroArea nodes + geographic hierarchy edges |

### Neo4j Ingest
| Script | Purpose |
|--------|---------|
| `ingest_scripts/neo4j_ingest/ingest_datasets.py` | Main ingest: QCT, DDA, AMI, HMDA all years via LOAD CSV |
| `ingest_scripts/neo4j_ingest/fix_edges.py` | One-time fixes: OMB crosswalk, HAS_STATE_AMI, SDDA edges, HMDA->MetroArea |

### Gap Fixes (one-time patches)
| Script | Purpose |
|--------|---------|
| `scripts/fix_sdda_gaps.py` | Adds SDDA->MetroArea edges for real MSAs; adds Dayton (19380) + Prescott (39140) MetroArea nodes |
| `scripts/fix_standalone.py` | Connects 138 orphan CensusTract (IN_COUNTY/IN_STATE), 95,568 NCNTY Section8AMILimit (HAS_MSA_AMI from County), 15,993 non-metro LenderBehaviorRisk (APPLIES_TO County) |

### Audit Scripts
| Script | Purpose |
|--------|---------|
| `scripts/audit_schema.py` | Verifies all 13 schema relationships exist with counts |
| `scripts/audit_full.py` | Full node + edge count audit across all labels |
| `scripts/audit_standalone.py` | Counts nodes with no relationships per label |

---

## 10. Final Audit

*Verified: June 2026*

### Schema relationship check (all 13 confirmed present)

| Relationship | From | To | Count | Status |
|-------------|------|----|------:|--------|
| IN_STATE | County | State | 3,251 | OK |
| IN_STATE | MetroArea | State | 441 | OK |
| IN_COUNTY | CensusTract | County | 111,597 | OK |
| IN_METRO | CensusTract | MetroArea | 85,000 | OK |
| IN_METRO | County | MetroArea | 3,194 | OK |
| APPLIES_TO | QCTDesignation | CensusTract | 1,657,416 | OK |
| APPLIES_TO | SDDADesignation | MetroArea | 1,846 | OK |
| APPLIES_TO | NMDDADesignation | County | 1,726 | OK |
| APPLIES_TO | StateAMILimit | State | 2,412 | OK |
| APPLIES_TO | LenderBehaviorRisk | MetroArea | 9,780 | OK |
| APPLIES_TO | LenderBehaviorRisk | County | 15,993 | OK |
| HAS_MSA_AMI | County | Section8AMILimit | 154,785 | OK |
| HAS_STATE_AMI | County | StateAMILimit | 95,865 | OK |

### Standalone node audit

| Label | Total | Standalone | % | Notes |
|-------|------:|----------:|--:|-------|
| State | 56 | 0 | 0% | Clean |
| County | 3,251 | 0 | 0% | Clean |
| CensusTract | 111,603 | 0 | 0% | Clean (138 CT planning regions fixed) |
| MetroArea | 443 | 0 | 0% | Clean |
| QCTDesignation | 1,657,416 | 0 | 0% | Clean |
| StateAMILimit | 2,412 | 0 | 0% | Clean |
| SDDADesignation | 1,880 | 34 | 1.8% | PR + county-level FMR areas (unfixable) |
| NMDDADesignation | 3,174 | 1,448 | 45.6% | HUD source gap 2017–2020 (unfixable) |
| Section8AMILimit | 124,425 | 915 | 0.7% | Territories + special areas (unfixable) |
| LenderBehaviorRisk | 25,831 | 58 | 0.2% | msa_code=NA at source (unfixable) |
| SpecialProgramLimit | 124,425 | 124,425 | 100% | By design — no edges intended |
