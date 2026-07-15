# LIHTC KG-RAG Agent — Build Log

## Architecture Overview

7-layer pipeline. Layers 0–2 build the Neo4j graph. Layers 3–6 are the agent.

```
Layer 0  — Raw data ingestion
Layer 1  — Bronze (cleaning)
Layer 2  — Silver/Gold (graph build)
Layer 3  — Stage 0: Normalizer
Layer 4  — Stage 1: Parser / Decomposer  (LLM Call 1)
Layer 5  — Stage 4: Executor  (parallel tool rounds)
           LLM Call 2: Gap Handler  (soft nulls only)
Layer 6  — Stage 5: Synthesizer  (LLM Call 3)
```

### Files

| File | Role |
|------|------|
| `scripts/agent/config.py` | API keys, model constants, LLM wrappers |
| `scripts/agent/normalizer.py` | Stage 0 — BHK/abbreviation/time normalization |
| `scripts/agent/fast_path.py` | Stage 1 fast path — skip LLM for simple patterns |
| `scripts/agent/parser.py` | LLM Call 1 — parse question into sub-goal plan |
| `scripts/agent/context.py` | ConversationContext — cross-turn state |
| `scripts/agent/executor.py` | Stage 4 — parallel tool execution |
| `scripts/agent/tools.py` | Tool functions + dispatch registry |
| `scripts/agent/cypher_library.py` | All Cypher queries |
| `scripts/agent/gap_handler.py` | LLM Call 2 — soft null interpretation |
| `scripts/agent/synthesizer.py` | LLM Call 3 — answer generation |
| `scripts/agent/app.py` | Streamlit UI |

### Neo4j Node Types

`CensusTract`, `County`, `State`, `MetroArea`, `QCTDesignation`, `SDDADesignation`,
`NMDDADesignation`, `Section8AMILimit`, `LenderBehaviorRisk`

### Tool Registry

| Tool | Mode | Purpose |
|------|------|---------|
| `get_tract_context` | lookup | Geographic hierarchy for one tract |
| `get_qct_status` | lookup | QCT designation for one tract/year |
| `get_dda_status` | lookup | DDA designation (SDDA + NMDDA paths) |
| `get_ami_limits` | lookup | Income limits + max rent for all household sizes |
| `get_basis_boost` | lookup | Combined QCT+DDA boost eligibility |
| `get_hmda_risk` | lookup | Fair lending risk — single year |
| `get_hmda_risk_trend` | trend | Fair lending risk — year range |
| `search_qct_tracts` | search | QCT tracts matching filters |
| `search_dda_areas` | search | DDA areas matching filters |
| `search_hmda_risk` | search | High-risk HMDA markets matching filters |

---

## Performance Fixes

### Fix 1 — Model split (fast vs slow)
**File:** `config.py`  
**Problem:** All LLM calls used the 70B model, wasting 1.5–3s on the parser where quality doesn't matter.  
**Fix:** Added `FAST_MODEL = meta/llama-3.1-8b-instruct` and `SLOW_MODEL = meta/llama-3.1-70b-instruct`. LLM Call 1 (parser) and Call 2 (gap handler) use `fast=True`. LLM Call 3 (synthesizer) uses the 70B.  
**Added:** `llm(fast=True/False)` selects model + temperature. `llm_stream()` generator for streaming synthesizer output.

### Fix 2 — Parallel execution
**File:** `executor.py`  
**Status:** Already implemented via `ThreadPoolExecutor`. Sub-goals within a dependency round run in parallel. No changes needed.

### Fix 3 — Session-state tool cache
**Files:** `tools.py`, `app.py`  
**Problem:** Repeated questions about the same tract re-queried Neo4j every time.  
**Fix:** `set_cache(store)` wires a dict from `st.session_state.tool_cache` into the tools module. `call_tool` reads/writes by key `tool_name|param1=val1|...`. Search tools (`search_qct_tracts`, `search_dda_areas`, `search_hmda_risk`) are never cached. Cache is flushed on tract change and on "Clear chat". A dedicated "Clear tool cache" button added to sidebar.

