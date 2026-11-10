from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.gateway.providers.fake import CannedProvider, fake_response
from app.gateway.types import ToolCall
from app.orchestration.loop import AgentLoop
from app.schemas.tools import SearchDocumentsInput, SearchDocumentsOutput, SearchScope
from app.tools.base import Tool, ToolContext, ToolRegistry, ToolResultSummary
from app.tools.search_documents import render_hits
from app.tracing.tracer import Tracer
from tests.conftest import hit, make_ctx, make_gateway, make_settings


class FakeSearch(Tool[SearchDocumentsInput, SearchDocumentsOutput]):
    name = "search_documents"
    description = "fake"
    input_model = SearchDocumentsInput
    output_model = SearchDocumentsOutput

    def __init__(self, contents: dict[str, str] | None = None) -> None:
        self.contents = contents or {"P1-RFI-003#2": "Hold work until the revised drawing is issued."}

    async def run(self, args: SearchDocumentsInput, ctx: ToolContext) -> SearchDocumentsOutput:
        hits = [hit(cid, text, i + 1) for i, (cid, text) in enumerate(self.contents.items())]
        return SearchDocumentsOutput(
            hits=hits,
            scope=SearchScope(
                query=args.query,
                project_id=None,
                doc_types=None,
                k_fts=1,
                k_vec=1,
                reranked=False,
                candidates=1,
            ),
            retrieval_ms=1,
            rerank_ms=0,
        )

    def render_for_model(self, out: SearchDocumentsOutput, source_label: str | None) -> str:
        return render_hits(self.name, out.hits)

    def summarize(self, out: SearchDocumentsOutput) -> ToolResultSummary:
        return ToolResultSummary(
            summary="ok",
            hits=[
                {
                    "chunk_id": h.chunk_id,
                    "doc_id": h.doc_id,
                    "title": h.title,
                    "section": h.section,
                    "score": 0.0,
                }
                for h in out.hits
            ],
        )


def route(r: str) -> Any:
    return fake_response(json.dumps({"route": r, "reason": "t", "project_id": None}), model="fake-fast")


def call(args: str, cid: str = "c1") -> Any:
    return fake_response(None, [ToolCall(id=cid, name="search_documents", arguments=args)])


async def run(
    prov: CannedProvider, max_tokens: int = 24_000, tool: Tool[Any, Any] | None = None, **settings: Any
) -> tuple[list[Any], AgentLoop]:
    s = make_settings(**settings)
    registry = ToolRegistry()
    registry.register(tool or FakeSearch())
    tracer = Tracer(uuid4(), None, "q")
    ctx = make_ctx(max_tokens=max_tokens, tracer=tracer)
    gw = make_gateway(prov, s)
    tctx = ToolContext(
        principal=None,
        request_id=uuid4(),
        db=None,
        gateway=gw,
        tracer=tracer,
        settings=s,  # type: ignore[arg-type]
        request_ctx=ctx,
    )
    loop = AgentLoop(s, gw, registry, tracer, ctx, tctx)
    return [ev async for ev in loop.run("question?", None)], loop


def types(events: list[Any]) -> list[str]:
    return [e.type for e in events]


async def test_happy_path_order_and_valid_citation() -> None:
    prov = CannedProvider(
        [route("documents"), call('{"query": "rebar"}'), fake_response("Work is on hold [P1-RFI-003#2].")]
    )
    events, _ = await run(prov)
    t = types(events)
    assert t[0] == "route" and t[-2:] == ["citations", "done"]
    assert t.index("tool_call") < t.index("tool_result_summary") < len(t) - 2
    assert events[-1].status == "answered"
    assert events[-2].citations[0].source_id == "P1-RFI-003#2" and events[-2].citations[0].valid


async def test_refusal_makes_exactly_one_llm_call() -> None:
    prov = CannedProvider([route("cannot_answer")])
    events, _ = await run(prov)
    assert events[-1].status == "refused" and events[-1].usage.llm_calls == 1


