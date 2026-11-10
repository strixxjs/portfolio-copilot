"""Serializable corpus model: the single output of build_corpus(seed)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class ChunkRow(BaseModel):
    id: str
    seq: int
    section: str
    page: int
    paragraph: int
    content: str
    token_count: int


class DocumentRow(BaseModel):
    id: str
    project_id: str
    doc_type: str
    title: str
    doc_date: date
    author: str
    metadata: dict[str, str | int | bool]
    chunks: list[ChunkRow]


class ProjectRow(BaseModel):
    id: str
    name: str
    client: str
    sector: str
    country: str
    contract_value: Decimal
    currency: str
    start_date: date
    planned_end_date: date
    status: str


class ChangeOrderRow(BaseModel):
    id: str
    project_id: str
    number: int
    title: str
    reason_category: str
    amount: Decimal
    status: str
    submitted_on: date
    decided_on: date | None
    source_doc_id: str


class BudgetLineRow(BaseModel):
    project_id: str
    cost_code: str
    description: str
    category: str
    budget_amount: Decimal
    committed_amount: Decimal
    actual_amount: Decimal


class MilestoneRow(BaseModel):
    project_id: str
    name: str
    planned_date: date
    forecast_date: date
    actual_date: date | None
    status: str
    critical: bool


class UserRow(BaseModel):
    id: UUID
    email: str
    display_name: str
    groups: list[str]


class GroupRow(BaseModel):
    id: UUID
    name: str


class AclRow(BaseModel):
    object_id: str
    principal: str  # group name


class Corpus(BaseModel):
    seed: int
    synthetic: bool = True
    projects: list[ProjectRow]
    documents: list[DocumentRow]
    change_orders: list[ChangeOrderRow]
    budget_lines: list[BudgetLineRow]
    milestones: list[MilestoneRow]
    users: list[UserRow]
    groups: list[GroupRow]
    document_acl: list[AclRow]
    project_acl: list[AclRow]
    canaries: dict[str, str]
