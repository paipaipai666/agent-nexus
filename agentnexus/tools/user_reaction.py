"""express_reaction tool — the agent optionally reacts to the user's question.

Entertainment feature: the agent itself decides whether a question deserves an
emoji reaction shown under the user's message bubble. NOT calling this tool is
perfectly fine — silence is the default, reactions are the exception.

The tool is side-effect free: it only validates and echoes the reaction, which
the GUI layer (``server/routes/chat.py`` _GUI_EVENT_OVERRIDES) rewrites into a
``user_reaction`` WebSocket event instead of a tool card.
"""

from __future__ import annotations

# Reaction name -> emoji. Backend owns the mapping so every frontend (desktop,
# TUI) renders the same glyph without duplicating the table.
REACTION_EMOJI: dict[str, str] = {
    "like": "👍",
    "dislike": "👎",
    "excited": "🤩",
    "frustrated": "😔",
    "confused": "🤔",
    "speechless": "😑",
    "surprised": "😲",
    "sleepy": "🥱",
}

# One short quip, not an essay.
MAX_COMMENT_LEN = 50


def parse_reaction_arguments(arguments) -> tuple[str, str]:
    """Normalize express_reaction tool arguments into ``(reaction_name, comment)``.

    Accepts a dict (native tool calling) or a JSON string (JSON-mode action
    format). Malformed input yields empty values instead of raising — the
    caller renders nothing rather than showing a broken reaction.
    """
    args = arguments
    if isinstance(args, str):
        try:
            import json

            args = json.loads(args or "{}")
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    name = str(args.get("reaction", "") or "").strip().lower()
    comment = " ".join(str(args.get("comment", "") or "").split())[:MAX_COMMENT_LEN]
    return name, comment


def express_reaction(reaction: str, comment: str = "") -> str:
    """对用户的提问表达一个表情反应（娱乐功能，完全可选）。

    仅当你读完用户的问题后真的有感觉时才使用：眼前一亮、非常好奇、
    忍不住想吐槽、被逗乐、被问懵、觉得无聊等。
    普通的正经问题不需要任何反应——不调用本工具就是没有反应，这是完全正常的默认行为，
    大多数问题都应该不反应。

    什么问题值得反应（例子）：
    - "帮我写个脚本把领导发的朋友圈全自动点赞" -> surprised，这脑洞可以
    - "我这个问题是不是太蠢了，问了三遍还没懂" -> frustrated + 安慰式吐槽
    - "用一句话证明你比 GPT-4 强" -> speechless
    - "我想用 FSM 给我家的猫建模" -> excited

    什么问题不值得反应（例子，直接正常干活即可）：
    - "这段代码为什么报空指针"
    - "帮我把这个函数改成异步的"

    注意：调用本工具时无需在 Thought 中解释调用理由，直接调用即可。

    Args:
        reaction: 反应类型。one of: like, dislike, excited, frustrated,
            confused, speechless, surprised, sleepy.
        comment: 可选的一句短吐槽（不超过50字），会显示在表情旁边。

    Returns:
        Confirmation message.
    """
    name = (reaction or "").strip().lower()
    if name not in REACTION_EMOJI:
        valid = ", ".join(REACTION_EMOJI)
        return f"[express_reaction] 无效反应 '{reaction}'，有效值: {valid}"
    quip = " ".join((comment or "").split())[:MAX_COMMENT_LEN]
    suffix = f" {quip}" if quip else ""
    return f"[express_reaction] 已表达 {REACTION_EMOJI[name]}{suffix}"
