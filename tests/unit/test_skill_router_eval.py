from agentnexus.skills.router.eval import (
    GoldItem,
    evaluate_hits,
    evaluate_on_entries,
    load_gold,
    sweep_params,
)


def _entries():
    from unittest.mock import MagicMock

    from agentnexus.skills.registry import SkillEntry
    from agentnexus.skills.workflow import Workflow

    def make(skill_id, name, desc, verbs=(), objects=(), examples=()):
        wf = Workflow.model_validate({
            "id": skill_id,
            "version": "1",
            "display_name": name,
            "description": desc,
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "s", "prompt": f"Use {name}."}],
            "success_criteria": ["Done."],
        })
        return SkillEntry(
            "default", skill_id, name, desc, MagicMock(), wf,
            source_kind="skill", verbs=tuple(verbs), objects=tuple(objects),
            examples=tuple(examples),
        )

    return [
        make(
            "docx", "DOCX", "Create and edit Word documents.",
            verbs=("创建", "编辑", "写"), objects=("文档", "word", "docx"),
            examples=("帮我写一份word文档", "创建docx"),
        ),
        make(
            "deploy", "Deploy", "Deploy applications to servers.",
            verbs=("部署", "发布"), objects=("应用", "服务器", "docker"),
            examples=("部署应用到服务器", "发布docker容器"),
        ),
        make(
            "search", "Search", "Search code and documentation.",
            verbs=("搜索", "查找"), objects=("代码", "文档", "资料"),
            examples=("搜索代码中的函数", "查找技术文档"),
        ),
    ]


def test_evaluate_hits_scores_rank_positions():
    gold = [
        GoldItem("q1", "default/a"),
        GoldItem("q2", "default/b"),
        GoldItem("q3", "default/c"),
    ]
    ranked = [
        ["default/a", "default/x"],
        ["default/x", "default/b"],
        ["default/y"],
    ]
    report = evaluate_hits(gold, ranked)
    assert report.n == 3
    assert report.hit_at_1 == 1 / 3
    assert report.hit_at_3 == 2 / 3
    assert report.mrr == (1.0 + 0.5) / 3
    assert len(report.misses) == 1


def test_evaluate_on_entries_zero_llm_hit_at_k(tmp_path):
    gold = [
        GoldItem("帮我写一份word文档", "default/docx"),
        GoldItem("部署应用到服务器", "default/deploy"),
        GoldItem("搜索代码中的函数", "default/search"),
    ]
    report = evaluate_on_entries(gold, _entries(), use_embeddings=False)
    assert report.n == 3
    assert report.hit_at_1 >= 2 / 3
    assert report.hit_at_5 == 1.0


def test_sweep_params_ranks_by_hit_at_1():
    gold = [
        GoldItem("帮我写一份word文档", "default/docx"),
        GoldItem("部署docker容器", "default/deploy"),
    ]
    rows = sweep_params(
        gold,
        _entries(),
        min_scores=(1.0, 2.0),
        margins=(0.5, 0.75),
        max_candidates=(5,),
        use_embeddings=False,
    )
    assert len(rows) == 4
    assert rows[0]["hit_at_1"] >= rows[-1]["hit_at_1"]
    assert {"min_score", "margin", "max_candidates", "hit_at_1", "mrr"} <= set(rows[0])


def test_load_gold_jsonl(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text(
        '{"query": "写文档", "skill_id": "default/docx"}\n'
        '{"query": "搜代码", "skill_id": "default/search"}\n',
        encoding="utf-8",
    )
    items = load_gold(path)
    assert len(items) == 2
    assert items[0].skill_id == "default/docx"
