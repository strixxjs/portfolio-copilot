"""SSE encoding: one event per block with a per-request sequence id."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def encode(seq: int, event: BaseModel) -> dict[str, Any]:
    return {"id": str(seq), "event": getattr(event, "type", "message"), "data": event.model_dump_json()}
