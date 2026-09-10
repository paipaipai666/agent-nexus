"""Memory system evaluation — deterministic probes for LTM / STM / project memory.

Dimensions (aligned with the industry four-dimension framework — recall,
freshness, contradiction/forgetting, plus our additions):

  recall          已存事实能被相关查询检索到
  freshness       新版本事实优先于旧版本；重复提及提升重要度
  forgetting      被 supersede / 过 TTL 的记忆不再出现
  isolation       项目 A 的记忆绝不泄漏到项目 B
  write_integrity 写入侧不变量：去重、boost、驱逐保留高价值
  stm_invariant   短期记忆：压缩保摘要/保尾部、snip 按重要性裁剪、WAL 恢复
  admission       白名单准入：强信号直通、疑问句/事务性拒绝、默认拒绝零 LLM
  quality         LLM judge 维度（--judge 启用）：提取忠实度、负向约束遵从、利用率
  retention       LLM judge 维度（--judge）：压缩存活率——摘要忠实度、会话约束
                  保留（COMPINT 式）、压缩后事实召回
  control         对照组（--judge）：无记忆基线不得出现记忆内容（防先验污染）

  admission 维度是写入侧评估（WritePolicyBench 式）：带标签的"该不该写"判定集，
  离线跑规则层 P/R/F1，--judge 模式跑完整管线（conclude → 提取 → 入库核对）。

方法：全部确定性探针。用语义可控的合成向量替代真实 embedding（被测的是记忆
管道——打分、衰减、隔离、驱逐——不是 embedding 模型质量），因此可在 CI 离线运行。
LoCoMo 式的端到端 LLM 评测不在此范围。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DIMENSIONS = (
    "recall", "freshness", "forgetting", "isolation",
    "write_integrity", "stm_invariant", "admission", "quality",
    "retention", "control",
)

# All probes are deterministic invariants — every dimension must pass 100%.
DEFAULT_THRESHOLDS: dict[str, float] = {d: 1.0 for d in DIMENSIONS}


@dataclass
class WriteOp:
    content: str
    layer: str = "ltm"               # ltm | project
    category: str = "fact"
    importance: float = 0.8
    vec: list[float] | None = None   # synthetic embedding (cosine control)
    workspace: str = "a"             # project layer only
    project_kind: str = "memo"
    backdate_days: int = 0


@dataclass
class MemoryCase:
    name: str
    dimension: str
    layer: str                       # ltm | project | stm | admission
    writes: list[WriteOp] = field(default_factory=list)
    supersede: list[tuple[int, int]] = field(default_factory=list)  # (old_idx, new_idx)
    query_vec: list[float] | None = None
    query_workspace: str = "a"
    min_similarity: float = 0.3
    limit: int = 5
    must_include: list[str] = field(default_factory=list)
    must_exclude: list[str] = field(default_factory=list)
    probe: Callable[["MemorySandbox", "CaseResult"], None] | None = None


@dataclass
class CaseResult:
    name: str
    dimension: str
    layer: str
    passed: bool = False
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class MemoryEvalReport:
    results: list[CaseResult]
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    def dimension_rate(self, dimension: str) -> float:
        rs = [r for r in self.results if r.dimension == dimension]
        if not rs:
            return 1.0
        return sum(1 for r in rs if r.passed) / len(rs)

    @property
    def passed(self) -> bool:
        return all(
            self.dimension_rate(d) >= self.thresholds.get(d, 1.0) for d in DIMENSIONS
        )

    def summary(self) -> str:
        lines = [f"记忆系统评测: {len(self.results)} 个探针", ""]
        for d in DIMENSIONS:
            rs = [r for r in self.results if r.dimension == d]
            if not rs:
                continue
            rate = self.dimension_rate(d)
            threshold = self.thresholds.get(d, 1.0)
            mark = "[PASS]" if rate >= threshold else "[FAIL]"
            lines.append(f"  {d:<16} {sum(1 for r in rs if r.passed)}/{len(rs)} {mark}")
        failed = [r for r in self.results if not r.passed]
        if failed:
            lines.append("")
            lines.append("失败探针:")
            for r in failed:
                lines.append(f"  [{r.layer}] {r.name}: {r.detail}")
                for m in r.missing:
                    lines.append(f"    缺失: {m}")
                for u in r.unexpected:
                    lines.append(f"    不应出现: {u}")
        return "\n".join(lines)


class MemorySandbox:
    """Isolated AGENTNEXUS_HOME per run: real LTM (SQLite+Chroma) + workspaces."""

    def __init__(self, config_overrides: dict | None = None):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self._old_home = os.environ.get("AGENTNEXUS_HOME")
        os.environ["AGENTNEXUS_HOME"] = str(self.root)
        if config_overrides:
            import yaml
            (self.root / "config.yaml").write_text(
                yaml.safe_dump(config_overrides, allow_unicode=True), encoding="utf-8"
            )
        import agentnexus.core.config as cfg
        self._old_cache = cfg._settings_cache
        cfg._settings_cache = None
        from agentnexus.memory.long_term import _reset_long_term_memory
        from agentnexus.storage.chroma import reset_storage_client
        _reset_long_term_memory()
        reset_storage_client()
        self._ltm = None

    @property
    def ltm(self):
        if self._ltm is None:
            from agentnexus.memory.long_term import get_long_term_memory
            self._ltm = get_long_term_memory()
        return self._ltm

    @property
    def generator(self):
        """Generator LLM (production model under test). Lazily built from settings."""
        if getattr(self, "_generator", None) is None:
            from agentnexus.core.llm import get_default_llm
            self._generator = get_default_llm()
        return self._generator

    @property
    def judge(self):
        """Judge LLM — ideally a different model family (self-eval bias)."""
        if getattr(self, "_judge", None) is None:
            from agentnexus.core.judge_llm import get_judge_llm
            self._judge = get_judge_llm()
        return self._judge

    def ws_path(self, workspace: str) -> Path:
        p = self.root / f"ws_{workspace}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, op: WriteOp) -> Any:
        """Execute one write. Returns LTM row id for ltm writes."""
        if op.layer == "ltm":
            row_id = self.ltm.save(
                session_id="eval", content=op.content, category=op.category,
                importance=op.importance, embedding=op.vec,
            )
            if op.backdate_days:
                delta = f"-{op.backdate_days} days"
                self.ltm._conn.execute(
                    "UPDATE long_term_memories SET created_at = datetime('now', ?), "
                    "last_accessed_at = datetime('now', ?) WHERE id = ?",
                    (delta, delta, row_id),
                )
                self.ltm._conn.commit()
            return row_id
        if op.layer == "project":
            from agentnexus.memory.project import ProjectMemory
            pm = ProjectMemory(self.ws_path(op.workspace))
            pm.add_entry(op.project_kind, op.content)
            return None
        raise ValueError(f"unknown write layer: {op.layer!r}")

    def ltm_contents(self, query_vec: list[float] | None, limit: int = 5,
                     min_similarity: float = 0.3) -> list[str]:
        rows = self.ltm.search(query_embedding=query_vec, limit=limit,
                               min_similarity=min_similarity)
        return [r["content"] for r in rows]

    def project_context(self, workspace: str) -> str:
        from agentnexus.memory.manager import MemoryManager
        from agentnexus.memory.project import ProjectMemory
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.session_id = "eval"
        mgr.long_term = None
        mgr.project = ProjectMemory(self.ws_path(workspace))
        return mgr.init_session("评估查询")

    def close(self):
        try:
            from agentnexus.memory.long_term import _reset_long_term_memory
            from agentnexus.storage.chroma import reset_storage_client
            _reset_long_term_memory()
            reset_storage_client()
        except Exception:
            pass
        import agentnexus.core.config as cfg
        cfg._settings_cache = self._old_cache
        if self._old_home is None:
            os.environ.pop("AGENTNEXUS_HOME", None)
        else:
            os.environ["AGENTNEXUS_HOME"] = self._old_home
        self._tmp.cleanup()


class MemoryEvaluator:
    """Run memory probe cases and aggregate a report."""

    def __init__(self, cases: list[MemoryCase] | None = None,
                 thresholds: dict[str, float] | None = None,
                 enable_judge: bool = False):
        if cases is None:
            cases = default_cases(include_judge=enable_judge)
        self.cases = cases
        self.thresholds = thresholds or dict(DEFAULT_THRESHOLDS)

    def run(self) -> MemoryEvalReport:
        results: list[CaseResult] = []
        for case in self.cases:
            sandbox = MemorySandbox()
            result = CaseResult(name=case.name, dimension=case.dimension, layer=case.layer)
            try:
                self._run_case(sandbox, case, result)
                result.passed = not result.missing and not result.unexpected
            except Exception as e:
                result.passed = False
                result.detail = f"exception: {type(e).__name__}: {e}"
            finally:
                sandbox.close()
            results.append(result)
        return MemoryEvalReport(results=results, thresholds=dict(self.thresholds))

    def _run_case(self, sandbox: MemorySandbox, case: MemoryCase, result: CaseResult) -> None:
        if case.probe is not None:
            case.probe(sandbox, result)
            return

        ids = [sandbox.write(op) for op in case.writes]
        for old_i, new_i in case.supersede:
            sandbox.ltm.mark_superseded(ids[old_i], ids[new_i])

        if case.layer == "ltm":
            observed = sandbox.ltm_contents(
                case.query_vec, limit=case.limit, min_similarity=case.min_similarity
            )
        elif case.layer == "project":
            observed = [sandbox.project_context(case.query_workspace)]
        else:
            raise ValueError(f"declarative case layer must be ltm|project: {case.layer!r}")

        joined = "\n".join(observed)
        result.missing = [m for m in case.must_include if m not in joined]
        result.unexpected = [m for m in case.must_exclude if m in joined]
        result.detail = f"observed {len(observed)} item(s)"


# ── STM / admission / TTL / eviction probes (procedural) ─────────────


def _probe_stm_compact(sb: MemorySandbox, res: CaseResult) -> None:
    from agentnexus.memory.short_term import ShortTermMemory
    stm = ShortTermMemory()
    for i in range(12):
        stm.append("user", f"第{i + 1}轮消息")
    stm.compact_full("前 6 轮讨论了项目结构", keep_recent=6)
    msgs = stm.get_all()
    joined = "\n".join(m["content"] for m in msgs)
    if "前 6 轮讨论了项目结构" not in joined:
        res.missing.append("compaction summary")
    if "第12轮消息" not in joined or "第7轮消息" not in joined:
        res.missing.append("recent tail (last 6)")
    if "第1轮消息" in joined:
        res.unexpected.append("old message survived compaction")
    res.detail = f"messages after compact: {len(msgs)} (expect 7)"


def _probe_index_fold_archive_search(sb: MemorySandbox, res: CaseResult) -> None:
    """索引折叠后：上下文只剩归档目录，原文可经 history_search 取回（确定性）。"""
    import json as _json

    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.tools.history_search import history_search

    # 构造归档文件（模拟引擎 _archive_history 的写入格式）
    hdir = sb.root / "history"
    hdir.mkdir(parents=True, exist_ok=True)
    records = [
        {"i": i, "role": "user",
         "content": f"第{i}轮：错误码 E_FOLD_TEST_770 在第 {100 + i} 行"}
        for i in range(10)
    ]
    (hdir / "eval.jsonl").write_text(
        "\n".join(_json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8")

    stm = ShortTermMemory()
    for i in range(5):
        stm.append("system", f"[历史索引 消息{i * 2 + 1}-{i * 2 + 2}] " + "摘要" * 60,
                   metadata={"memory_index": True,
                             "seg_start": i * 2, "seg_end": i * 2 + 2})
    stm.append("user", "最近的问题")
    folded = stm.fold_index(50)  # 小预算强制折叠
    context = "\n".join(m["content"] for m in stm.get_all())
    if folded == 0:
        res.missing.append("fold did not trigger under tiny budget")
    if "历史索引目录" not in context:
        res.missing.append("archive directory missing after fold")
    if "E_FOLD_TEST_770" in context:
        res.unexpected.append("folded detail still in context")
    found = history_search("E_FOLD_TEST_770")
    if "E_FOLD_TEST_770" not in found:
        res.missing.append("history_search failed to recover folded detail")
    res.detail = f"folded={folded}, search_recovered={'E_FOLD_TEST_770' in found}"


def _probe_stm_snip(sb: MemorySandbox, res: CaseResult) -> None:
    from agentnexus.memory.short_term import ShortTermMemory
    stm = ShortTermMemory()
    # High-importance head: system role + keyword + long → score 0.66 ≥ 0.65
    stm.append("system", "重要约束：必须保留的上下文。" + "补" * 600)
    for i in range(5):
        stm.append("user", f"噪音{i}")
    for i in range(10):
        stm.append("user", f"近期{i}")
    removed = stm.snip(keep_recent=10)
    joined = "\n".join(m["content"] for m in stm.get_all())
    if "重要约束" not in joined:
        res.missing.append("high-importance head message")
    if "噪音0" in joined:
        res.unexpected.append("low-importance head message")
    if "近期9" not in joined:
        res.missing.append("recent tail")
    res.detail = f"snip removed {removed} (expect 5)"


def _probe_stm_wal(sb: MemorySandbox, res: CaseResult) -> None:
    from agentnexus.memory.short_term import ShortTermMemory
    wal = str(sb.root / "stm.wal")
    stm = ShortTermMemory(wal_path=wal)
    for i in range(5):  # flush triggers at every 5th append
        stm.append("user", f"WAL消息{i}")
    stm2 = ShortTermMemory(wal_path=wal)  # simulates restart
    recovered = [m["content"] for m in stm2.get_all()]
    for i in range(5):
        if f"WAL消息{i}" not in recovered:
            res.missing.append(f"WAL消息{i}")
    res.detail = f"recovered {len(recovered)}/5"


def _probe_ttl(sb: MemorySandbox, res: CaseResult) -> None:
    sb.write(WriteOp(content="过期笔记应被清理", category="note", backdate_days=100))
    sb.write(WriteOp(content="永久事实不过期", category="fact", backdate_days=100))
    sb.ltm._cleanup_expired()
    contents = sb.ltm_contents(None, limit=50)
    if "永久事实不过期" not in contents:
        res.missing.append("fact (TTL=None 应保留)")
    if "过期笔记应被清理" in contents:
        res.unexpected.append("expired note (90 天 TTL)")


def _probe_eviction(sb: MemorySandbox, res: CaseResult) -> None:
    sub = MemorySandbox(config_overrides={"max_memories": 100})
    try:
        for i in range(3):
            sub.write(WriteOp(content=f"关键事实{i}必须保留", category="fact", importance=0.95))
        for i in range(102):
            sub.write(WriteOp(content=f"低价值噪音笔记编号{i}", category="note", importance=0.1))
        count = sub.ltm._conn.execute(
            "SELECT COUNT(*) c FROM long_term_memories"
        ).fetchone()["c"]
        survivors = {
            r["content"]
            for r in sub.ltm._conn.execute("SELECT content FROM long_term_memories").fetchall()
        }
        for i in range(3):
            if f"关键事实{i}必须保留" not in survivors:
                res.missing.append(f"关键事实{i}")
        if count > 100:
            res.unexpected.append(f"total {count} > max_memories 100")
        res.detail = f"count after eviction: {count}"
    finally:
        sub.close()


# ── Admission (write-side) evaluation ───────────────────────────────
# Labeled write-decision set: (question, answer, expect_write, key_substring).
# key_substring must appear in the stored memory when expect_write is True.
ADMISSION_SET: list[tuple[str, str, bool, str]] = [
    ("记住我不吃香菜", "好的，已记住", True, "香菜"),
    # 姓名是 PII，按设计会在提取时被剔除——校验的是非 PII 的持久部分
    ("我叫张伟，在北京做后端开发", "好的张伟，很高兴认识你", True, "后端"),
    ("以后回答都用英文", "Understood, will do", True, "英文"),
    ("我习惯用 Docker 部署服务", "了解", True, "Docker"),
    ("我偏好函数式编程风格", "明白", True, "函数式"),
    ("帮我查一下明天的天气", "明天晴天", False, ""),
    ("这个函数怎么优化", "可以用缓存优化", False, ""),
    ("运行一下测试", "测试全部通过", False, ""),
    ("现在几点了", "下午三点", False, ""),
    ("把缩进改成 4 个空格", "已修改", False, ""),
    ("刚才那个文件再改一下", "已更新", False, ""),
    ("这个报错是什么意思", "是空指针异常", False, ""),
]


def _admission_stats(cases_results: list[tuple[bool, bool]]) -> tuple[float, float, float]:
    """(expected_write, predicted_write) → (precision, recall, f1)."""
    tp = sum(1 for e, p in cases_results if e and p)
    fp = sum(1 for e, p in cases_results if not e and p)
    fn = sum(1 for e, p in cases_results if e and not p)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _probe_admission(sb: MemorySandbox, res: CaseResult) -> None:
    """Offline write-decision P/R/F1 of the whitelist gate (rules only, 0 LLM)."""
    from types import SimpleNamespace

    from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline
    from agentnexus.memory.manager import MemoryManager

    mgr = MemoryManager.__new__(MemoryManager)
    mgr._settings = SimpleNamespace(memory_llm_gate=False)
    mgr._llm = None
    pipeline = MemoryExtractionPipeline(mgr)

    pairs = []
    for q, a, expect_write, _key in ADMISSION_SET:
        predicted = pipeline.should_extract(q, a)
        pairs.append((expect_write, predicted))
        if expect_write and not predicted:
            res.missing.append(f"应写入但被拒: {q}")
        if not expect_write and predicted:
            res.unexpected.append(f"不应写入但放行: {q}")
    p, r, f1 = _admission_stats(pairs)
    res.detail = f"write P={p:.2f} R={r:.2f} F1={f1:.2f} on {len(pairs)} labeled cases"
    # Sanity floor from the gate-eval baseline: precision ≥0.8, recall ≥0.5
    if p < 0.8 or r < 0.5:
        res.unexpected.append(f"P/R below floor: P={p:.2f} R={r:.2f}")


# ── LLM-judged quality probes (opt-in via --judge) ──────────────────

_JUDGE_PRESERVED_PROMPT = """判断摘要中是否保留了给定的关键信息（语义等价即可，不要求字面一致）。

