from __future__ import annotations

from agentic_ai.section_order import SECTION_ORDER, section_order_for


def test_every_role_type_has_a_section_order() -> None:
    for role in ("data_scientist", "data_analyst", "ai_engineer", "fullstack_ship"):
        assert role in SECTION_ORDER
        assert section_order_for(role) == SECTION_ORDER[role]


def test_ai_engineer_leads_with_projects() -> None:
    """CLAUDE.md Section Order: AI Engineer JD -> Projects -> Experience -> Skills."""
    assert section_order_for("ai_engineer")[0] == "projects"


def test_data_analyst_leads_with_experience() -> None:
    assert section_order_for("data_analyst")[0] == "experience"
