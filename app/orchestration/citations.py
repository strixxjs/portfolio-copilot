"""Post-hoc citation validation and canary stripping. Deterministic; no LLM judge."""

from __future__ import annotations

import re

from app.schemas.common import Citation

CITATION = re.compile(r"\[((?:P[0-9]+-[A-Z]+-[0-9]{3}#[0-9]+)|(?:sql#[0-9]+))\]")


def _citation(key: str, valid: bool) -> Citation:
    if key.startswith("sql#"):
        return Citation(source_id=key, kind="sql", valid=valid)
    doc_id, seq = key.split("#")
    return Citation(source_id=key, kind="chunk", doc_id=doc_id, seq=int(seq), valid=valid)


def validate_citations(text: str, valid_ids: set[str]) -> tuple[str, list[Citation], int]:
    seen: dict[str, Citation] = {}
    removed = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal removed
        key = m.group(1)
        ok = key in valid_ids
        seen.setdefault(key, _citation(key, ok))
        if ok:
            return m.group(0)
        removed += 1
        return ""

    clean = CITATION.sub(repl, text)
    clean = re.sub(r"[ \t]{2,}", " ", clean).replace(" .", ".").strip()
    ordered = [c for c in seen.values() if c.valid] + [c for c in seen.values() if not c.valid]
    return clean, ordered, removed


def strip_canaries(text: str, pattern: str) -> tuple[str, bool]:
    rx = re.compile(pattern)
    found = bool(rx.search(text))
    return (re.sub(r"[ \t]{2,}", " ", rx.sub("", text)).strip(), found)
