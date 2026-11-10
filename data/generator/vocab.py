"""Vocabulary pools. All names are invented."""

from __future__ import annotations

DISCIPLINES = ("structural", "civils", "MEP", "architectural", "geotechnical")
MATERIALS = (
    "C40/50 concrete",
    "B500B rebar",
    "S355 structural steel",
    "precast parapet units",
    "aluminium cladding panels",
    "HDPE drainage pipe",
)
LOCATIONS = {
    "P1": ("Pier 3", "Abutment B", "Span 2 deck", "North approach embankment"),
    "P2": ("Level 2 east wing", "Plant room B", "Theatre suite 4", "Main atrium"),
    "P3": ("Gatehouse", "Warehouse bay 3", "Yard drainage", "Rail siding"),
}
SUBCONTRACTORS = (
    "Ardent Groundworks Ltd",
    "Meridian M&E Services",
    "Castellan Steel Ltd",
    "Brightwater Drainage",
)
PEOPLE = (
    ("Hannah Okafor", "Project Manager"),
    ("Tomasz Wierzbicki", "Site Manager"),
    ("Priya Raman", "Design Manager"),
    ("Liam Doherty", "Quantity Surveyor"),
    ("Grace Mensah", "Structural Engineer"),
    ("Oskar Lindqvist", "Planning Engineer"),
    ("Mei Tanaka", "Client Representative"),
    ("Daniel Brooks", "HSE Advisor"),
)
WEATHER = (
    "dry and overcast",
    "light rain in the morning",
    "sunny",
    "heavy rain after 14:00",
    "windy, gusts to 40 km/h",
)
TRADES = (
    "groundworkers",
    "steel fixers",
    "carpenters",
    "concrete gang",
    "electricians",
    "pipefitters",
    "scaffolders",
)
PLANT = ("80t crawler crane", "20t excavator", "concrete pump", "telehandler", "piling rig", "MEWP")

# RFI subjects per project: (subject, discipline, location, material). The first P1 entry is the
# "rebar cover at Pier 3" RFI that the step-1 acceptance query looks for.
RFI_SUBJECTS = {
    "P1": (
        ("Pier foundation pile cut-off level", "geotechnical", "Pier 3", "C40/50 concrete"),
        ("Bearing plinth dimensions", "structural", "Span 2 deck", "C40/50 concrete"),
        ("Rebar cover at Pier 3 abutment", "structural", "Pier 3", "B500B rebar"),
        ("Parapet unit fixing detail", "structural", "Span 2 deck", "precast parapet units"),
        ("Drainage outfall invert level", "civils", "North approach embankment", "HDPE drainage pipe"),
        ("Embankment fill compaction spec", "geotechnical", "North approach embankment", "C40/50 concrete"),
        ("Sheet pile toe level at Abutment B", "geotechnical", "Abutment B", "S355 structural steel"),
        ("Deck waterproofing membrane type", "civils", "Span 2 deck", "C40/50 concrete"),
    ),
    "P2": (
        ("Medical gas pipework routing", "MEP", "Theatre suite 4", "HDPE drainage pipe"),
        ("Atrium glazing support steel", "structural", "Main atrium", "S355 structural steel"),
        ("Plant room floor loading", "structural", "Plant room B", "C40/50 concrete"),
        ("Fire damper access panels", "MEP", "Level 2 east wing", "aluminium cladding panels"),
        ("Cladding bracket spacing", "architectural", "Level 2 east wing", "aluminium cladding panels"),
        ("Theatre ceiling pendant supports", "structural", "Theatre suite 4", "S355 structural steel"),
        ("Ventilation duct clash at gridline E4", "MEP", "Level 2 east wing", "S355 structural steel"),
        ("Slab edge rebar congestion", "structural", "Main atrium", "B500B rebar"),
    ),
    "P3": (
        ("Yard slab joint layout", "civils", "Yard drainage", "C40/50 concrete"),
        ("Rail siding buffer stop foundation", "structural", "Rail siding", "C40/50 concrete"),
        ("Warehouse portal frame connection", "structural", "Warehouse bay 3", "S355 structural steel"),
        ("Gatehouse cladding colour", "architectural", "Gatehouse", "aluminium cladding panels"),
        ("Interceptor chamber size", "civils", "Yard drainage", "HDPE drainage pipe"),
        ("Dock leveller pit reinforcement", "structural", "Warehouse bay 3", "B500B rebar"),
        ("Ground bearing capacity at bay 3", "geotechnical", "Warehouse bay 3", "C40/50 concrete"),
        ("Siding drainage gradient", "civils", "Rail siding", "HDPE drainage pipe"),
    ),
}

CHANGE_ORDER_TITLES = {
    "P1": (
        ("Additional sheet piling at Abutment B", "unforeseen_ground"),
        ("Revised parapet design to new standard", "regulatory"),
        ("Client-requested lighting columns on Span 2", "client_request"),
        ("Omission of temporary access ramp", "value_engineering"),
        ("Corrected bearing plinth reinforcement", "design_error"),
    ),
    "P2": (
        ("Additional isolation room on Level 2", "client_request"),
        ("Upgraded fire stopping to revised regulations", "regulatory"),
        ("Plant room B structural strengthening", "design_error"),
        ("Alternative cladding system", "value_engineering"),
        ("Contaminated ground removal at service trench", "unforeseen_ground"),
    ),
    "P3": (
        ("Soft spot excavation and replacement at bay 3", "unforeseen_ground"),
        ("Extra dock levellers", "client_request"),
        ("Revised interceptor to EA requirement", "regulatory"),
        ("Simplified gatehouse roof", "value_engineering"),
        ("Portal frame connection redesign", "design_error"),
    ),
}

MILESTONES = {
    "transport": (
        "Piling complete",
        "Pier 3 complete",
        "Deck beams installed",
        "Deck pour complete",
        "Waterproofing and surfacing",
        "Road opening",
    ),
    "healthcare": (
        "Frame complete",
        "Watertight envelope",
        "MEP first fix complete",
        "Theatre fit-out complete",
        "Commissioning complete",
        "Handover to Trust",
    ),
    "industrial": (
        "Groundworks complete",
        "Steel frame complete",
        "Roof and cladding complete",
        "Yard surfacing complete",
        "Rail siding commissioned",
        "Practical completion",
    ),
}

BUDGET_LINES = (
    ("01-100", "Site establishment and welfare", "prelims"),
    ("02-100", "Earthworks and excavation", "subcontract"),
    ("03-100", "Concrete supply", "materials"),
    ("03-200", "Reinforcement supply and fix", "subcontract"),
    ("04-100", "Structural steelwork", "subcontract"),
    ("05-100", "Direct labour", "labour"),
    ("06-100", "Plant hire", "plant"),
    ("07-100", "Drainage and utilities", "subcontract"),
    ("08-100", "Finishes and cladding", "materials"),
    ("09-100", "Risk and contingency", "contingency"),
)

CLAUSE_TOPICS = (
    ("12.3", "Liquidated damages"),
    ("14.1", "Variations"),
    ("16.2", "Extension of time"),
    ("19.4", "Payment terms"),
)

NCR_ITEMS = (
    ("Honeycombing on exposed concrete face", "inadequate vibration during the pour", "repair"),
    ("Rebar spacing outside tolerance", "fixing gang worked from a superseded drawing revision", "rework"),
    ("Steel member delivered with incorrect paint system", "supplier order error", "accept-as-is"),
    ("Drainage pipe laid to insufficient fall", "setting-out error by the subcontractor", "rework"),
)
