"""Golden set loading and ScriptedProvider programs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.gateway.providers.fake import ScriptAction, ScriptProgram


class Expect(BaseModel):
    route: str
    status: str
    recall_docs: list[str] = Field(default_factory=list)
    answer_contains_any: list[str] = Field(default_factory=list)
    sql_value: float | str | None = None
    must_not_cite_docs_matching: str | None = None
    canary_absent: bool = False


class GoldenQuestion(BaseModel):
    id: str
    category: str
    user: str
    question: str
    expect: Expect
    script: list[dict[str, Any]] = Field(default_factory=list)


class Golden(BaseModel):
    version: int
    users: dict[str, str]
    top_k: int
    questions: list[GoldenQuestion]


def load_golden(path: Path) -> Golden:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Golden(
        version=raw["version"],
        users=raw["settings"]["users"],
        top_k=raw["settings"]["top_k"],
        questions=[GoldenQuestion.model_validate(q) for q in raw["questions"]],
    )


def program_for(q: GoldenQuestion) -> ScriptProgram:
    project = None
    actions: list[ScriptAction] = []
    for step in q.script:
        if "call" in step:
            args = dict(step.get("args", {}))
            project = project or args.get("project_id")
            actions.append(ScriptAction(call=step["call"], args=args, sql=step.get("sql")))
        elif "final" in step:
            actions.append(ScriptAction(final=str(step["final"])))
    return ScriptProgram(route=q.expect.route, project_id=project, actions=actions)