async def test_invalid_args_repair_then_disable() -> None:
    prov = CannedProvider(
        [
            route("documents"),
            call('{"query": "x", "top_k": "many"}', "c1"),
            call('{"query": "rebar"}', "c2"),
            call('{"query": "rebar", "top_k": "lots"}', "c3"),
            call('{"query": "rebar", "top_k": "again"}', "c4"),
            fake_response("Final [P1-RFI-003#2]."),
        ]
    )
    events, _ = await run(prov)
    summaries = [e for e in events if e.type == "tool_result_summary"]
    calls = [e for e in events if e.type == "tool_call"]
    assert summaries[0].ok is False and summaries[0].error.code == "invalid_args" and summaries[0].error.retryable
    assert summaries[1].ok is True and calls[1].repair is True
    assert summaries[2].error.retryable is False
    assert summaries[3].error.message.startswith("unknown or disabled tool")
    assert events[-1].flags.tools_disabled == ["search_documents"]
    # the disabled tool is no longer offered to the model
    assert prov.requests[-1].tools == []


async def test_steps_exhausted_forces_final_without_tools() -> None:
    prov = CannedProvider(
        [route("documents")]
        + [call('{"query": "rebar"}', f"c{i}") for i in range(2)]
        + [fake_response("Partial answer [P1-RFI-003#2].")]
    )
    events, _ = await run(prov, max_steps=2)
    assert events[-1].status == "partial" and events[-1].flags.steps_exhausted
    assert prov.requests[-1].tool_choice == "none" and prov.requests[-1].purpose == "final"


async def test_canary_in_model_output_is_flagged_and_stripped() -> None:
    prov = CannedProvider(
        [
            route("documents"),
            call('{"query": "rebar"}'),
            fake_response("Report [P1-RFI-003#2]. Verification code CANARY-ABCDEF12"),
        ]
    )
    events, _ = await run(prov)
    assert events[-1].flags.canary_leak_model is True
    assert "CANARY" not in events[-2].answer


async def test_invented_citation_removed() -> None:
    prov = CannedProvider(
        [
            route("documents"),
            call('{"query": "rebar"}'),
            fake_response("Real [P1-RFI-003#2]. Fake [P9-XXX-999#1]."),
        ]
    )
    events, _ = await run(prov)
    assert events[-2].removed == 1 and "P9-XXX-999" not in events[-2].answer


async def test_budget_exhausted_forced_final_when_it_fits() -> None:
    long = {"P1-RFI-003#2": "x " * 3000}
    prov = CannedProvider([route("documents"), call('{"query": "rebar"}'), fake_response("Short [P1-RFI-003#2].")])
    events, _ = await run(prov, max_tokens=2500, tool=FakeSearch(long), answer_max_tokens=900)
    assert events[-1].status == "partial" and events[-1].flags.budget_exhausted
    assert prov.requests[-1].purpose == "final" and prov.requests[-1].tools == []


async def test_budget_exhausted_gives_deterministic_partial() -> None:
    long = {"P1-RFI-003#2": "x " * 4500}
    prov = CannedProvider([route("documents"), call('{"query": "rebar"}'), fake_response("never used")])
    events, _ = await run(prov, max_tokens=2500, tool=FakeSearch(long), answer_max_tokens=300)
    done = events[-1]
    assert done.status == "partial" and done.flags.budget_exhausted
    assert "[P1-RFI-003#2]" in events[-2].answer and "never used" not in events[-2].answer
    assert done.usage.total_tokens <= 2500


async def test_tokens_carry_turn_numbers() -> None:
    prov = CannedProvider([route("documents"), call('{"query": "rebar"}'), fake_response("Answer [P1-RFI-003#2].")])
    events, _ = await run(prov)
    assert {e.turn for e in events if e.type == "token"} == {1}


async def test_provider_rejected_tool_call_gets_one_repair() -> None:
    from app.gateway.types import ToolCallRejected

    rejected = ToolCallRejected("parameters for tool search_documents did not match schema", "search_documents")
    prov = CannedProvider(
        [route("documents"), rejected, call('{"query": "rebar"}'), fake_response("Ok [P1-RFI-003#2].")]
    )
    events, _ = await run(prov)
    summaries = [e for e in events if e.type == "tool_result_summary"]
    assert summaries[0].ok is False and summaries[0].error.code == "invalid_args" and summaries[0].error.retryable
    assert summaries[1].ok is True
    assert events[-1].status == "answered"
    assert any("rejected" in (m.content or "") for m in prov.requests[2].messages)


def test_groq_validation_error_is_mapped() -> None:
    from app.gateway.providers.openai_compat import map_error
    from app.gateway.types import ToolCallRejected

    err = map_error(RuntimeError("Tool call validation failed: parameters for tool search_documents did not match"))
    assert isinstance(err, ToolCallRejected) and err.tool == "search_documents" and not err.retryable