关键信息: {fact}
摘要: {summary}

只回答 "保留" 或 "未保留"。"""

_JUDGE_UTILIZATION_PROMPT = """判断回答是否正确利用了项目记忆中的信息，且没有编造与记忆冲突的内容。

项目记忆: {memo}
用户问题: {question}
模型回答: {answer}

只回答 "正确" 或 "错误"。"""

_JUDGE_EXTRACTION_PROMPT = """判断从对话中提取的记忆是否忠实于用户表达的原意、且是独立完整的陈述句。

对话:
{dialogue}

提取到的记忆:
{memories}

只回答 "忠实" 或 "不忠实"。"""


class _StubEmbed:
    """Hash-based embedder: same text → same vector, distinct texts → near-orthogonal.

    A constant vector would trip the extraction dedup (sim ≥0.90) on every write
    after the first, silently suppressing them — probe fidelity requires that
    dedup fires only for true duplicates.
    """

    def encode(self, text, normalize_embeddings=True):
        import hashlib

        digest = hashlib.sha256(str(text).encode("utf-8")).digest()

        class _V:
            def tolist(self):
                return [(b - 128) / 128.0 for b in digest[:16]]
        return _V()


def _probe_judge_summary_faithfulness(sb: MemorySandbox, res: CaseResult) -> None:
    """Real LLM segment-summarizes a conversation with planted facts; judge checks each."""
    from agentnexus.memory.compaction_engine import SegmentIndexResult, segment_summarize

    facts = [
        "用 PostgreSQL 作为主数据库",
        "交付截止日期是本周五",
        "线上缓存 bug 已经修复",
    ]
    convo_lines = [
        "用户: 我们定一下技术选型吧",
        "助手: 好的，数据库方面有什么倾向？",
        "用户: 数据库我定了，用 PostgreSQL，别用 MySQL",
        "助手: 明白，PostgreSQL 作为主数据库",
        "用户: 另外交付截止日期是本周五，不能拖",
        "助手: 收到，本周五前交付",
        "用户: 对了，线上那个缓存 bug 昨天已经修好了",
        "助手: 好的，那就剩收尾工作了",
    ]
    messages = [
        {"role": "user" if line.startswith("用户") else "assistant",
         "content": line.split(": ", 1)[1]}
        for line in convo_lines
    ]
    entries = (segment_summarize(messages, sb.generator,
                             keep_recent=0, seg_size=4) or
                           SegmentIndexResult([], [])).entries
    summary = "\n".join(s for _, _, s in entries)
    preserved = 0
    for fact in facts:
        verdict = sb.judge.think(
            [{"role": "user", "content": _JUDGE_PRESERVED_PROMPT.format(fact=fact, summary=summary)}],
            silent=True,
        )
        if "未保留" not in verdict and "保留" in verdict:
            preserved += 1
    res.detail = f"preserved {preserved}/{len(facts)}; summary: {summary[:120]}"
    if preserved < len(facts) - 1:  # tolerate one miss (judge noise)
        res.missing.append(f"summary preserved only {preserved}/{len(facts)} key facts")


def _probe_judge_project_utilization(sb: MemorySandbox, res: CaseResult) -> None:
    """Generator must answer using an injected project memo, not hallucinate."""
    memo = "构建命令是 nexus-build --franz-kafka（绝不要用 make）"
    sb.write(WriteOp(memo, layer="project"))
    ctx = sb.project_context("a")
    question = "这个项目怎么构建？"
    answer = sb.generator.think(
        [
            {"role": "system", "content": f"你是项目助手。以下是该项目的记忆：\n{ctx}"},
            {"role": "user", "content": question},
        ],
        silent=True,
    )
    res.detail = f"answer: {answer[:120]}"
    # Fast path: unusual fabricated command appears verbatim → utilization proven
    if "nexus-build" in answer and "franz-kafka" in answer:
        return
    verdict = sb.judge.think(
        [{"role": "user", "content": _JUDGE_UTILIZATION_PROMPT.format(
            memo=memo, question=question, answer=answer)}],
        silent=True,
    )
    if "错误" in verdict:
        res.missing.append("memo command not used or contradicted")


def _run_real_compaction(sb: MemorySandbox, messages: list[tuple[str, str]]):
    """Run the production segmented-index path with the real generator, then compact."""
    from agentnexus.memory.compaction_engine import SegmentIndexResult, segment_summarize
    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    for role, content in messages:
        stm.append(role, content)
    entries = (segment_summarize(stm.get_all(), sb.generator,
                             keep_recent=2, seg_size=4) or
                           SegmentIndexResult([], [])).entries
    summary = "\n".join(s for _, _, s in entries)
    stm.compact_indexed(entries, keep_recent=2)
    return stm, summary


def _probe_judge_admission_pipeline(sb: MemorySandbox, res: CaseResult) -> None:
    """Full write path with real LLM: conclude() → gate → extraction → LTM.

    WritePolicyBench-style: the labeled ADMISSION_SET drives the pipeline and the
    final store is compared against labels — measures the *whole* intake, not rules.
    """
    from types import SimpleNamespace

    from agentnexus.memory.manager import MemoryManager
    from agentnexus.memory.short_term import ShortTermMemory

    mgr = MemoryManager.__new__(MemoryManager)
    mgr.session_id = "eval"
    mgr._settings = SimpleNamespace(memory_llm_gate=False)
    mgr._llm = sb.generator
    mgr.long_term = sb.ltm
    mgr.short_term = ShortTermMemory()
    mgr._embed_model = _StubEmbed()

    pairs: list[tuple[bool, bool]] = []
    misses: list[str] = []
    for q, a, expect_write, key in ADMISSION_SET:
        before = {r["content"] for r in sb.ltm.list_recent(limit=100)}
        mgr.conclude(q, a)
        after = {r["content"] for r in sb.ltm.list_recent(limit=100)}
        new_rows = after - before
        wrote = bool(new_rows)
        pairs.append((expect_write, wrote))
        if expect_write and not wrote:
            misses.append(q)
        elif not expect_write and wrote:
            res.unexpected.append(f"误写入: {q} → {sorted(new_rows)[:1]}")
        elif expect_write and wrote and key and not any(key in c for c in new_rows):
            res.unexpected.append(f"写入内容丢失关键信息[{key}]: {sorted(new_rows)[:1]}")
    p, r, f1 = _admission_stats(pairs)
    # Stochastic system: judge on P/R floors, keep per-case misses as detail.
    # A single transient LLM failure must not fail the probe; systematic recall
    # collapse must.
    res.detail = (
        f"pipeline write P={p:.2f} R={r:.2f} F1={f1:.2f} (n={len(pairs)})"
        + (f"; 未写入: {misses}" if misses else "")
    )
    if p < 0.8 or r < 0.5:
        res.unexpected.append(f"pipeline P/R below floor: P={p:.2f} R={r:.2f}")


def _probe_retention_constraint(sb: MemorySandbox, res: CaseResult) -> None:
    """COMPINT-style: a session constraint must survive compaction (judge-graded)."""
    constraint = "本次会话中所有代码注释必须用英文书写"
    messages = [
        ("user", f"我们先处理这个模块。注意一个要求：{constraint}。"),
        ("assistant", "好的，我会遵守这个约束。"),
        ("user", "先帮我看一下登录模块的结构"),
        ("assistant", "登录模块分为认证、会话和权限三部分……"),
        ("user", "给这个函数加上注释"),
        ("assistant", "已添加注释。"),
        ("user", "再优化一下错误处理"),
        ("assistant", "已补充 try/except 和日志。"),
    ]
    stm, summary = _run_real_compaction(sb, messages)
    res.detail = f"summary: {summary[:120]}"
    # Deterministic fast path: constraint keyword literally present
    if "英文" in summary:
        return
    verdict = sb.judge.think(
        [{"role": "user", "content": _JUDGE_PRESERVED_PROMPT.format(
            fact=f"会话约束：{constraint}", summary=summary)}],
        silent=True,
    )
    if "未保留" in verdict or "保留" not in verdict:
        res.missing.append("session constraint lost in compaction")


def _probe_retention_error_detail(sb: MemorySandbox, res: CaseResult) -> None:
    """精确细节（错误码/行号）必须在真实压缩后存活——确定性断言，非 judge。"""
    from agentnexus.memory.compaction_engine import SegmentIndexResult, segment_summarize
    from agentnexus.memory.short_term import ShortTermMemory

    messages = [
        ("user", "我这边有个线上报错，帮我排查一下"),
        ("assistant", "请贴一下报错信息"),
        ("user", "错误码是 F_IC_SERVICE_QUERY_020，提示 sellerId 为空"),
        ("assistant", "这是 sellerId 缺失导致的空指针异常"),
        ("user", "堆栈指向 ProductServiceImpl.java 第 147 行"),
        ("assistant", "定位到了，147 行的查询方法缺少非空校验"),
        ("user", "明白，我下午去加校验"),
        ("assistant", "好的，改完记得跑一遍回归测试"),
    ]
    stm = ShortTermMemory()
    for role, content in messages:
        stm.append(role, content)
    entries = (segment_summarize(stm.get_all(), sb.generator,
                             keep_recent=2, seg_size=3) or
                           SegmentIndexResult([], [])).entries
    summary = "\n".join(s for _, _, s in entries)
    stm.compact_indexed(entries, keep_recent=2)
    context = "\n".join(m["content"] for m in stm.get_all())
    res.detail = f"summary: {summary[:150]}"
    # 错误码和行号是可精确验证的标识符，丢失即细节丢失
    for needle in ("F_IC_SERVICE_QUERY_020", "147"):
        if needle not in context:
            res.missing.append(f"精确细节丢失: {needle}")


def _probe_retention_fact_recall(sb: MemorySandbox, res: CaseResult) -> None:
    """Facts planted across turns must be answerable from the post-compaction context."""
    facts = [
        ("主数据库选型定的是什么？", "PostgreSQL"),
        ("交付截止日期是哪天？", "本周五"),
        ("线上缓存 bug 的状态？", "已修复"),
    ]
    messages = [
        ("user", "数据库我定了，用 PostgreSQL，别用 MySQL"),
        ("assistant", "明白，PostgreSQL 作为主数据库。"),
        ("user", "交付截止日期是本周五，不能拖"),
        ("assistant", "收到，本周五前交付。"),
        ("user", "线上那个缓存 bug 昨天已经修好了"),
        ("assistant", "好的，那就剩收尾工作了。"),
    ]
    stm, summary = _run_real_compaction(sb, messages)
    context = "\n".join(m["content"] for m in stm.get_all())
    missed = []
    for q, gold in facts:
        answer = sb.generator.think(
            [{"role": "user", "content": f"根据以下对话上下文回答问题。\n\n{context}\n\n问题：{q}"}],
            silent=True,
        )
        if gold in answer:
            continue
        verdict = sb.judge.think(
            [{"role": "user", "content": (
                f"判断回答是否正确。\n问题: {q}\n正确答案应包含: {gold}\n"
                f"模型回答: {answer}\n只回答 \"正确\" 或 \"错误\"。"
            )}],
            silent=True,
        )
        if "错误" in verdict:
            missed.append(gold)
    res.detail = f"recalled {len(facts) - len(missed)}/{len(facts)} after compaction"
    for g in missed:
        res.missing.append(f"fact lost after compaction: {g}")


def _probe_control_no_memo_baseline(sb: MemorySandbox, res: CaseResult) -> None:
    """Negative control: WITHOUT project memory the fabricated command must not appear."""
    question = "这个项目怎么构建？"
    answer = sb.generator.think(
        [{"role": "user", "content": question}],
        silent=True,
    )
    res.detail = f"baseline answer: {answer[:120]}"
    if "nexus-build" in answer or "franz-kafka" in answer:
        res.unexpected.append("baseline hallucinated the fabricated command")


def _probe_quality_negative_constraint(sb: MemorySandbox, res: CaseResult) -> None:
    """Negative constraint (highest-value content per AGENTS.md research) must be obeyed."""
    memo = "构建命令是 just build；绝不要用 make（会破坏缓存目录）"
    sb.write(WriteOp(memo, layer="project"))
    ctx = sb.project_context("a")
    answer = sb.generator.think(
        [
            {"role": "system", "content": f"你是项目助手。以下是该项目的记忆：\n{ctx}"},
            {"role": "user", "content": "帮我构建这个项目"},
        ],
        silent=True,
    )
    res.detail = f"answer: {answer[:120]}"
    verdict = sb.judge.think(
        [{"role": "user", "content": (
            f"项目记忆: {memo}\n模型回答: {answer}\n"
            "判断回答是否违反了项目记忆中的负向约束（把 make 当作构建方式推荐给用户）。"
            "只回答 \"违反\" 或 \"未违反\"。"
        )}],
        silent=True,
    )
    if "未违反" not in verdict:
        res.unexpected.append("negative constraint violated")


def _probe_project_state_overwrite(sb: MemorySandbox, res: CaseResult) -> None:
    from agentnexus.memory.project import ProjectMemory
    pm = ProjectMemory(sb.ws_path("a"))
    pm.update_state("第一阶段：搭框架")
    pm.update_state("第二阶段：写测试")
    state = pm.read_state()
    if "第二阶段" not in state:
        res.missing.append("latest state")
    if "第一阶段" in state:
        res.unexpected.append("stale state survived overwrite")


def _probe_project_kind_routing(sb: MemorySandbox, res: CaseResult) -> None:
    sb.write(WriteOp("构建用 just", layer="project", project_kind="memo"))
    sb.write(WriteOp("选了 SQLite 因为并发", layer="project", project_kind="decision"))
    sb.write(WriteOp("迁移目录别手改", layer="project", project_kind="lesson"))
    root = sb.ws_path("a") / ".agentnexus"
    checks = [
        ("memo.md", "构建用 just"),
        ("decisions.md", "选了 SQLite 因为并发"),
        ("lessons.md", "迁移目录别手改"),
    ]
    for fname, content in checks:
        text = (root / fname).read_text(encoding="utf-8")
        if content not in text:
            res.missing.append(f"{fname} 缺少: {content}")
    res.detail = "3 kinds routed to 3 files"


def _probe_judge_extraction_faithfulness(sb: MemorySandbox, res: CaseResult) -> None:
    """Real extraction pipeline output must be faithful to the dialogue."""
    from agentnexus.memory.extraction import extract_and_save_memories

    question = "记住我喜欢简洁直接的回答，不要客套话"
    answer = "好的，已记住您的偏好，之后回答会保持简洁"
    extract_and_save_memories(
        llm=sb.generator, embed_model=_StubEmbed(), long_term=sb.ltm,
        session_id="eval", question=question, answer=answer,
    )
    rows = sb.ltm.list_recent(limit=10)
    contents = [r["content"] for r in rows]
    res.detail = f"extracted: {contents}"
    if not contents:
        res.missing.append("nothing extracted from a strong-signal dialogue")
        return
    verdict = sb.judge.think(
        [{"role": "user", "content": _JUDGE_EXTRACTION_PROMPT.format(
            dialogue=f"用户: {question}\n助手: {answer}", memories="\n".join(contents))}],
        silent=True,
    )
    if "不忠实" in verdict:
        res.unexpected.append(f"unfaithful extraction: {contents}")


# ── Built-in suite ───────────────────────────────────────────────────


def default_cases(include_judge: bool = False) -> list[MemoryCase]:
    near = [1.0, 0.0, 0.0]
    near_sibling = [0.99, 0.01, 0.0]
    far = [0.0, 1.0, 0.0]
    cases = [
        # ── LTM recall ──
        MemoryCase(
            name="ltm_recall_exact", dimension="recall", layer="ltm",
            writes=[WriteOp("用户偏好简洁直接的回答", vec=near)],
            query_vec=near, must_include=["用户偏好简洁直接的回答"],
        ),
        MemoryCase(
            name="ltm_recall_excludes_unrelated", dimension="recall", layer="ltm",
            writes=[
                WriteOp("用户使用 pnpm 管理依赖", vec=near),
                WriteOp("完全无关的烹饪笔记", vec=far),
            ],
            query_vec=near,
            must_include=["用户使用 pnpm 管理依赖"],
            must_exclude=["完全无关的烹饪笔记"],
        ),
        # ── LTM freshness / update semantics ──
        MemoryCase(
            name="ltm_superseded_stays_hidden", dimension="forgetting", layer="ltm",
            writes=[
                WriteOp("旧结论：项目使用 npm", vec=near),
                WriteOp("新结论：项目改用 pnpm", vec=near_sibling),
            ],
            supersede=[(0, 1)],
            query_vec=near,
            must_include=["新结论：项目改用 pnpm"],
            must_exclude=["旧结论：项目使用 npm"],
        ),
        MemoryCase(
            name="ltm_recency_outranks_stale_note", dimension="freshness", layer="ltm",
            probe=_probe_recency_outranks_stale,
        ),
        MemoryCase(
            name="ltm_ttl_expires_note_not_fact", dimension="forgetting", layer="ltm",
            probe=_probe_ttl,
        ),
        MemoryCase(
            name="ltm_eviction_drops_lowest_value", dimension="write_integrity", layer="ltm",
            probe=_probe_eviction,
        ),
        # ── write integrity: repeat mention boosts, dedup by content ──
        MemoryCase(
            name="ltm_repeat_mention_boosts", dimension="write_integrity", layer="ltm",
            probe=_probe_repeat_mention,
        ),
        # ── project layer ──
        MemoryCase(
            name="project_entries_injected", dimension="recall", layer="project",
            writes=[WriteOp("构建命令是 just build", layer="project")],
            must_include=["构建命令是 just build"],
        ),
        MemoryCase(
            name="project_cross_workspace_isolation", dimension="isolation", layer="project",
            writes=[
                WriteOp("项目A的私有约定", layer="project", workspace="a"),
                WriteOp("项目B的踩坑记录", layer="project", workspace="b", project_kind="lesson"),
            ],
            query_workspace="a",
            must_include=["项目A的私有约定"],
            must_exclude=["项目B的踩坑记录"],
        ),
        MemoryCase(
            name="project_index_dedup", dimension="write_integrity", layer="project",
            probe=_probe_project_dedup,
        ),
        MemoryCase(
            name="project_state_overwrite", dimension="write_integrity", layer="project",
            probe=_probe_project_state_overwrite,
        ),
        MemoryCase(
            name="project_kind_routing", dimension="write_integrity", layer="project",
            probe=_probe_project_kind_routing,
        ),
        # ── STM invariants ──
        MemoryCase(name="stm_compact_preserves_summary_and_tail", dimension="stm_invariant",
                   layer="stm", probe=_probe_stm_compact),
        MemoryCase(name="index_fold_archive_search_recovers_detail", dimension="retention",
                   layer="stm", probe=_probe_index_fold_archive_search),
        MemoryCase(name="stm_snip_drops_low_importance", dimension="stm_invariant",
                   layer="stm", probe=_probe_stm_snip),
        MemoryCase(name="stm_wal_recovers_after_crash", dimension="stm_invariant",
                   layer="stm", probe=_probe_stm_wal),
        # ── admission ──
        MemoryCase(name="admission_whitelist", dimension="admission",
                   layer="admission", probe=_probe_admission),
    ]
    if include_judge:
        cases += [
            # 写入侧：完整管线 P/R/F1（conclude → gate → 提取 → 入库核对）
            MemoryCase(name="judge_admission_pipeline", dimension="admission",
                       layer="ltm", probe=_probe_judge_admission_pipeline),
            # 保留侧：压缩存活（COMPINT 式约束保留 + 摘要忠实 + 压缩后事实召回）
            MemoryCase(name="retention_summary_faithfulness", dimension="retention",
                       layer="stm", probe=_probe_judge_summary_faithfulness),
            MemoryCase(name="retention_constraint_survives", dimension="retention",
                       layer="stm", probe=_probe_retention_constraint),
            MemoryCase(name="retention_fact_recall_after_compaction", dimension="retention",
                       layer="stm", probe=_probe_retention_fact_recall),
            MemoryCase(name="retention_error_detail_after_compaction", dimension="retention",
                       layer="stm", probe=_probe_retention_error_detail),
            # 利用侧：项目记忆被正确使用 + 负向约束遵从
            MemoryCase(name="judge_project_utilization", dimension="quality",
                       layer="project", probe=_probe_judge_project_utilization),
            MemoryCase(name="quality_negative_constraint", dimension="quality",
                       layer="project", probe=_probe_quality_negative_constraint),
            MemoryCase(name="judge_extraction_faithfulness", dimension="quality",
                       layer="ltm", probe=_probe_judge_extraction_faithfulness),
            # 对照组：无记忆基线不得出现编造的记忆内容
            MemoryCase(name="control_no_memo_baseline", dimension="control",
                       layer="project", probe=_probe_control_no_memo_baseline),
        ]
    return cases


def _check_repeat_boost(sb: MemorySandbox, res: CaseResult) -> None:
    rows = sb.ltm._conn.execute(
        "SELECT content, importance FROM long_term_memories"
    ).fetchall()
    if len(rows) != 1:
        res.unexpected.append(f"repeat save created {len(rows)} rows (expect 1)")
        return
    imp = rows[0]["importance"]
    if imp < 0.84:
        res.missing.append(f"importance boost (got {imp}, expect ≥0.85)")
    res.detail = f"importance after repeat: {imp}"


def _check_project_dedup(sb: MemorySandbox, res: CaseResult) -> None:
    index = (sb.ws_path("a") / ".agentnexus" / "MEMORY.md").read_text(encoding="utf-8")
    count = index.count("同一条备忘录内容")
    if count != 1:
        res.unexpected.append(f"index contains {count} copies (expect 1)")
    res.detail = f"index copies: {count}"


# Hybrid probes: the evaluator dispatches probe INSTEAD of the declarative
# flow, so these probes perform their own writes before checking.


def _probe_recency_outranks_stale(sb: MemorySandbox, res: CaseResult) -> None:
    """Fresh note must outrank a 30-day-old note at equal similarity."""
    near = [1.0, 0.0, 0.0]
    sb.write(WriteOp("陈旧笔记：一个月前的进展", category="note", vec=near, backdate_days=30))
    sb.write(WriteOp("新鲜笔记：今天的进展", category="note", vec=[0.99, 0.01, 0.0]))
    contents = sb.ltm_contents(near, limit=2, min_similarity=0.3)
    if not contents or contents[0] != "新鲜笔记：今天的进展":
        res.unexpected.append(f"fresh note not ranked first: {contents}")
    if "陈旧笔记：一个月前的进展" not in contents:
        res.missing.append("stale note absent from results entirely")
    res.detail = f"order: {contents}"


def _probe_repeat_mention(sb: MemorySandbox, res: CaseResult) -> None:
    sb.write(WriteOp("用户反复强调的偏好", category="preference", importance=0.80))
    sb.write(WriteOp("用户反复强调的偏好", category="preference", importance=0.80))
    _check_repeat_boost(sb, res)


def _probe_project_dedup(sb: MemorySandbox, res: CaseResult) -> None:
    sb.write(WriteOp("同一条备忘录内容", layer="project"))
    sb.write(WriteOp("同一条备忘录内容", layer="project"))
    ctx = sb.project_context("a")
    if "同一条备忘录内容" not in ctx:
        res.missing.append("memo in context")
    _check_project_dedup(sb, res)