### Fix 4 — Streaming synthesizer
**Files:** `config.py`, `synthesizer.py`, `app.py`  
**Problem:** Synthesizer blocked for 3–6s with no visible feedback.  
**Fix:** `llm_stream()` yields chunks via OpenAI streaming API. `synthesize(..., stream_callback=fn)` calls `fn(chunk)` per token. `app.py` updates a `st.empty()` placeholder live, then clears it and renders the structured answer sections.

### Fix 5 — Fast path pattern matching
**File:** `fast_path.py` (new)  
**Problem:** Every question — even "is this a QCT?" — paid the full LLM Call 1 latency.  
**Fix:** `try_fast_path(question)` matches 8 regex patterns (basis boost, AMI limits, max rent, QCT, DDA, HMDA risk, full profile, market viability). Confidence threshold 0.90 — only fires on high-confidence matches. Returns a `FastPathResult` with `matched`, `end_goal`, `tools`, `confidence`, and `fips_in_text` (see bug fix below).  
**Exclusions:** Definitional questions ("explain what a QCT is", "what is a DDA?") and trend questions ("getting better/worse", "from 2018 to 2024") are excluded so they fall through to the LLM.

### Fix 6 — Compact parser prompt
**File:** `parser.py`  
**Problem:** `json.dumps(TOOLS)` injected ~5,500 chars of verbose JSON into every LLM Call 1.  
**Fix:** `_compact_tools()` renders each tool as 3 readable lines (name+params, RETURNS, NOT). `_compact_params()` only includes the ~11 params the LLM actually needs to extract. Total system prompt: ~10,800 chars vs ~13,000 before.

### Fix 7 — Pre-formatted synthesizer context
**File:** `synthesizer.py`  
**Problem:** Synthesizer received raw nested JSON (~60% noise). Model had to parse structure before reasoning.  
**Fix:** `_fmt_tool_data()` converts each tool result into labeled readable text. AMI limits show `$X → max rent $Y/mo` per household size. HMDA trend shows year-by-year rows. Reduces synthesizer context by ~60% and eliminates JSON parsing overhead.

---

## Bug Fixes

### Bug — `is_designated = true` comparison always false
**File:** `cypher_library.py`  
**Problem:** `q_basis_boost` and `q_dda_status` used `WHERE x.is_designated = true`. Neo4j stores the field as integer `0`/`1`, not boolean. Strict type comparison always returns false → basis boost always showed "not eligible" even for designated tracts.  
**Fix:** Changed to `COALESCE(sd.is_designated, 0) = 1` and `COALESCE(nd.is_designated, 0) = 1` throughout both queries.

### Bug — Cypher query log always empty
**File:** `cypher_library.py`, `tools.py`  
**Problem:** Used `threading.local()` to collect executed Cypher queries. Thread-local storage doesn't reliably persist across Streamlit reruns and `ThreadPoolExecutor` worker threads — the log was empty every time.  
**Fix:** Removed all thread-local storage. Each tool function creates `log: list = []` locally, passes it as `_log=log` through every `_run()` call, and returns it in the result dict as `cypher_log`. The UI reads from `result["cypher_log"]` directly.

### Bug — Chat history losing parse content on rerun
**File:** `app.py`  
**Problem:** Parse result was only stored in history on clarification turns. Synthesis was stored as a plain markdown string. On the next question, the previous turn collapsed to just its final answer.  
**Fix:** Parse result is always appended to `st.session_state.messages` immediately after parsing. Synthesis is stored as a structured dict `{direct_answer, evidence, caveats, conclusion}`. `_render_synthesis_msg()` replays the full tabbed layout from history.

### Bug — Markdown tables broken in Direct Answer
**File:** `app.py`  
**Problem:** HTML `<div>` container stripped markdown table syntax — tables rendered as plain text.  
**Fix:** Replaced `st.markdown(html_div)` with `st.container(border=True)` + `st.markdown(text)`. Native Streamlit container preserves markdown rendering.

