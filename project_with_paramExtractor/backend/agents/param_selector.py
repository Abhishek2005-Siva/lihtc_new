"""Semantic parameter selector.

Instead of dumping all ~88 catalog parameters into the ParamExtractor prompt,
embed the user's question and every parameter (name + description), then keep only
the parameters whose cosine similarity clears a threshold — unioned with a small
always-include core so geography and pipeline-control params are never dropped.

Param embeddings are computed once and cached to disk keyed by a content hash, so
only the query is embedded at runtime.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

_REF_PATH   = Path(__file__).resolve().parents[3] / "data" / "param_reference.json"
_CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "param_embeddings.json"

# Params that must ALWAYS reach the extractor regardless of similarity score.
# Geography + time + the pipeline/filter control params the downstream stages need.
_ALWAYS_INCLUDE = {
    "fips_code", "county_fips", "state_fips", "cbsa_code",
    "year", "designation_year", "start_year", "end_year",
    "program_type", "household_size",
    "is_qct_designated", "is_dda_designated", "min_<attr>", "max_<attr>",
    "end_goal", "query_mode", "limit",
    "clarification_needed", "clarification_question",
}

_THRESHOLD = 0.35   # cosine sim floor for a semantic match
_MAX_MATCHES = 30   # hard cap on semantic matches (safety against a flat query)


def _param_text(p: dict) -> str:
    """The text embedded for a parameter: name + node + description + values."""
    parts = [p["name"], p.get("node", ""), p.get("description", "")]
    if p.get("values"):
        parts.append("values: " + ", ".join(str(v) for v in p["values"]))
    return " | ".join(x for x in parts if x)


def _format_line(p: dict) -> str:
    name = p["name"]; typ = p["type"]; node = p.get("node", "")
    desc = p.get("description", ""); vals = p.get("values"); ex = p.get("example", "")
    detail = desc
    if vals:
        detail += f" Values: {vals}."
    if ex != "":
        detail += f" e.g. {ex!r}"
    node_tag = f" [{node}]" if node else ""
    return f"  {name:<40} {typ}{node_tag} -- {detail}"


class ParamSelector:
    def __init__(self, llm_client, threshold: float = _THRESHOLD) -> None:
        self.llm = llm_client
        self.threshold = threshold
        self.params: list[dict] = json.loads(_REF_PATH.read_text(encoding="utf-8"))["parameters"]
        self._model = getattr(llm_client, "_EMBED_MODEL", "unknown")
        self._hash = self._content_hash()
        self._matrix: np.ndarray | None = None   # (n_params, dim), L2-normalized

    # ── Embedding cache ────────────────────────────────────────────────────────

    def _content_hash(self) -> str:
        # Key on the embedding model too — different models produce incompatible vectors.
        blob = self._model + "\n" + "".join(_param_text(p) for p in self.params)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def _load_cache(self) -> np.ndarray | None:
        if not _CACHE_PATH.exists():
            return None
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        if data.get("hash") != self._hash:
            return None  # schema changed — recompute
        vecs = np.array(data["vectors"], dtype=np.float32)
        if vecs.shape[0] != len(self.params):
            return None
        return vecs

    def _save_cache(self, vecs: np.ndarray) -> None:
        try:
            _CACHE_PATH.write_text(
                json.dumps({"hash": self._hash, "vectors": vecs.tolist()}),
                encoding="utf-8",
            )
        except Exception:
            pass  # cache is an optimization; never fatal

    def _ensure_matrix(self) -> np.ndarray:
        if self._matrix is not None:
            return self._matrix
        vecs = self._load_cache()
        if vecs is None:
            raw = self.llm.embed([_param_text(p) for p in self.params], input_type="passage")
            vecs = _normalize(np.array(raw, dtype=np.float32))
            self._save_cache(vecs)
        else:
            vecs = _normalize(vecs)
        self._matrix = vecs
        return vecs

    # ── Selection ──────────────────────────────────────────────────────────────

    def select(self, question: str) -> list[dict]:
        """Return the parameters relevant to this question (always-include + matches)."""
        try:
            matrix = self._ensure_matrix()
            q_vec = _normalize(np.array(
                self.llm.embed([question], input_type="query"), dtype=np.float32
            ))[0]
            sims = matrix @ q_vec  # cosine (both normalized)
        except Exception:
            # Any embedding failure → fall back to the full catalog (safe, just larger).
            return self.params

        order = np.argsort(-sims)
        chosen_idx: list[int] = []
        for i in order:
            if sims[i] >= self.threshold and len(chosen_idx) < _MAX_MATCHES:
                chosen_idx.append(int(i))

        chosen = {self.params[i]["name"] for i in chosen_idx}
        selected = [
            p for p in self.params
            if p["name"] in _ALWAYS_INCLUDE or p["name"] in chosen
        ]
        return selected

    def build_block(self, question: str) -> str:
        """Format the selected parameters as the EXTRACTABLE PARAMETERS block."""
        return "\n".join(_format_line(p) for p in self.select(question))


def _normalize(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return v / norms
