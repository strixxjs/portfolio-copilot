"""open_document_section: read a section (or outline + first section) of one visible document."""

from __future__ import annotations

from app.retrieval.navigation import SectionNotFound, open_section
from app.schemas.tools import OpenDocumentSectionInput, OpenDocumentSectionOutput
from app.tools.base import Tool, ToolContext, ToolError, ToolFailure, ToolResultSummary
from app.tools.search_documents import render_hits


class OpenDocumentSectionTool(Tool[OpenDocumentSectionInput, OpenDocumentSectionOutput]):
    name = "open_document_section"
    description = (
        "Open a section of one document, or its outline, to read surrounding context after a search hit. "
        "Returns chunks with citation ids."
    )
    input_model = OpenDocumentSectionInput
    output_model = OpenDocumentSectionOutput

    async def run(self, args: OpenDocumentSectionInput, ctx: ToolContext) -> OpenDocumentSectionOutput:
        try:
            out = await open_section(ctx.db, ctx.principal, args)
        except SectionNotFound as e:
            raise ToolFailure(
                ToolError(
                    code="not_found",
                    message=f"section {e.section!r} not found in {e.doc_id}; available sections: "
                    + ", ".join(e.sections),
                    details={"sections": e.sections},
                    retryable=True,
                )
            ) from e
        if out is None:
            # Identical wording for "does not exist" and "no access": no existence leak.
            raise ToolFailure(
                ToolError(code="not_found", message=f"document or section not found: {args.doc_id}", retryable=False)
            )
        return out

    def render_for_model(self, out: OpenDocumentSectionOutput, source_label: str | None) -> str:
        outline = f'<outline doc="{out.doc_id}">{" | ".join(out.sections)}</outline>\n'
        return outline + render_hits(self.name, out.chunks)

    def summarize(self, out: OpenDocumentSectionOutput) -> ToolResultSummary:
        return ToolResultSummary(
            summary=f"opened {out.doc_id} ({len(out.chunks)} chunks; sections: {', '.join(out.sections)})",
            hits=[
                {
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "title": c.title,
                    "section": c.section,
                    "score": 0.0,
                }
                for c in out.chunks
            ],
        )
