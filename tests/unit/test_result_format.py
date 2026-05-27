from agentnexus.tools.result_format import summarize_tool_result


def test_summarize_plain_string():
    assert summarize_tool_result("hello") == "hello"


def test_summarize_structured_result_without_preview():
    result = {
        "status": "ok",
        "message": "[file_write] 已创建 a.txt",
    }
    assert summarize_tool_result(result) == "[file_write] 已创建 a.txt"


def test_summarize_structured_result_with_preview():
    result = {
        "status": "ok",
        "message": "[file_write] 已覆盖 a.txt",
        "preview": {
            "text": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new"
        },
    }
    text = summarize_tool_result(result)
    assert "[file_write] 已覆盖 a.txt" in text
    assert "Diff preview:" in text
    assert "@@ -1 +1 @@" in text
