from agentnexus.skills import registry as registry_module

_as_str_list = registry_module._as_str_list
_title_from_markdown = registry_module._title_from_markdown
_description_from_markdown = registry_module._description_from_markdown


class TestAsStrList:
    def test_single_string(self):
        assert _as_str_list("hello") == ["hello"]

    def test_list_of_strings(self):
        assert _as_str_list(["a", "b"]) == ["a", "b"]

    def test_none_returns_empty(self):
        assert _as_str_list(None) == []

    def test_empty_list(self):
        assert _as_str_list([]) == []


class TestTitleFromMarkdown:
    def test_finds_h1(self):
        assert _title_from_markdown("# Title\n\nDescription") == "Title"

    def test_finds_h1_with_alternative_syntax(self):
        assert _title_from_markdown("= Title =") == ""

    def test_no_title_returns_empty(self):
        assert _title_from_markdown("Just text") == ""


class TestDescriptionFromMarkdown:
    def test_after_h1_and_blank_line(self):
        text = "# Title\n\nContent after first heading and blank line is description"
        result = _description_from_markdown(text)
        assert result == "Content after first heading and blank line is description"

    def test_no_heading(self):
        assert _description_from_markdown("Plain description text.") == "Plain description text."

    def test_empty(self):
        assert _description_from_markdown("") == ""