### Bug — `bedroom_count` causing 8 sub-goals
**Files:** `tools.py`, `parser.py`  
**Problem:** Parser decomposed "all bedroom sizes" into 8 separate `get_ami_limits` calls, one per bedroom count. This happened because `bedroom_count` was a parameter, and the LLM mapped each size to a separate sub-goal.  
**Root cause:** `Section8AMILimit` is wide-format — all 8 household sizes are columns on a single row, not separate rows. One query returns everything.  
**Fix:** Removed `bedroom_count` from `get_ami_limits` entirely. Tool always returns all 8 sizes. Added explicit CRITICAL rule and wrong/correct plan examples to `DECOMPOSER_RULES` so the LLM never creates multiple calls.

### Bug — Fast path ignores FIPS in question text
**File:** `fast_path.py`, `app.py`  
**Problem:** "Which state does tract 01101001800 belong to and what's the AMI limit?" — fast path fired on "AMI limit", but `build_fast_parse_result` only read `ctx.fips_code` from conversation context (None on first turn). Result: clarification prompt asking for a FIPS code that was right there in the question.  
**Fix:** `try_fast_path()` now runs `_FIPS_RE = re.compile(r"\b(\d{11})\b")` against the question text and returns `fips_in_text` on `FastPathResult`. `app.py` uses `fp.fips_in_text or ctx.fips_code`.

### Bug — Fast path fires for HMDA trend questions
**File:** `fast_path.py`  
**Problem:** "Has fair lending risk been getting better or worse from 2018 to 2024?" matched the `hmda_risk` fast path pattern. Fast path built a plan with `get_hmda_risk` (single year, LIMIT 1) instead of `get_hmda_risk_trend` (year range, no limit). Result: only one year returned instead of the full series.  
**Fix:** Added `exclude` pattern to `hmda_risk` fast path entry covering all trend keywords: `getting better/worse`, `over time`, `history/historical`, `improve/worsen/change`, `from YYYY`, `YYYY to YYYY`, `past N years`, `years`. These fall through to the LLM which routes to `get_hmda_risk_trend`.

### Bug — Streaming synthesizer fails when fast path is used
**File:** `config.py`  
**Problem:** Fast path skips LLM Call 1, so `get_nvidia_client()` was never called before the synthesizer. `_nvidia_client` singleton was None when `llm_stream()` fired. The client was built using `NVIDIA_API_KEY` — a module-level constant frozen at import time, before `app.py`'s sidebar sets `os.environ["NVIDIA_API_KEY"]`. Result: `OpenAIError: Missing credentials`.  
**Fix:** `get_nvidia_client()` now reads `os.environ.get("NVIDIA_API_KEY")` at call time and tracks `_nvidia_client_key`. If the key has changed since the client was built, the client is rebuilt. This also handles mid-session key rotation.

---

## HMDA Multi-MD Problem (Dallas MSA)

### Problem 1 — Ranges instead of single values
**Symptom:** Every cell in the HMDA trend table showed a range like `0.31–0.56` instead of one number.  
**Root cause:** Dallas CBSA 19100 has 11 Metropolitan Division (MD) codes. `LenderBehaviorRisk` has one node per MD per year, all connected to the same `MetroArea` node. The query returned all 11 rows per year; the synthesizer min-maxed them.  
**Fix:**  
- Cypher: `ORDER BY r.assessment_year ASC, r.total_applications DESC` — largest MD first within each year.  
- Tool: deduplicate by taking the first row per year (highest `total_applications` = most representative MD).  
- Same fix applied to `q_hmda_risk` (single-year lookup): added `ORDER BY r.total_applications DESC` before `LIMIT 1`.

### Problem 2 — Disparity ratio NULL for 2018–2021
**Symptom:** Disparity ratio showed N/A for early years.  
**Root cause:** Two cases: (a) `denial_rate_white = NULL` — sample too small to compute; (b) `denial_rate_white = 0.0` — no white applicants denied that year, making the ratio mathematically undefined. The Silver pipeline left the field NULL in both cases.  
**Fix:** `_derive_disparity(row)` in `tools.py`:  
- If stored ratio is NULL and both minority/white rates exist and white > 0: compute `round(minority / white, 4)`.  
- If white = 0 and minority > 0: set `disparity_note = "White denial rate = 0% while minority denial rate = X%. Ratio undefined."` — synthesizer quotes this directly.  
- Applied to both `get_hmda_risk` and `get_hmda_risk_trend`.

