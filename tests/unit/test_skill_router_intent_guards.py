"""P1 回归：技能路由器的两个真实缺陷（fine-grained 评测 PER-103 / PER-104 复现）。

缺陷 1（误触发）：「股票这个词用英语怎么说」含"股票"但意图是翻译，
纯关键词打分把 stock_analysis 顶上去了。修复手段是技能元数据里的
negative_hints（路由器已有 -3.0/命中 的惩罚机制，但评测技能没声明）。

缺陷 2（多意图盲区）：「查一下北京天气，顺便 review 一下我刚写的代码」
有两个独立意图，但 SkillRouteDecision.mode 从未产出 "multi_intent"
（死枚举），且 "顺便" 不在并行连接词里。修复后应产出
mode="multi_intent" 且按意图出现位置排序（前者为主）。
"""
from pathlib import Path

from agentnexus.skills.registry import SkillEntry
from agentnexus.skills.router.decide import SkillRouter
from agentnexus.skills.workflow import Workflow


def _make_skill(workflow_id: str, name: str, desc: str,
                verbs=(), objects=(), examples=(), negative_hints=()) -> SkillEntry:
    wf = Workflow.model_validate({
        "id": workflow_id, "version": "1", "display_name": name,
        "description": desc,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "s1", "prompt": "Do."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry(
        namespace="eval", workflow_id=workflow_id, display_name=name,
        description=desc, path=Path("/tmp/eval"), workflow=wf,
        verbs=verbs, objects=objects, examples=examples,
        negative_hints=negative_hints,
    )


def _entries(with_negative_hints: bool = True) -> list[SkillEntry]:
    return [
        _make_skill("weather_query", "天气查询", "查询城市天气、温度、出行建议",
                    ("查询", "查", "看看"), ("天气", "温度", "气温"),
                    ("北京今天天气怎么样", "上海明天会下雨吗")),
        _make_skill("stock_analysis", "股票分析", "分析股票走势、行情和投资价值",
                    ("分析", "看看", "查"), ("股票", "股价", "行情"),
                    ("分析一下贵州茅台", "平安银行股价走势"),
                    negative_hints=("怎么说", "翻译", "英语", "什么意思") if with_negative_hints else ()),
        _make_skill("code_review", "代码审查", "审查已有代码的质量、安全性和并发问题",
                    ("审查", "review", "检查"), ("代码", "函数", "bug"),
                    ("帮我审查这段代码", "review 这个函数")),
        _make_skill("code_generation", "代码生成", "根据需求编写新的代码实现",
                    ("写", "实现", "生成", "编写"), ("代码", "函数", "脚本", "程序"),
                    ("帮我写一段快速排序", "实现一个登录接口")),
    ]


def _router(entries) -> SkillRouter:
    return SkillRouter(min_score=2.0, use_embeddings=False)


class TestNegativeHintSuppression:
    """PER-104：领域词命中但无领域意图 → 不应路由。"""

    def test_stock_word_in_translation_question_abstains(self):
        router = _router(_entries())
        decision = router.decide("股票这个词用英语怎么说", _entries())
        assert decision.route is None or decision.route.entry.workflow_id != "stock_analysis", (
            f"翻译意图不得路由到股票技能，实际={decision.route.entry.workflow_id}"
        )

    def test_real_stock_intent_still_routes(self):
        entries = _entries()
        router = _router(entries)
        decision = router.decide("分析一下贵州茅台这只股票", entries)
        assert decision.route is not None
        assert decision.route.entry.workflow_id == "stock_analysis"

    def test_without_negative_hints_false_trigger_reproduces(self):
        """对照组：没有 negative_hints 时误触发确实存在（证明修复有效而非测试无效）。"""
        entries = _entries(with_negative_hints=False)
        router = _router(entries)
        decision = router.decide("股票这个词用英语怎么说", entries)
        assert decision.route is not None
        assert decision.route.entry.workflow_id == "stock_analysis", "对照组应复现误触发"


class TestMultiIntentRouting:
    """PER-103：并行多意图 → mode=multi_intent，按意图出现位置定主技能。"""

    def test_parallel_query_produces_multi_intent(self):
        entries = _entries()
        router = _router(entries)
        decision = router.decide("查一下北京天气，顺便 review 一下我刚写的代码", entries)
        assert decision.mode == "multi_intent", f"实际 mode={decision.mode}"
        assert decision.route is not None
        assert decision.route.entry.workflow_id == "weather_query", (
            f"先出现的意图应为主技能，实际={decision.route.entry.workflow_id}"
        )
        assert any(s.endswith("/code_review") for s in decision.secondary_skills)

    def test_single_intent_stays_single(self):
        entries = _entries()
        router = _router(entries)
        decision = router.decide("查一下北京天气", entries)
        assert decision.mode == "single"
        assert decision.route.entry.workflow_id == "weather_query"

    def test_sequential_connector_is_not_multi_intent(self):
        entries = _entries()
        router = _router(entries)
        decision = router.decide("先查一下北京天气然后 review 代码", entries)
        assert decision.mode != "multi_intent", "顺序意图不是并行多意图"

    def test_no_false_multi_intent_on_overlapping_vocab(self):
        """单意图句命中两个技能的词汇（写代码重叠）→ 不得拆成 multi。"""
        entries = _entries()
        router = _router(entries)
        decision = router.decide("帮我写一段代码实现快速排序", entries)
        assert decision.mode != "multi_intent"
        assert decision.route.entry.workflow_id == "code_generation"
