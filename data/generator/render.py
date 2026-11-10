"""Entity → document (title, ordered sections). Each section becomes one chunk."""

from __future__ import annotations

import math

from data.generator.entities import (
    ChangeOrder,
    Clause,
    DailyReport,
    Meeting,
    Ncr,
    ProjectEntities,
    Rfi,
    ScheduleNarrative,
)
from data.generator.models import ChunkRow, DocumentRow
from data.generator.seed_rng import fmt_money

Sections = list[tuple[str, str]]


def make_doc(
    doc_id: str,
    project_id: str,
    doc_type: str,
    title: str,
    doc_date: object,
    author: str,
    sections: Sections,
    extra_meta: dict[str, str | int | bool] | None = None,
) -> DocumentRow:
    chunks = [
        ChunkRow(
            id=f"{doc_id}#{seq}",
            seq=seq,
            section=name,
            page=seq // 2 + 1,
            paragraph=seq % 2 + 1,
            content=text,
            token_count=math.ceil(len(text) / 4),
        )
        for seq, (name, text) in enumerate(sections)
    ]
    meta: dict[str, str | int | bool] = {"synthetic": True}
    meta.update(extra_meta or {})
    return DocumentRow(
        id=doc_id,
        project_id=project_id,
        doc_type=doc_type,
        title=title,
        doc_date=doc_date,
        author=author,
        metadata=meta,
        chunks=chunks,
    )


def render_rfi(pid: str, name: str, r: Rfi) -> DocumentRow:
    cost = (
        f"Cost impact: yes, estimated at {fmt_money(r.cost_impact)}."
        if r.cost_impact is not None
        else "Cost impact: no cost impact."
    )
    time = f"Time impact: {r.time_impact_days} days." if r.time_impact_days else "Time impact: none."
    sections = [
        (
            "Header",
            f"Request for Information {r.number:03d} on {name} ({pid}). Subject: {r.subject}. "
            f"Discipline: {r.discipline}. Location: {r.location}. Raised by {r.raised_by} on "
            f"{r.raised_on.isoformat()}.",
        ),
        (
            "Question",
            f"The contractor requests clarification on {r.subject.lower()} at {r.location}. The current "
            f"drawings show a detail for {r.material} that conflicts with the specification and with "
            f"site conditions observed during setting out. Please confirm the required detail before "
            f"work in this area continues.",
        ),
        ("Response", f"Response issued on {r.answered_on.isoformat()}: {r.response}"),
        ("Status and impact", f"Status: {r.status}. {cost} {time}"),
    ]
    return make_doc(r.id, pid, "rfi", f"RFI {r.number:03d} – {r.subject}", r.raised_on, r.raised_by, sections)


def render_change_order(pid: str, co: ChangeOrder) -> DocumentRow:
    breakdown = "; ".join(f"{part}: {fmt_money(v)}" for part, v in co.breakdown)
    approval = {
        "approved": f"Approved by the Employer's Representative on {co.decided_on}.",
        "rejected": f"Rejected by the Employer's Representative on {co.decided_on}; the contractor may resubmit.",
        "submitted": "Submitted and awaiting the Employer's Representative's decision.",
        "withdrawn": "Withdrawn by the contractor before a decision was made.",
        "draft": "Draft, not yet submitted.",
    }[co.status]
    reason_text = {
        "unforeseen_ground": "ground conditions that differ materially from the site investigation report",
        "regulatory": "a change in the applicable regulations after contract award",
        "client_request": "a written request from the client",
        "value_engineering": "a value engineering proposal that reduces cost without reducing performance",
        "design_error": "an error in the contract design that had to be corrected",
    }[co.reason_category]
    impact = (
        f"The change extends the affected activity by {co.schedule_impact_days} days."
        if co.schedule_impact_days
        else "The change has no effect on the programme."
    )
    sections = [
        (
            "Summary",
            f"Change Order {co.number:03d} – {co.title}. Value {fmt_money(co.amount)}. "
            f"Reason category: {co.reason_category}. Location: {co.location}. Status: {co.status}.",
        ),
        (
            "Justification",
            f"This change is required because of {reason_text}. At {co.location} the works as "
            f"originally specified could not proceed without this change. Raised by {co.raised_by}.",
        ),
        ("Cost breakdown", f"Total {fmt_money(co.amount)}. Breakdown: {breakdown}."),
        ("Schedule impact", impact),
        ("Approval", f"Submitted on {co.submitted_on.isoformat()}. {approval}"),
    ]
    return make_doc(
        co.id,
        pid,
        "change_order",
        f"Change Order {co.number:03d} – {co.title}",
        co.submitted_on,
        co.raised_by,
        sections,
    )