### Problem 3 — "Mixed trends" non-answer
**Symptom:** Synthesizer said "mixed trends with some years showing improvement and others deterioration" — useless to an underwriter who asked "should I be concerned?"  
**Root cause:** Synthesizer rules didn't define CFPB thresholds, direction-reading logic, or what a concrete recommendation looks like.  
**Fix:** Updated `_RULES["lending_risk"]` in `synthesizer.py` with:  
- CFPB thresholds: < 1.0 favorable; 1.0–1.5 mild; 1.5–2.0 elevated; > 2.0 high risk.  
- Explicit instruction to identify threshold crossings (1.0, 2.0).  
- Required output: direction (improving/worsening), which years, concrete recommendation (not a deal blocker / flag for credit memo / serious concern).  
- Banned "mixed trends" without specifying which years.

---

## HMDA Trend Tool (New)

### `get_hmda_risk_trend(cbsa_code, start_year, end_year)`
**Why added:** The existing `get_hmda_risk(fips_code, year)` is a single-year point-in-time lookup. For questions about change over time ("getting better or worse from 2018 to 2024"), the decomposer was calling `get_hmda_risk` once per year — generating 7 sub-goals and 7 Cypher queries with LIMIT 1 each.  
**Implementation:**  
- `cypher_library.py`: `q_hmda_risk_trend()` — matches by `cbsa_code` (not `fips_code`), year range string comparison, returns all rows ordered by year ASC / total_applications DESC. No LIMIT.  
- `tools.py`: deduplication to one row per year, `_derive_disparity()` applied to each row.  
- `parser.py`: added to `TOOLS` list with explicit routing rule — any trend/time-range question must use this tool via `dep_type="data"` on `get_tract_context` to obtain `cbsa_code`.  
- `fast_path.py`: HMDA fast path excludes trend keywords so they always reach the LLM.  
- `synthesizer.py`: `_fmt_tool_data()` formats the trend list year-by-year; `_RULES["lending_risk"]` updated with direction-reading and threshold rules.

**Decomposer routing rule added to `parser.py`:**  
```
get_hmda_risk(fips_code, year)               → SINGLE year, LIMIT 1
get_hmda_risk_trend(cbsa_code, start, end)   → YEAR RANGE, no limit

For trend questions:
  sg1: get_tract_context(fips_code)
  sg2: get_hmda_risk_trend(cbsa_code=$sg1.cbsa_code, start_year=X, end_year=Y)
       dep_type="data" on sg1
```

---

## LIHTC Max Rent Fix (IRC §42)

### Problem — Different rents per bedroom size
**Symptom:** Agent returned $941/mo for 2BR and $1,129/mo for 3BR (different values).  
**Root cause:** Tool computed `max_rents_by_size` as `limit_Nperson × 0.30 / 12` for each N from 1–8. The synthesizer received a table of 8 different rents and presented them per bedroom. This used bedroom_count + 1 as household size — wrong.  
**Correct formula (IRC §42):** Max gross rent always = `limit_4person × 0.30 / 12`. The 4-person limit is the anchor regardless of bedroom count. All bedroom sizes share the same gross rent ceiling.  
**Verified:** Tract 01101001800, 2025, VLI → `limit_4person = $41,800` → **max rent = $1,045/mo** for all unit sizes.

### Fix — Tool layer
**File:** `tools.py` — `get_ami_limits()`  
- Removed `max_rents_by_size` dict (the per-bedroom table).  
- Added `max_rent_irc42 = round(limit_4person × 0.30 / 12)` — single pre-computed value.  
- Added `limit_4person` as explicit field.  
- Added `max_rent_basis = "limit_4person × 30% ÷ 12 (IRC §42(g)) — same for all bedroom sizes"`.  
- `limits_by_size` retained for tenant eligibility screening (not rent calculation).

