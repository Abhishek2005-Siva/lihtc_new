import sys
sys.path.insert(0, "project")
from backend.llm.parser import parse_json_object

# Simulate what the PathPlanner returned — markdown with multiple JSON blocks
response = '''Based on the extracted parameters and the end goal, I will generate three ordered paths.

**Path 1: Most Direct (Fewest Steps)**
```json
{"id": "path_1", "description": "Search tracts", "steps": [], "feasibility": "high"}
```

**Selected Path and Selection Reason**
```json
{
  "end_goal": "site_selection",
  "paths": [
    {"id": "path_1", "description": "Direct county search", "steps": [{"tool": "search_tracts", "params": {"year": 2025, "county_fips": "48113", "is_qct_designated": true}, "depends_on": null}], "feasibility": "high"},
    {"id": "path_2", "description": "State-level fallback", "steps": [{"tool": "search_tracts", "params": {"year": 2025, "state_fips": "48", "is_qct_designated": true}, "depends_on": null}], "feasibility": "medium"},
    {"id": "path_3", "description": "Broad search no geo filter", "steps": [{"tool": "search_tracts", "params": {"year": 2025, "is_qct_designated": true}, "depends_on": null}], "feasibility": "low"}
  ],
  "selected_path": "path_1",
  "selection_reason": "Most specific — uses county_fips directly."
}
```'''

result = parse_json_object(response)
print("end_goal:", result.get("end_goal"))
print("paths found:", len(result.get("paths", [])))
print("selected_path:", result.get("selected_path"))
print("PASS" if result.get("end_goal") == "site_selection" else "FAIL")
