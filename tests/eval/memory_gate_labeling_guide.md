# Memory Gate Labeling Guide

## Purpose

This guide defines the annotation standard for evaluating the LLM memory gate (`memory_gate.txt`). Each sample is labeled `yes` (worth long-term memory) or `no` (not worth it).

## Decision Criterion

**Core question:** Would this information help the AI provide better service in a *future, unrelated* session?

## Label "yes" — Stable, cross-session valuable

| Category | Examples |
|----------|----------|
| User identity | "我在北京做后端开发", "我叫张三", "我是学生" |
| Persistent preference | "我喜欢用 Vim", "以后回答简短点", "我不吃香菜" |
| Confirmed fact/decision | "我们决定用 PostgreSQL", "项目用 Python 3.12" |
| Recurring error pattern | "这个库在 Windows 上总是报路径错误，需要用 Path 处理" |

## Label "no" — Transient, single-session only

| Category | Examples |
|----------|----------|
| One-time task state | "我在写期末大作业", "帮我把这个函数改一下" |
| Transactional Q&A | "怎么用 grep", "Python 排序怎么写" |
| Greetings/filler | "好的谢谢", "明白了" |
| Tool output echo | "文件已移动", "测试通过了" |

## Borderline

If unsure, label as `borderline`. Borderline samples are excluded from accuracy/recall/precision calculations.

## Notes

- Short answers are NOT automatically "no". "记住了" in response to "我喜欢用 Vim" → the *conversation* is worth remembering.
- Judge the **information content**, not the answer length.
- When the user states a preference but the AI hasn't confirmed, still label "yes" — the preference itself is valuable.