def render_ncr(pid: str, n: Ncr) -> DocumentRow:
    sections = [
        (
            "Description",
            f"Non-conformance {n.number:03d} raised on {n.raised_on.isoformat()} at {n.location}: {n.description}.",
        ),
        ("Root cause", f"Investigation found the root cause to be {n.root_cause}."),
        (
            "Disposition",
            f"Disposition: {n.disposition}. The designer reviewed the proposal before acceptance.",
        ),
        ("Close-out", "Closed out after inspection." if n.closed else "Open; awaiting re-inspection."),
    ]
    return make_doc(n.id, pid, "ncr", f"NCR {n.number:03d} – {n.description}", n.raised_on, "Quality Manager", sections)


def render_daily_report(pid: str, d: DailyReport, extra_issue: str | None = None) -> DocumentRow:
    labour = ", ".join(f"{t} {n}" for t, n in d.headcount.items())
    issues = list(d.issues) + ([extra_issue] if extra_issue else [])
    sections = [
        (
            "Site conditions",
            f"Daily site report for {d.day.isoformat()}. Weather: {d.weather}, {d.temp_c} °C.",
        ),
        (
            "Labour and plant",
            f"Labour on site: {labour}. Total {sum(d.headcount.values())}. Plant: {', '.join(d.plant)}.",
        ),
        ("Activities", " ".join(a + "." for a in d.activities)),
        ("Issues and incidents", " ".join(issues) if issues else "No issues or incidents recorded."),
        ("Visitors", "Visitors: " + (", ".join(d.visitors) if d.visitors else "none") + "."),
    ]
    return make_doc(
        d.id, pid, "daily_report", f"Daily Site Report – {d.day.isoformat()}", d.day, "Site Manager", sections
    )


def render_meeting(pid: str, m: Meeting, extra_decision: str | None = None) -> DocumentRow:
    decisions = list(m.decisions) + ([extra_decision] if extra_decision else [])
    actions = " ".join(f"{a} – owner {o}, due {due.isoformat()}." for a, o, due in m.actions)
    sections = [
        ("Attendees", "Present: " + ", ".join(m.attendees) + "."),
        ("Progress review", " ".join(m.progress)),
        ("Decisions", " ".join(decisions)),
        ("Actions", actions),
    ]
    return make_doc(
        m.id,
        pid,
        "meeting_minutes",
        f"Progress Meeting {m.number} – {m.day.isoformat()}",
        m.day,
        "Project Manager",
        sections,
    )


def render_clause(pid: str, c: Clause, contract_start: object) -> DocumentRow:
    sections = [("Clause text", f"Clause {c.number} – {c.topic}. {c.text}"), ("Commentary", c.commentary)]
    return make_doc(
        c.id,
        pid,
        "contract_clause",
        f"Clause {c.number} – {c.topic}",
        contract_start,
        "Commercial Manager",
        sections,
    )


def render_narrative(pid: str, s: ScheduleNarrative) -> DocumentRow:
    label = s.month.strftime("%B %Y")
    sections = [
        ("Period summary", f"Schedule narrative for period {s.period} ({label}). {s.summary}"),
        ("Critical path", s.critical_path),
        ("Delays and mitigation", s.delays),
        ("Look-ahead", s.lookahead),
    ]
    return make_doc(
        s.id,
        pid,
        "schedule_narrative",
        f"Schedule Narrative – Period {s.period} ({label})",
        s.month,
        "Planning Engineer",
        sections,
    )


def render_project(e: ProjectEntities) -> list[DocumentRow]:
    p = e.project
    docs: list[DocumentRow] = []
    docs += [render_rfi(p.id, p.name, r) for r in e.rfis]
    docs += [render_change_order(p.id, c) for c in e.change_orders]
    docs += [render_ncr(p.id, n) for n in e.ncrs]
    docs += [render_daily_report(p.id, d) for d in e.daily_reports]
    docs += [render_meeting(p.id, m) for m in e.meetings]
    docs += [render_clause(p.id, c, p.start_date) for c in e.clauses]
    docs += [render_narrative(p.id, s) for s in e.narratives]
    return docs
