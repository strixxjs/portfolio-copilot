"""search_documents: permission-filtered hybrid search over the corpus."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from app.retrieval.hybrid import hybrid_search
from app.schemas.common import ChunkHit
from app.schemas.tools import SearchDocumentsInput, SearchDocumentsOutput
from app.tools.base import Tool, ToolContext, ToolResultSummary

if TYPE_CHECKING:
    from app.retrieval.embedder import Embedder
    from app.retrieval.reranker import Reranker


def render_hits(tool: str, hits: list[ChunkHit]) -> str:
    parts = [f'<tool_result tool="{tool}" hits="{len(hits)}">']
    for h in hits:
        parts.append(
            f'<document id="{h.chunk_id}" doc="{h.doc_id}" type="{h.doc_type.value}" title="{escape(h.title)}" '
            f'section="{escape(h.section)}" date="{h.doc_date.isoformat()}">\n'
            f"{escape(h.content, quote=False)}\n</document>"
        )
    parts.append("</tool_result>")
    return "\n".join(parts)


class SearchDocumentsTool(Tool[SearchDocumentsInput, SearchDocumentsOutput]):
    name = "search_documents"
    description = (
        "Hybrid search (keyword + semantic) over construction project documents the current user may read. "
        "Returns ranked chunks with citation ids. Use specific terms (ids like P1-RFI-003, locations, materials)."
    )
    input_model = SearchDocumentsInput
    output_model = SearchDocumentsOutput

    def __init__(self, embedder: Embedder, reranker: Reranker | None) -> None:
        self.embedder = embedder
        self.reranker = reranker

    async def run(self, args: SearchDocumentsInput, ctx: ToolContext) -> SearchDocumentsOutput:
        return await hybrid_search(ctx.db, self.embedder, self.reranker, ctx.settings, ctx.principal, args, ctx.tracer)

    def render_for_model(self, out: SearchDocumentsOutput, source_label: str | None) -> str:
        return render_hits(self.name, out.hits)

    def summarize(self, out: SearchDocumentsOutput) -> ToolResultSummary:
        return ToolResultSummary(
            summary=f"{len(out.hits)} hits from {out.scope.candidates} candidates in {out.retrieval_ms} ms"
            + (f" (+{out.rerank_ms} ms rerank)" if out.scope.reranked else ""),
            hits=[
                {
                    "chunk_id": h.chunk_id,
                    "doc_id": h.doc_id,
                    "title": h.title,
                    "section": h.section,
                    "score": round(h.score, 4),
                }
                for h in out.hits
            ],
        )
