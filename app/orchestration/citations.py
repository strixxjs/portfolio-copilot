"""Post-hoc citation validation and canary stripping. Deterministic; no LLM judge.

Models do not always copy keys byte-for-byte: GPT-OSS writes non-breaking hyphens (U+2011) inside ids,
uses fullwidth brackets 【…】, and groups keys as [A, B]. All of these are normalised to [A][B] before
validation; anything inside brackets that is not a list of citation keys is left untouched.
"""

from __future__ import annotations

import re

from app.schemas.common import Citation

KEY = re.compile(r"^(?:P[0-9]+-[A-Z]+-[0-9]{3}#[0-9]+|sql#[0-9]+)$")
BRACKET = re.compile(r"[\[【]([^\[\]【】\n]{1,300})[\]】]")
_SPLIT = re.compile(r"[,;]\s*|\s+")
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−﹣－"), "-")


def _keys(inner: str) -> list[str] | None:
    """The citation keys in one bracket, or None when the bracket is not a citation group."""
    tokens = [t.split("†")[0].strip() for t in _SPLIT.split(inner.translate(_DASHES).strip()) if t.strip()]
    if tokens and all(KEY.match(t) for t in tokens):
        return tokens
    return None


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
        keys = _keys(m.group(1))
        if keys is None:
            return m.group(0)
        kept: list[str] = []
        for key in keys:
            ok = key in valid_ids
            seen.setdefault(key, _citation(key, ok))
            if ok:
                kept.append(f"[{key}]")
            else:
                removed += 1
        return "".join(kept)

    clean = BRACKET.sub(repl, text)
    clean = re.sub(r"[ \t]{2,}", " ", clean).replace(" .", ".").strip()
    ordered = [c for c in seen.values() if c.valid] + [c for c in seen.values() if not c.valid]
    return clean, ordered, removed


def strip_canaries(text: str, pattern: str) -> tuple[str, bool]:
    rx = re.compile(pattern)
    found = bool(rx.search(text))
    return (re.sub(r"[ \t]{2,}", " ", rx.sub("", text)).strip(), found)
