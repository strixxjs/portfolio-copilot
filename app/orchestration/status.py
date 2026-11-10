"""AnswerStatus decision rules, deterministic and in this order (plan 4.4)."""

from __future__ import annotations

from app.schemas.common import AnswerStatus, Citation
from app.schemas.events import DoneFlags


def decide_status(final_text: str, citations: list[Citation], flags: DoneFlags, any_data: bool) -> AnswerStatus:
    if final_text.lstrip().startswith("NOT_FOUND:"):
        return AnswerStatus.NOT_FOUND
    if flags.steps_exhausted or flags.budget_exhausted:
        return AnswerStatus.PARTIAL
    if any(c.valid for c in citations):
        return AnswerStatus.ANSWERED
    if any_data:
        return AnswerStatus.PARTIAL
    return AnswerStatus.NOT_FOUND