### Fix — Synthesizer rules
**File:** `synthesizer.py` — `_RULES["max_rent"]`  
5 explicit rules added:  
1. Always use `max_rent_irc42` from tool data — do not recalculate.  
2. NEVER calculate different rents for different bedroom sizes.  
3. NEVER use `limit_Nperson` where N ≠ 4 for rent calculation.  
4. `limits_by_size` is for eligibility screening only.  
5. For "max rent for 2BR and 3BR" → one sentence, one number, state it applies to both.

### Fix — Synthesizer formatter
**File:** `synthesizer.py` — `_fmt_tool_data()`  
Removed the per-bedroom rent table from the formatted context. Now shows:  
```
IRC §42 max gross rent (ALL unit sizes): $1,045/mo
Basis: limit_4person × 30% ÷ 12 (IRC §42(g)) — same for all bedroom sizes
limit_4person = $41,800
Household income limits by size (for eligibility screening only):
  1-person: $29,300  ...
```

### Fix — Format hint
Removed "rents by bedroom" from the DIRECT ANSWER format guidance that was telling the model to produce a multi-row table.

---

## QCT Search Query Fix

### Problem — Redundant state join when county_fips is provided
**File:** `cypher_library.py` — `q_search_qct_tracts()`  
**Problem 1:** Always matched `(t)-[:IN_STATE]->(st:State)` even when `county_fips` was the only filter. An extra graph hop that adds latency and traverses nodes that contribute nothing — `county_fips = '48113'` uniquely identifies Dallas County, TX without needing to verify the state.  
**Problem 2:** `q.is_designated = true` — same int-vs-boolean bug as `q_basis_boost`. `QCTDesignation.is_designated` is stored as `0`/`1`, not boolean. Returned 0 rows for every county-level search.  
**Fix:** Split into two code paths:  
- **County path** (when `county_fips` provided): `MATCH ... -[:IN_COUNTY]->(c:County {county_fips: $county_fips})` — no state join at all.  
- **State path** (when only `state_abbr` provided): keeps the `IN_STATE` hop as before.  
- Both paths use `COALESCE(q.is_designated, 0) = 1` instead of `= true`.  
**Result:** Dallas County 2025, poverty > 25% → 48 tracts returned correctly (was 0 before fix).

---

## Architectural Decisions

### Tool layer fetches everything; synthesizer picks what's relevant
Established as a core principle. Applied to `get_ami_limits` (returns all 8 household sizes; synthesizer selects the one asked for) and all other tools.

### dep_type distinction
- `"sequencing"` — wait for deps to finish but run own Cypher independently. Deps go to synthesizer as peers, not as input params.
- `"data"` — needs a specific field from a dep's result as an input param. Executor extracts and injects it.
- `null` — no deps; runs in Round 1.

`get_basis_boost` always has `dep_type="sequencing"` on `get_qct_status` + `get_dda_status`.  
`get_hmda_risk_trend` has `dep_type="data"` on `get_tract_context` (needs `cbsa_code`).

### Hard null vs soft null
- **Hard null** — stop the entire pipeline (e.g., tract not found in graph).  
- **Soft null** — continue pipeline, fire gap handler (LLM Call 2) to interpret the missing data.

### Section8AMILimit is wide-format
All 8 household size columns (`limit_1person` through `limit_8person`) are on a single row, not separate rows. One query per `(fips, year, program_type)` returns everything. Never query per bedroom size.

### assessment_year is stored as STRING
`LenderBehaviorRisk.assessment_year` is a string in the graph. All Cypher queries must pass `str(year)` for equality comparisons. Range comparisons (`>=`, `<=`) work because string ordering matches numeric ordering for 4-digit years.

### MetroArea has no metro_name property
`MetroArea` nodes have: `cbsa_code`, `fmr_area_code`, `area_population`, `state_fips`. No `metro_name`. Use `fmr_area_code` (e.g., `METRO19100M19100`) as the area label.
