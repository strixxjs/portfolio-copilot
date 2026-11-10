"""Deterministic randomness helpers. One Random instance is passed explicitly everywhere."""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

NAMESPACE = uuid.UUID("6f1a9d3e-0000-4000-8000-000000000001")


def make_rng(seed: int) -> random.Random:
    return random.Random(seed)


def stable_uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def rand_date(rng: random.Random, start: date, end: date) -> date:
    span = (end - start).days
    return start + timedelta(days=rng.randint(0, max(span, 0)))


def money(rng: random.Random, low: int, high: int, step: int = 500) -> Decimal:
    return Decimal(rng.randrange(low, high, step)).quantize(Decimal("0.01"))


def fmt_money(value: Decimal, currency: str = "GBP") -> str:
    symbol = "£" if currency == "GBP" else f"{currency} "
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"
