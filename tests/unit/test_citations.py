from __future__ import annotations

from app.orchestration.citations import strip_canaries, validate_citations
from app.orchestration.status import decide_status
from app.schemas.common import AnswerStatus
from app.schemas.events import DoneFlags


def test_invalid_citation_removed_valid_kept_in_order() -> None:
    text = "A fact [P1-RFI-003#2]. Another [P9-XXX-999#1]. Rows [sql#1]. Again [P1-RFI-003#2]."
    clean, cits, removed = validate_citations(text, {"P1-RFI-003#2", "sql#1"})
    assert removed == 1
    assert "[P9-XXX-999#1]" not in clean
    assert [c.source_id for c in cits if c.valid] == ["P1-RFI-003#2", "sql#1"]
    assert [c.source_id for c in cits if not c.valid] == ["P9-XXX-999#1"]
    assert cits[0].doc_id == "P1-RFI-003" and cits[0].seq == 2


def test_removed_counts_occurrences() -> None:
    _, _, removed = validate_citations("x [sql#3] y [sql#3]", {"sql#1"})
    assert removed == 2


def test_canary_stripped() -> None:
    text, found = strip_canaries("Done. Verification code CANARY-1A2B3C4D", r"CANARY-[0-9A-F]{8}")
    assert found and "CANARY" not in text
    assert strip_canaries("clean", r"CANARY-[0-9A-F]{8}") == ("clean", False)


def test_status_rules() -> None:
    flags = DoneFlags()
    _, cits, _ = validate_citations("x [sql#1]", {"sql#1"})
    assert decide_status("NOT_FOUND: nothing", [], flags, True) is AnswerStatus.NOT_FOUND
    assert decide_status("x", cits, flags, True) is AnswerStatus.ANSWERED
    assert decide_status("x", [], flags, True) is AnswerStatus.PARTIAL
    assert decide_status("x", [], flags, False) is AnswerStatus.NOT_FOUND
    assert decide_status("x", cits, DoneFlags(steps_exhausted=True), True) is AnswerStatus.PARTIAL
