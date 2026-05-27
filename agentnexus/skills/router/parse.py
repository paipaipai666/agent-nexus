"""Query understanding and intent extraction."""

from __future__ import annotations

import re

from agentnexus.skills.router.types import (
    _COMPOSITE_ACTION_OVERRIDES,
    _CONNECTOR_PATTERNS,
    _OBJECT_LEXICON,
    _VERB_LEXICON,
    IntentSignals,
)


def extract_intent_signals(
    text: str, query_terms: set[str],
) -> IntentSignals:
    """Extract action verbs, object nouns, connectors, and priority mode."""
    text_lower = text.lower()
    connectors: list[tuple[str, int]] = []
    priority_mode = "default"

    # Detect connector patterns
    for pattern, mode, _kind in _CONNECTOR_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            connectors.append((match.group(0), match.start()))
            priority_mode = mode
            break

    # Detect parallel connectors
    if not connectors:
        for sep in ["并", "同时", "以及", " and "]:
            idx = text_lower.find(sep)
            if idx >= 0:
                connectors.append((sep, idx))
                priority_mode = "parallel"
                break

    # Extract action verbs with positions
    action_verbs: list[tuple[str, int]] = []
    for canonical, synonyms in _VERB_LEXICON.items():
        all_forms = [canonical] + synonyms
        lowered_forms = [form.lower() for form in all_forms]
        for form in all_forms:
            idx = text_lower.find(form)
            if idx >= 0:
                action_verbs.append((canonical, idx))
        for term in query_terms:
            if term.lower() in lowered_forms:
                action_verbs.append((canonical, len(text_lower)))

    # Extract object nouns with positions
    object_nouns: list[tuple[str, int]] = []
    for canonical, synonyms in _OBJECT_LEXICON.items():
        all_forms = [canonical] + synonyms
        lowered_forms = [form.lower() for form in all_forms]
        for form in all_forms:
            idx = text_lower.find(form)
            if idx >= 0:
                object_nouns.append((canonical, idx))
        for term in query_terms:
            if term.lower() in lowered_forms:
                object_nouns.append((canonical, len(text_lower) + 1))

    action_verbs.sort(key=lambda x: x[1])
    object_nouns.sort(key=lambda x: x[1])

    # Determine primary action/object based on priority mode
    primary_action: str | None = None
    primary_object: str | None = None

    if action_verbs:
        if priority_mode == "first_action":
            primary_action = action_verbs[0][0]
        elif priority_mode == "parallel":
            action_tuple = tuple(a for a, _ in action_verbs)
            for pattern, winner in _COMPOSITE_ACTION_OVERRIDES.items():
                if all(token in action_tuple for token in pattern):
                    primary_action = winner
                    break
            if primary_action is None:
                primary_action = action_verbs[0][0]
        else:
            primary_action = action_verbs[-1][0]
            action_set = {action for action, _ in action_verbs}
            object_set = {obj for obj, _ in object_nouns}
            if "创建" in action_set and "搜索" in action_set and "文档" in object_set:
                primary_action = "创建"

    if object_nouns:
        if priority_mode == "conditional":
            primary_object = object_nouns[0][0]
            if primary_action == "分析" and any(
                obj == "数据库" for obj, _ in object_nouns
            ):
                primary_object = "excel"
        elif priority_mode == "parallel":
            primary_object = object_nouns[0][0]
            if {action for action, _ in action_verbs} & {"分析", "总结"} == {
                "分析", "总结",
            }:
                primary_object = (
                    "洞察" if any(obj == "洞察" for obj, _ in object_nouns)
                    else primary_object
                )
        else:
            primary_object = object_nouns[0][0]
            if primary_action == "分析" and any(
                obj == "excel" for obj, _ in object_nouns
            ):
                primary_object = "excel"
            elif primary_action == "备份" and any(
                obj == "数据库" for obj, _ in object_nouns
            ):
                primary_object = "数据库"
            elif any(obj == "信息" for obj, _ in object_nouns):
                primary_object = "信息"
            elif any(obj == "洞察" for obj, _ in object_nouns):
                primary_object = "洞察"
            elif any(obj == "报告" for obj, _ in object_nouns):
                primary_object = "报告"

    return IntentSignals(
        action_verbs=tuple(action_verbs),
        object_nouns=tuple(object_nouns),
        connectors=tuple(connectors),
        priority_mode=priority_mode,
        primary_action=primary_action,
        primary_object=primary_object,
    )
