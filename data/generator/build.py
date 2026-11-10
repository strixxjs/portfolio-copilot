"""build_corpus(seed) -> Corpus. Pure and deterministic: same seed, byte-identical corpus.json."""

from __future__ import annotations

import json

from data.generator.entities import ProjectEntities, build_project
from data.generator.models import (
    BudgetLineRow,
    ChangeOrderRow,
    Corpus,
    MilestoneRow,
    ProjectRow,
)
from data.generator.poison import build_poisoned
from data.generator.principals import build_principals, document_acl, project_acl
from data.generator.projects import PROJECTS
from data.generator.render import render_project
from data.generator.seed_rng import make_rng


def build_corpus(seed: int) -> Corpus:
    rng = make_rng(seed)
    by_project: dict[str, ProjectEntities] = {p.id: build_project(rng, p) for p in PROJECTS}
    documents = [d for e in by_project.values() for d in render_project(e)]
    poisoned, canaries = build_poisoned(rng, seed, by_project)
    documents += poisoned
    documents.sort(key=lambda d: d.id)
    users, groups = build_principals()
    return Corpus(
        seed=seed,
        projects=[
            ProjectRow(
                id=p.id,
                name=p.name,
                client=p.client,
                sector=p.sector,
                country=p.country,
                contract_value=p.contract_value,
                currency=p.currency,
                start_date=p.start_date,
                planned_end_date=p.planned_end_date,
                status=p.status,
            )
            for p in PROJECTS
        ],
        documents=documents,
        change_orders=[
            ChangeOrderRow(
                id=c.id,
                project_id=c.project_id,
                number=c.number,
                title=c.title,
                reason_category=c.reason_category,
                amount=c.amount,
                status=c.status,
                submitted_on=c.submitted_on,
                decided_on=c.decided_on,
                source_doc_id=c.id,
            )
            for e in by_project.values()
            for c in e.change_orders
        ],
        budget_lines=[
            BudgetLineRow(
                project_id=b.project_id,
                cost_code=b.cost_code,
                description=b.description,
                category=b.category,
                budget_amount=b.budget_amount,
                committed_amount=b.committed_amount,
                actual_amount=b.actual_amount,
            )
            for e in by_project.values()
            for b in e.budget_lines
        ],
        milestones=[
            MilestoneRow(
                project_id=m.project_id,
                name=m.name,
                planned_date=m.planned_date,
                forecast_date=m.forecast_date,
                actual_date=m.actual_date,
                status=m.status,
                critical=m.critical,
            )
            for e in by_project.values()
            for m in e.milestones
        ],
        users=users,
        groups=groups,
        document_acl=document_acl(documents),
        project_acl=project_acl([p.id for p in PROJECTS]),
        canaries=canaries,
    )


def dump_corpus(corpus: Corpus) -> str:
    return json.dumps(corpus.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
