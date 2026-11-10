"""The three fixed (synthetic) project definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Documents and data describe the portfolio "as of" this date.
AS_OF = date(2025, 9, 30)


@dataclass(frozen=True)
class ProjectDef:
    id: str
    name: str
    client: str
    sector: str
    country: str
    contract_value: Decimal
    start_date: date
    planned_end_date: date
    status: str
    currency: str = "GBP"


PROJECTS: tuple[ProjectDef, ...] = (
    ProjectDef(
        "P1",
        "Northgate Viaduct Replacement",
        "Highways North",
        "transport",
        "United Kingdom",
        Decimal("48500000.00"),
        date(2024, 3, 4),
        date(2026, 9, 30),
        "active",
    ),
    ProjectDef(
        "P2",
        "Riverside Hospital East Wing",
        "Riverside Health Trust",
        "healthcare",
        "United Kingdom",
        Decimal("31200000.00"),
        date(2024, 6, 10),
        date(2026, 12, 18),
        "active",
    ),
    ProjectDef(
        "P3",
        "Harbour Logistics Depot Phase 2",
        "Portside Logistics plc",
        "industrial",
        "United Kingdom",
        Decimal("17800000.00"),
        date(2025, 1, 13),
        date(2026, 7, 31),
        "active",
    ),
)
