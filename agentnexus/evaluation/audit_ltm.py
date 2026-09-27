"""LTM store audit — production gate calibration (GateCal P3).

Answers, from the real SQLite store, three questions the benchmark cannot:

1. 线上真实松紧：what categories actually entered, and how much of it
   carries a strong-signal token (i.e. could have passed the rules layer)?
2. 旁路率：which writers (session prefixes / system merge / tests) produced
   the rows — gate path vs bypass path attribution.
3. 标注采样：a deterministic stratified CSV sample for human/LLM labeling
   to compute production P_write.

Read-only: opens the DB with mode=ro, never writes.
"""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter
from pathlib import Path

from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline

_STRONG = tuple(MemoryExtractionPipeline._STRONG_SIGNALS)

# session_id prefix → write-path attribution. Rows from tui_/server_/session_
# may have entered via the conclude() gate OR the memory_save tool; SQLite has
# no per-row provenance, so that bucket is reported as "runtime (ambiguous)".
_WRITERS = {
    "perf_test": "test",
    "system": "merge/compaction",
    "eval": "eval probe",
}


def _connect_ro(db_path: str) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _writer(session_id: str) -> str:
    for prefix, writer in _WRITERS.items():
        if session_id.startswith(prefix):
            return writer
    return "runtime (gate/memory_save, ambiguous)"


def audit_store(db_path: str, recent_days: int = 30) -> str:
    """Print store-wide audit. Returns the report text."""
    conn = _connect_ro(db_path)
    lines: list[str] = [f"audit: {db_path}"]
    try:
        total = conn.execute("SELECT COUNT(*) c FROM long_term_memories").fetchone()["c"]
        lines.append(f"total memories: {total}")
        if not total:
            return "\n".join(lines)

        lines.append("\nby category:")
        for r in conn.execute(
            "SELECT category, COUNT(*) c, AVG(importance) ai "
            "FROM long_term_memories GROUP BY category ORDER BY c DESC"
        ):
            lines.append(f"  {r['category']:<16} n={r['c']:<5} avg_importance={r['ai']:.2f}")

        lines.append("\nby writer (session_id prefix):")
        writers = Counter(
            _writer(r["session_id"])
            for r in conn.execute("SELECT session_id FROM long_term_memories").fetchall()
        )
        for w, c in writers.most_common():
            lines.append(f"  {w:<42} {c:<6} ({c / total:.1%})")

        sig = conn.execute(
            "SELECT COUNT(*) c FROM long_term_memories WHERE "
            + " OR ".join("content LIKE ?" for _ in _STRONG),
            tuple(f"%{t}%" for t in _STRONG),
        ).fetchone()["c"]
        lines.append(f"\nstrong-signal token in content: {sig}/{total} ({sig / total:.1%})")
        lines.append("  ↳ rows WITHOUT one never passed the rules whitelist;"
                     " they came via the LLM gate or a bypass path")

        recent = conn.execute(
            "SELECT COUNT(*) c FROM long_term_memories "
            "WHERE created_at > datetime('now', ?)",
            (f"-{recent_days} days",),
        ).fetchone()["c"]
        lines.append(f"rows last {recent_days}d: {recent}")
    finally:
        conn.close()
    return "\n".join(lines)


def sample_for_labeling(db_path: str, out_csv: str, n_per_category: int = 10) -> int:
    """Deterministic stratified sample (every-kth by id) → CSV for labeling.

    Columns: id, category, importance, session_id, created_at, content,
    worth_remembering (empty, to fill). Returns row count written.
    """
    conn = _connect_ro(db_path)
    written = 0
    try:
        categories = [
            r["category"]
            for r in conn.execute(
                "SELECT category, COUNT(*) c FROM long_term_memories "
                "GROUP BY category HAVING c > 0 ORDER BY c DESC"
            )
        ]
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["id", "category", "importance", "session_id",
                        "created_at", "content", "worth_remembering"])
            for cat in categories:
                rows = conn.execute(
                    "SELECT id, category, importance, session_id, created_at, content "
                    "FROM long_term_memories WHERE category = ? ORDER BY id",
                    (cat,),
                ).fetchall()
                stride = max(1, len(rows) // n_per_category)
                for r in rows[::stride][:n_per_category]:
                    w.writerow([r["id"], r["category"], r["importance"],
                                r["session_id"], r["created_at"], r["content"], ""])
                    written += 1
    finally:
        conn.close()
    return written


def main(db_path: str | None = None, sample_csv: str | None = None,
         n_per_category: int = 10) -> None:
    from agentnexus.core.config import get_settings
    db_path = db_path or get_settings().memory_db_path
    print(audit_store(db_path))
    if sample_csv:
        n = sample_for_labeling(db_path, sample_csv, n_per_category)
        print(f"\nsample written: {n} rows -> {sample_csv}")


if __name__ == "__main__":
    # Real CLI: `agentnexus eval memory-audit`. Bare module run = store audit only.
    main()
