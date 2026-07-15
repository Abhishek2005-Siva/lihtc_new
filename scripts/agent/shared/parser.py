"""Structured parsing helpers for model responses."""
from __future__ import annotations

import json
import re
from typing import Any


def _sanitize(text: str) -> str:
    """Strip markdown fences and replace bare control chars that break JSON parsing."""
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text


def _repair(text: str) -> str:
    """Best-effort repair of common LLM JSON malformations.

    Handles two failure modes that strict and non-strict json.loads both reject:

    1. Missing commas between object properties — the LLM emits:
           "key1": "val1"
           "key2": "val2"
       instead of the correct comma-separated form.  We insert the missing
       comma whenever a closing token (", number, true, false, null, }, ])
       is immediately followed (ignoring whitespace) by a new key (").

    2. Literal newlines inside string values — the LLM writes a multi-line
       Cypher query directly in the JSON without escaping the newlines as \\n.
       We escape them so json.loads can read the value correctly.
    """
    # Pass 1 — escape literal newlines that appear inside JSON string values.
    # Walk the string char-by-char so we only touch newlines that are inside "...".
    chars: list[str] = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            chars.append(ch)
            continue
        if ch == "\\" and in_string:
            escape_next = True
            chars.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            chars.append(ch)
            continue
        if in_string and ch == "\n":
            chars.append("\\n")
            continue
        if in_string and ch == "\r":
            chars.append("\\r")
            continue
        chars.append(ch)
    text = "".join(chars)

    # Pass 2 — insert missing commas between object properties / array elements.
    # Match: a closing token at end-of-token, optional whitespace, then a " that
    # starts the next key or string value.
    text = re.sub(
        r'(["\d]|true|false|null|[}\]])'   # closing token
        r'(\s+)'                            # whitespace (newline / spaces)
        r'(?=")',                           # followed by opening " (next key)
        r'\1,\2',
        text,
    )
    # Same for } or ] followed by another { or [
    text = re.sub(
        r'([}\]])'
        r'(\s+)'
        r'(?=[{\[])',
        r'\1,\2',
        text,
    )

    # Pass 3 — remove trailing commas before } or ] (some models add them)
    text = re.sub(r',\s*([}\]])', r'\1', text)

    return text


# Matches: designation_year="2025" or .year="2025" (incorrectly quoted integer year)
_QUOTED_YEAR_RE = re.compile(
    r'((?:designation_year|\.year)\s*[=:]\s*)"(\d{4})"',
    re.IGNORECASE,
)

# Param keys whose values should always be integers (graph stores them as INTEGER)
_INT_YEAR_PARAMS = {
    "year", "designation_year", "qct_year", "sdda_year", "nmdda_year", "ami_year",
}


def _fix_cypher_field(data: dict[str, Any]) -> dict[str, Any]:
    """Post-process parsed JSON to fix common LLM Cypher encoding issues."""
    if "cypher" in data and isinstance(data["cypher"], str):
        cypher = data["cypher"]
        cypher = cypher.replace("\\n", "\n")
        cypher = re.sub(r'\bfips_code\s*:\s*"value"', 'fips_code: $fips', cypher)
        cypher = re.sub(r'designation_year\s*=\s*"value"', 'designation_year = $year', cypher)
        cypher = re.sub(r'designation_year\s*:\s*"value"', 'designation_year: $year', cypher)
        cypher = _QUOTED_YEAR_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}", cypher)
        data["cypher"] = cypher

    if "params" in data and isinstance(data["params"], dict):
        for key in list(data["params"]):
            if key in _INT_YEAR_PARAMS and isinstance(data["params"][key], str):
                raw = data["params"][key]
                if raw.isdigit():
                    data["params"][key] = int(raw)

    return data


def _extract_first_json_object(text: str) -> str:
    """Return the substring spanning the first complete {...} JSON object.

    Brace-counting stops at the matching closing brace, ignoring everything
    after it (trailing commentary, second JSON objects, etc.).
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("Unterminated JSON object in response")


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = _sanitize(text)

    # Attempt 1: standard parse of full text (fast path)
    try:
        return _fix_cypher_field(json.loads(cleaned))
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract only the first {...} — fixes "Extra data" / trailing commentary
    try:
        first_obj = _extract_first_json_object(cleaned)
        return _fix_cypher_field(json.loads(first_obj))
    except (ValueError, json.JSONDecodeError):
        pass

    # Attempt 3: repair then re-extract — fixes missing commas and unescaped newlines
    try:
        repaired = _repair(cleaned)
        first_obj = _extract_first_json_object(repaired)
        return _fix_cypher_field(json.loads(first_obj))
    except (ValueError, json.JSONDecodeError):
        pass

    # Attempt 4: repair + non-strict (allows remaining control chars in strings)
    try:
        repaired = _repair(cleaned)
        first_obj = _extract_first_json_object(repaired)
        return _fix_cypher_field(json.loads(first_obj, strict=False))
    except (ValueError, json.JSONDecodeError) as exc:
        raise json.JSONDecodeError(
            f"Could not parse LLM response as JSON after repair attempts: {exc}",
            cleaned, 0,
        ) from exc
