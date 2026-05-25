import time
from pathlib import Path

from agentnexus.services.skill import SkillService
from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.workflow import Workflow


def _entry(skill_id: str, name: str, description: str) -> SkillEntry:
    workflow = Workflow.model_validate({
        "id": skill_id,
        "version": "1",
        "display_name": name,
        "description": description,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "guide", "prompt": f"Use {name}."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry("default", skill_id, name, description, Path("SKILL.md"), workflow, source_kind="skill")


def test_skill_router_index_call_rate_and_latency():
    entries = [
        _entry("docx", "DOCX", "Create edit inspect and format Microsoft Word docx documents. 生成 word 文档。"),
        _entry("pdf", "PDF", "Read split merge rotate and extract content from PDF documents."),
        _entry("xlsx", "XLSX", "Analyze edit calculate formulas and charts in spreadsheets."),
        _entry("pptx", "PPTX", "Create edit and inspect PowerPoint presentations and slides."),
    ]
    for index in range(60):
        entries.append(_entry(
            f"generic-{index}",
            f"Generic {index}",
            "General helper for common writing coding review and planning tasks.",
        ))
    registry = SkillRegistry([])
    registry._entries = entries
    service = SkillService(registry)

    cases = [
        ("生成一份word docx文档", "default/docx"),
        ("extract pages from this pdf file", "default/pdf"),
        ("analyze spreadsheet formulas in xlsx", "default/xlsx"),
        ("create powerpoint slides pptx", "default/pptx"),
    ]
    start = time.perf_counter()
    hits = 0
    for text, expected in cases * 25:
        service.reset()
        route = service.maybe_auto_select(text)
        if route is not None and route.entry.qualified_id == expected:
            hits += 1
    elapsed = time.perf_counter() - start
    call_rate = hits / (len(cases) * 25)

    assert call_rate >= 0.95
    assert elapsed < 1.0
