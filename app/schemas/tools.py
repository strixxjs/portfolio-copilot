"""Tool input/output contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ChunkHit, DocType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchDocumentsInput(StrictModel):
    query: str = Field(min_length=2, max_length=500, description="Natural-language or keyword query")
    project_id: str | None = Field(
        default=None, pattern=r"^P[0-9]+$", description="Restrict to one project code, e.g. P1"
    )
    doc_types: list[DocType] | None = Field(default=None, description="Restrict to document types")
    top_k: int = Field(default=8, ge=1, le=20)


class SearchScope(BaseModel):
    query: str
    project_id: str | None
    doc_types: list[DocType] | None
    k_fts: int
    k_vec: int
    reranked: bool
    candidates: int = Field(description="Distinct chunks after RRF fusion, before the cut to top_k")


class SearchDocumentsOutput(BaseModel):
    hits: list[ChunkHit]
    scope: SearchScope
    retrieval_ms: int
    rerank_ms: int


class OpenDocumentSectionInput(StrictModel):
    doc_id: str = Field(pattern=r"^P[0-9]+-[A-Z]+-[0-9]{3}$", description="Document id, e.g. P1-RFI-003")
    section: str | None = Field(
        default=None, max_length=100, description="Section to open; omit for the outline and first section"
    )
    chunk_seq: int | None = Field(
        default=None, ge=0, description="Open the chunk with this seq plus `window` neighbours on each side"
    )
    window: int = Field(default=1, ge=0, le=5)


class OpenDocumentSectionOutput(BaseModel):
    doc_id: str
    title: str
    doc_type: DocType
    sections: list[str] = Field(description="All section names of the document, in order")
    chunks: list[ChunkHit]


class QueryProjectDataInput(StrictModel):
    question: str = Field(min_length=3, max_length=500, description="The data question in natural language")
    project_id: str | None = Field(default=None, pattern=r"^P[0-9]+$")


class QueryProjectDataOutput(BaseModel):
    sql: str = Field(description="The validated SQL that was executed")
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    generation_attempts: int
    execution_ms: int
