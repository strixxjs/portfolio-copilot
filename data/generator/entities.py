"""Entities are generated first; documents are rendered from them, so text and tables always agree."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from data.generator import vocab
from data.generator.projects import AS_OF, ProjectDef
from data.generator.seed_rng import money, rand_date


@dataclass
class ChangeOrder:
    id: str
    project_id: str
    number: int
    title: str
    reason_category: str
    amount: Decimal
    status: str
    submitted_on: date
    decided_on: date | None
    location: str
    schedule_impact_days: int
    breakdown: list[tuple[str, Decimal]]
    raised_by: str


@dataclass
class Milestone:
    project_id: str
    name: str
    planned_date: date
    forecast_date: date
    actual_date: date | None
    status: str
    critical: bool

    @property
    def slip_days(self) -> int:
        return (self.forecast_date - self.planned_date).days


@dataclass
class BudgetLine:
    project_id: str
    cost_code: str
    description: str
    category: str
    budget_amount: Decimal
    committed_amount: Decimal
    actual_amount: Decimal


@dataclass
class Rfi:
    id: str
    number: int
    subject: str
    discipline: str
    location: str
    material: str
    raised_by: str
    raised_on: date
    answered_on: date
    response: str
    status: str
    cost_impact: Decimal | None
    time_impact_days: int


@dataclass
class Ncr:
    id: str
    number: int
    description: str
    root_cause: str
    disposition: str
    location: str
    raised_on: date
    closed: bool


@dataclass
class DailyReport:
    id: str
    number: int
    day: date
    weather: str
    temp_c: int
    headcount: dict[str, int]
    plant: list[str]
    activities: list[str]
    issues: list[str]
    visitors: list[str]


@dataclass
class Meeting:
    id: str
    number: int
    day: date
    attendees: list[str]
    progress: list[str]
    decisions: list[str]
    actions: list[tuple[str, str, date]]


@dataclass
class Clause:
    id: str
    number: str
    topic: str
    text: str
    commentary: str


@dataclass
class ScheduleNarrative:
    id: str
    period: int
    month: date
    summary: str
    critical_path: str
    delays: str
    lookahead: str


@dataclass
class ProjectEntities:
    project: ProjectDef
    change_orders: list[ChangeOrder] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    budget_lines: list[BudgetLine] = field(default_factory=list)
    rfis: list[Rfi] = field(default_factory=list)
    ncrs: list[Ncr] = field(default_factory=list)
    daily_reports: list[DailyReport] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)
    narratives: list[ScheduleNarrative] = field(default_factory=list)


CO_STATUSES = ("approved", "approved", "submitted", "rejected", "withdrawn")


def _person(rng: random.Random) -> str:
    name, role = rng.choice(vocab.PEOPLE)
    return f"{name} ({role})"


def build_change_orders(rng: random.Random, p: ProjectDef) -> list[ChangeOrder]:
    out: list[ChangeOrder] = []
    statuses = list(CO_STATUSES)
    rng.shuffle(statuses)
    for i, (title, reason) in enumerate(vocab.CHANGE_ORDER_TITLES[p.id], start=1):
        status = statuses[i - 1]
        amount = money(rng, 10_000, 900_000)
        if reason == "value_engineering":
            amount = -money(rng, 10_000, 120_000)
        submitted = rand_date(rng, p.start_date + timedelta(days=60), AS_OF - timedelta(days=30))
        decided = submitted + timedelta(days=rng.randint(7, 28)) if status in ("approved", "rejected") else None
        parts = rng.sample(["labour", "materials", "plant", "subcontract", "design fees"], 3)
        weights = [Decimal(w) for w in (Decimal("0.5"), Decimal("0.3"), Decimal("0.2"))]
        first = (amount * weights[0]).quantize(Decimal("0.01"))
        second = (amount * weights[1]).quantize(Decimal("0.01"))
        breakdown = [(parts[0], first), (parts[1], second), (parts[2], amount - first - second)]
        out.append(
            ChangeOrder(
                id=f"{p.id}-CO-{i:03d}",
                project_id=p.id,
                number=i,
                title=title,
                reason_category=reason,
                amount=amount,
                status=status,
                submitted_on=submitted,
                decided_on=decided,
                location=rng.choice(vocab.LOCATIONS[p.id]),
                schedule_impact_days=rng.choice([0, 0, 5, 10, 14, 21]),
                breakdown=breakdown,
                raised_by=_person(rng),
            )
        )
    return out


def build_milestones(rng: random.Random, p: ProjectDef) -> list[Milestone]:
    names = vocab.MILESTONES[p.sector]
    span = (p.planned_end_date - p.start_date).days
    critical_idx = set(rng.sample(range(len(names)), 2))
    out: list[Milestone] = []
    for i, name in enumerate(names):
        planned = p.start_date + timedelta(days=int(span * (i + 1) / (len(names) + 0.5)))
        slip = rng.choice([0, 0, 7, 14, 21, 35, 49])
        forecast = planned + timedelta(days=slip)
        if forecast <= AS_OF:
            status, actual = "complete", forecast
        elif planned <= AS_OF:
            status, actual = "delayed", None
        elif slip > 0:
            status, actual = "delayed" if slip >= 21 else "in_progress", None
        else:
            status, actual = "not_started", None
        out.append(Milestone(p.id, name, planned, forecast, actual, status, i in critical_idx))
    return out


def build_budget_lines(rng: random.Random, p: ProjectDef) -> list[BudgetLine]:
    weights = [rng.randint(5, 20) for _ in vocab.BUDGET_LINES]
    total_weight = sum(weights)
    pool = p.contract_value * Decimal("0.9")
    overspent = rng.randrange(1, len(vocab.BUDGET_LINES) - 1)
    out: list[BudgetLine] = []
    for idx, ((code, desc, cat), w) in enumerate(zip(vocab.BUDGET_LINES, weights, strict=True)):
        budget = (pool * w / total_weight).quantize(Decimal("1000"))
        progress = Decimal(rng.randint(35, 70)) / 100
        actual = (budget * progress).quantize(Decimal("0.01"))
        if idx == overspent:
            actual = (budget * Decimal(rng.randint(104, 118)) / 100).quantize(Decimal("0.01"))
        committed = max(actual, (budget * Decimal(rng.randint(75, 98)) / 100).quantize(Decimal("0.01")))
        out.append(BudgetLine(p.id, code, desc, cat, budget.quantize(Decimal("0.01")), committed, actual))
    return out


RESPONSES = (
    "Proceed as drawn; the detail shown on the current revision is confirmed by the designer.",
    "Revise the detail as per the attached sketch SK-{n:02d}; the designer has issued the change.",
    "Use the alternative proposed by the contractor, subject to submission of a method statement.",
    "Hold work in this area until the revised drawing is issued next week.",
)


def build_rfis(rng: random.Random, p: ProjectDef) -> list[Rfi]:
    out: list[Rfi] = []
    for i, (subject, disc, loc, mat) in enumerate(vocab.RFI_SUBJECTS[p.id], start=1):
        raised = rand_date(rng, p.start_date + timedelta(days=20), AS_OF - timedelta(days=20))
        has_cost = rng.random() < 0.4
        cost = money(rng, 2_000, 60_000) if has_cost else None
        out.append(
            Rfi(
                id=f"{p.id}-RFI-{i:03d}",
                number=i,
                subject=subject,
                discipline=disc,
                location=loc,
                material=mat,
                raised_by=_person(rng),
                raised_on=raised,
                answered_on=raised + timedelta(days=rng.randint(2, 12)),
                response=rng.choice(RESPONSES).format(n=i),
                status=rng.choice(["closed", "closed", "open"]),
                cost_impact=cost,
                time_impact_days=rng.choice([0, 0, 0, 3, 7]),
            )
        )
    return out


def build_ncrs(rng: random.Random, p: ProjectDef) -> list[Ncr]:
    return [
        Ncr(
            id=f"{p.id}-NCR-{i:03d}",
            number=i,
            description=desc,
            root_cause=cause,
            disposition=disp,
            location=rng.choice(vocab.LOCATIONS[p.id]),
            raised_on=rand_date(rng, p.start_date + timedelta(days=30), AS_OF - timedelta(days=10)),
            closed=rng.random() < 0.6,
        )
        for i, (desc, cause, disp) in enumerate(vocab.NCR_ITEMS, start=1)
    ]


def build_daily_reports(rng: random.Random, p: ProjectDef, rfis: list[Rfi], count: int = 8) -> list[DailyReport]:
    days = sorted({rand_date(rng, AS_OF - timedelta(days=120), AS_OF) for _ in range(count * 3)})[:count]
    out: list[DailyReport] = []
    for i, day in enumerate(days, start=1):
        trades = rng.sample(vocab.TRADES, 4)
        locs = vocab.LOCATIONS[p.id]
        activities = [
            f"{rng.choice(trades).capitalize()} working at {rng.choice(locs)} on {rng.choice(vocab.MATERIALS)} installation"
            for _ in range(rng.randint(3, 5))
        ]
        issues: list[str] = []
        if rng.random() < 0.6:
            rfi = rng.choice(rfis)
            issues.append(f"Work at {rfi.location} paused awaiting the response to {rfi.id} ({rfi.subject}).")
        if rng.random() < 0.4:
            issues.append(f"Delivery of {rng.choice(vocab.MATERIALS)} arrived {rng.randint(1, 4)} hours late.")
        out.append(
            DailyReport(
                id=f"{p.id}-DSR-{i:03d}",
                number=i,
                day=day,
                weather=rng.choice(vocab.WEATHER),
                temp_c=rng.randint(4, 24),
                headcount={t: rng.randint(2, 14) for t in trades},
                plant=rng.sample(vocab.PLANT, 3),
                activities=activities,
                issues=issues,
                visitors=[_person(rng) for _ in range(rng.randint(0, 2))],
            )
        )
    return out


def build_meetings(
    rng: random.Random, p: ProjectDef, cos: list[ChangeOrder], rfis: list[Rfi], lines: list[BudgetLine]
) -> list[Meeting]:
    out: list[Meeting] = []
    overspent = max(lines, key=lambda b: b.actual_amount - b.budget_amount)
    for i in range(1, 5):
        day = rand_date(rng, AS_OF - timedelta(days=40 * (5 - i)), AS_OF - timedelta(days=40 * (4 - i)))
        co = cos[(i - 1) % len(cos)]
        rfi = rfis[(i * 2) % len(rfis)]
        out.append(
            Meeting(
                id=f"{p.id}-MOM-{i:03d}",
                number=i,
                day=day,
                attendees=sorted({_person(rng) for _ in range(5)}),
                progress=[
                    f"{co.id} ({co.title}) is {co.status}.",
                    f"{rfi.id} on {rfi.subject.lower()} was discussed; status {rfi.status}.",
                    f"Cost code {overspent.cost_code} ({overspent.description.lower()}) is forecast over budget.",
                ],
                decisions=[
                    f"The team agreed to resequence work at {rng.choice(vocab.LOCATIONS[p.id])} to protect the programme.",
                    f"{rng.choice(vocab.SUBCONTRACTORS)} to submit a recovery programme by the next meeting.",
                ],
                actions=[
                    (f"Close out {rfi.id}", _person(rng), day + timedelta(days=7)),
                    (f"Issue cost report including {co.id}", _person(rng), day + timedelta(days=14)),
                ],
            )
        )
    return out


def build_clauses(p: ProjectDef, ld_rate: Decimal) -> list[Clause]:
    texts = {
        "Liquidated damages": (
            f"If the Contractor fails to complete the Works by the Completion Date, the Contractor shall pay "
            f"the Employer liquidated damages at the rate of £{ld_rate:,.2f} per week or part week, up to a "
            f"maximum of 10 percent of the Contract Sum."
        ),
        "Variations": (
            "The Employer's Representative may instruct a variation at any time before completion. The Contractor "
            "shall submit a quotation within 14 days of the instruction, including cost and time effects."
        ),
        "Extension of time": (
            "The Contractor shall notify any delay event within 28 days of becoming aware of it. Failure to notify "
            "within this period bars entitlement to an extension of time for that event."
        ),
        "Payment terms": (
            "Interim payments are made monthly. The final date for payment is 28 days after the due date. "
            "Retention of 3 percent is held and half is released at practical completion."
        ),
    }
    commentary = {
        "Liquidated damages": "The rate was negotiated at tender and represents a genuine pre-estimate of loss.",
        "Variations": "Quotations not agreed within 21 days are valued by the Employer's Representative.",
        "Extension of time": "The 28-day notice is a condition precedent; site teams must log events promptly.",
        "Payment terms": "Pay-less notices must be issued no later than 5 days before the final date for payment.",
    }
    return [
        Clause(f"{p.id}-CL-{i:03d}", num, topic, texts[topic], commentary[topic])
        for i, (num, topic) in enumerate(vocab.CLAUSE_TOPICS, start=1)
    ]


def build_narratives(rng: random.Random, p: ProjectDef, ms: list[Milestone]) -> list[ScheduleNarrative]:
    out: list[ScheduleNarrative] = []
    crit = [m for m in ms if m.critical]
    worst = max(ms, key=lambda m: m.slip_days)
    for i in range(1, 4):
        month = date(AS_OF.year, AS_OF.month - (3 - i), 1)
        period = (month.year - p.start_date.year) * 12 + month.month - p.start_date.month + 1
        out.append(
            ScheduleNarrative(
                id=f"{p.id}-SN-{i:03d}",
                period=period,
                month=month,
                summary=(
                    f"Progress in period {period} was {rng.choice(['broadly on plan', 'behind plan', 'ahead of plan'])}. "
                    f"Overall completion is {rng.randint(30, 75)} percent."
                ),
                critical_path=" then ".join(m.name for m in crit) + " remain on the critical path.",
                delays=(
                    f"The milestone '{worst.name}' is forecast {worst.slip_days} days late "
                    f"(planned {worst.planned_date.isoformat()}, forecast {worst.forecast_date.isoformat()}). "
                    f"Mitigation: additional {rng.choice(vocab.TRADES)} and extended working hours."
                ),
                lookahead=f"Next period focuses on {rng.choice(vocab.LOCATIONS[p.id])} and {rng.choice(vocab.MATERIALS)} works.",
            )
        )
    return out


def build_project(rng: random.Random, p: ProjectDef) -> ProjectEntities:
    e = ProjectEntities(project=p)
    e.change_orders = build_change_orders(rng, p)
    e.milestones = build_milestones(rng, p)
    e.budget_lines = build_budget_lines(rng, p)
    e.rfis = build_rfis(rng, p)
    e.ncrs = build_ncrs(rng, p)
    e.daily_reports = build_daily_reports(rng, p, e.rfis)
    e.meetings = build_meetings(rng, p, e.change_orders, e.rfis, e.budget_lines)
    e.clauses = build_clauses(p, money(rng, 15_000, 60_000, 1000))
    e.narratives = build_narratives(rng, p, e.milestones)
    return e
