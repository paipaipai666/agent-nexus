"""Gate tests: the repo stays comment-free and the scanners behave."""

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check_no_comments.py"

spec = importlib.util.spec_from_file_location("check_no_comments", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_repo_has_no_comments():
    proc = subprocess.run(
        [sys.executable, str(GATE), str(REPO_ROOT)],
        capture_output=True, text=False,
        cwd=str(REPO_ROOT),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"comment gate failed:\n{out}"


def test_python_scanner_flags_prose_but_keeps_directives():
    bad = "x = 1  # increment the counter\n# standalone note\n"
    good = (
        "#!/usr/bin/env python\n"
        "x = 1  # type: ignore[attr-defined]\n"
        "y = 2  # noqa\n"
        "z = 3  # pragma: no cover\n"
    )
    assert len(gate.scan_python(bad)) == 2
    assert gate.scan_python(good) == []


def test_python_scanner_ignores_strings():
    src = 's = """\n# not a comment\n"""\nt = "a # b"\n'
    assert gate.scan_python(src) == []


def test_js_scanner_flags_prose_but_keeps_directives():
    bad = "const a = 1; // why this works\n/* block */\n"
    good = (
        "/// <reference types=\"vitest\" />\n"
        "// eslint-disable-next-line react-hooks/exhaustive-deps\n"
        "call(/* @__PURE__ */ fn());\n"
        "// @ts-expect-error -- intentional\n"
    )
    assert len(gate.scan_js(bad)) == 2
    assert gate.scan_js(good) == []


def test_js_scanner_ignores_strings_templates_and_urls():
    src = (
        'const a = "https://example.com//x";\n'
        "const b = `tpl ${ /* real */ v } text // not code`;\n"
        "const re = /https?:\\/\\//;\n"
        "fetch('https://api.example.com');\n"
    )
    findings = gate.scan_js(src)
    assert len(findings) == 1
    assert "real" in findings[0][1]


def test_js_scanner_division_is_not_regex():
    src = "const r = a / b / c;\nif (x > 0) { y = x / 2; }\n"
    assert gate.scan_js(src) == []


def test_css_scanner():
    assert len(gate.scan_css("a { color: red; } /* note */\n")) == 1
    assert gate.scan_css('.x { content: "/*"; }\n') == []


def test_yaml_block_scalar_content_is_not_comment():
    src = "run: |\n  echo hello\n  # shell-looking line is content\nkey: value\n"
    assert gate.scan_hash(src, "yaml") == []
    assert len(gate.scan_hash("key: value  # explain\n", "yaml")) == 1


def test_toml_strings_and_comments():
    good = 'a = "# not comment"\nb = """\n# doc\n"""\n'
    bad = "a = 1 # why\n"
    assert gate.scan_hash(good, "toml") == []
    assert len(gate.scan_hash(bad, "toml")) == 1


def test_gitignore_only_full_line_comments():
    assert len(gate.scan_hash("# section\n*.log\n", "gitignore")) == 1
    assert gate.scan_hash("*.log\nfoo\\#bar\n", "gitignore") == []
