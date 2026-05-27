"""Skill discovery for AgentNexus sessions.

The public skill format is a directory with a required ``SKILL.md`` file.
Legacy workflow YAML manifests are still accepted as a compatibility format
and are converted into the same internal workflow/session profile model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from agentnexus.core.config import Settings
from agentnexus.skills.profile import validate_session_profile
from agentnexus.skills.workflow import (
    MemoryPolicy,
    PromptProfile,
    RetrievalPolicy,
    SkillResource,
    ToolPolicy,
    Workflow,
    WorkflowLoader,
    WorkflowStep,
)

WORKFLOW_PATTERNS = (
    "workflow.yaml",
    "workflow.yml",
    "*.workflow.yaml",
    "*.workflow.yml",
)
SKILL_MD_NAME = "SKILL.md"
RESOURCE_DIRS = {
    "scripts": "script",
    "references": "reference",
    "assets": "asset",
}
MAX_RESOURCE_COUNT = 200
MAX_SKILL_BODY_LINES = 500


@dataclass(frozen=True)
class SkillEntry:
    """A discovered skill exposed as a selectable session entry."""

    namespace: str
    workflow_id: str
    display_name: str
    description: str
    path: Path
    workflow: Workflow
    source_kind: str = "skill"
    aliases: tuple[str, ...] = ()
    verbs: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    negative_hints: tuple[str, ...] = ()

    @property
    def qualified_id(self) -> str:
        return f"{self.namespace}/{self.workflow_id}"


class SkillRegistry:
    """Discover and resolve local SKILL.md skills and legacy workflow skills."""

    def __init__(
        self,
        roots: list[str | Path] | None = None,
        *,
        default_namespace: str = "default",
        loader: WorkflowLoader | None = None,
    ):
        self.roots = [Path(root).expanduser() for root in roots or []]
        self.default_namespace = default_namespace or "default"
        self.loader = loader or WorkflowLoader()
        self.errors: list[str] = []
        self.discovery_errors: list[str] = []
        self.duplicate_ids: dict[str, list[Path]] = {}
        self._entries: list[SkillEntry] = []

    @classmethod
    def from_settings(cls, settings: Settings) -> "SkillRegistry":
        home = Path(os.environ.get("AGENTNEXUS_HOME", Path.home() / ".agentnexus"))
        roots: list[Path | str] = [home / "skills"]
        extensions_dirs = getattr(settings, "extensions_dirs", [])
        if isinstance(extensions_dirs, (list, tuple)):
            roots.extend(extensions_dirs)
        builtin = Path(__file__).parent / "builtin"
        if builtin.exists():
            roots.append(builtin)
        default_namespace = getattr(settings, "skills_default_namespace", "default")
        if not isinstance(default_namespace, str):
            default_namespace = "default"
        return cls(roots, default_namespace=default_namespace)

    def discover(self) -> list[SkillEntry]:
        self.errors = []
        self.discovery_errors = []
        self.duplicate_ids = {}
        entries: list[SkillEntry] = []
        seen_paths: set[Path] = set()
        seen_ids: dict[str, SkillEntry] = {}

        for root in self.roots:
            try:
                if not root.exists():
                    continue
                if not root.is_dir():
                    self.errors.append(f"{root}: not a directory")
                    continue
                paths = self._iter_skill_paths(root)
            except OSError as exc:
                self.errors.append(f"{root}: cannot scan skill root: {exc}")
                continue
            for path in paths:
                try:
                    resolved = path.resolve()
                except OSError as exc:
                    self.errors.append(f"{path}: cannot resolve skill path: {exc}")
                    continue
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                try:
                    workflow = self._load_skill_path(path)
                except ValueError as exc:
                    self.errors.append(str(exc))
                    continue
                namespace = self._namespace_for_path(root, path, workflow.id)
                qualified_id = f"{namespace}/{workflow.id}"
                if qualified_id in seen_ids:
                    first_entry = seen_ids[qualified_id]
                    self.duplicate_ids.setdefault(qualified_id, [first_entry.path]).append(path)
                    continue
                entry = SkillEntry(
                    namespace=namespace,
                    workflow_id=workflow.id,
                    display_name=workflow.display_name,
                    description=workflow.description or "",
                    path=path,
                    workflow=workflow,
                    source_kind="skill" if path.name == SKILL_MD_NAME else "workflow",
                    aliases=tuple(workflow.aliases),
                    verbs=tuple(workflow.verbs),
                    objects=tuple(workflow.objects),
                    domains=tuple(workflow.domains),
                    examples=tuple(workflow.examples),
                    negative_hints=tuple(workflow.negative_hints),
                )
                seen_ids[qualified_id] = entry
                entries.append(entry)

        self._entries = sorted(entries, key=lambda item: (item.namespace, item.workflow_id, str(item.path)))
        self.discovery_errors = [
            f"Duplicate skill id {qualified_id}: "
            + " and ".join(str(path) for path in paths)
            for qualified_id, paths in sorted(self.duplicate_ids.items())
        ]
        self.errors.extend(self.discovery_errors)
        return self.list()

    def list(self) -> list[SkillEntry]:
        return list(self._entries)

    def get(self, id_or_namespace_id: str) -> SkillEntry | None:
        lookup = (id_or_namespace_id or "").strip()
        if not lookup:
            return None
        duplicate_matches = self._duplicate_matches(lookup)
        if duplicate_matches:
            choices = ", ".join(sorted(duplicate_matches))
            raise ValueError(f"Duplicate skill id '{lookup}', remove duplicate definitions: {choices}")
        if "/" in lookup:
            matches = [entry for entry in self._entries if entry.qualified_id == lookup]
        else:
            matches = [entry for entry in self._entries if entry.workflow_id == lookup]
        if len(matches) > 1:
            choices = ", ".join(entry.qualified_id for entry in matches)
            raise ValueError(f"Ambiguous skill id '{lookup}', use one of: {choices}")
        return matches[0] if matches else None

    def validate(self, id_or_namespace_id: str | None = None) -> list[str]:
        """Validate discovered skills and prompt assets, returning readable errors."""
        errors = list(self.errors) if id_or_namespace_id is None else []
        entries = self.list()
        if id_or_namespace_id:
            try:
                entry = self.get(id_or_namespace_id)
            except ValueError as exc:
                return [str(exc)]
            if entry is None:
                return [f"Skill not found: {id_or_namespace_id}"]
            entries = [entry]
        for entry in entries:
            try:
                if entry.source_kind == "skill":
                    errors.extend(_validate_skill_markdown_file(entry.path, entry.qualified_id))
                validate_session_profile(entry.workflow.to_session_profile())
            except Exception as exc:
                errors.append(f"{entry.qualified_id}: {exc}")
        return errors

    def _iter_workflow_paths(self, root: Path) -> list[Path]:
        paths: list[Path] = []
        for pattern in WORKFLOW_PATTERNS:
            paths.extend(root.rglob(pattern))
        return sorted(paths)

    def _iter_skill_paths(self, root: Path) -> list[Path]:
        skill_md_paths = sorted(root.rglob(SKILL_MD_NAME))
        skill_md_dirs = {path.parent.resolve() for path in skill_md_paths}
        workflow_paths = [
            path for path in self._iter_workflow_paths(root)
            if path.parent.resolve() not in skill_md_dirs
        ]
        return sorted(skill_md_paths + workflow_paths)

    def _load_skill_path(self, path: Path) -> Workflow:
        if path.name == SKILL_MD_NAME:
            return _load_skill_markdown(path)
        return self.loader.load(path)

    def _namespace_for_path(self, root: Path, path: Path, workflow_id: str | None = None) -> str:
        try:
            parent = path.parent.relative_to(root)
        except ValueError:
            return self.default_namespace
        if parent == Path("."):
            return self.default_namespace
        if (
            path.name == SKILL_MD_NAME
            and len(parent.parts) == 1
            and workflow_id
            and parent.parts[0] == workflow_id
        ):
            return self.default_namespace
        return parent.parts[0] if parent.parts else self.default_namespace

    def _duplicate_matches(self, lookup: str) -> list[str]:
        if "/" in lookup:
            return [qualified_id for qualified_id in self.duplicate_ids if qualified_id == lookup]
        return [qualified_id for qualified_id in self.duplicate_ids if qualified_id.split("/", 1)[1] == lookup]


def _load_skill_markdown(path: Path) -> Workflow:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Invalid skill manifest {path}: {exc}") from exc
    metadata, body = _parse_skill_markdown(text)
    workflow_id = str(metadata.get("id") or path.parent.name).strip()
    display_name = str(
        metadata.get("display_name")
        or metadata.get("name")
        or _title_from_markdown(body)
        or workflow_id
    )
    description = str(metadata.get("description") or _description_from_markdown(body))
    max_risk = str(metadata.get("max_risk") or metadata.get("risk") or "high")
    allow = _as_str_list(metadata.get("allow_tools") or metadata.get("allow"))
    deny = _as_str_list(metadata.get("deny_tools") or metadata.get("deny"))
    fragments = _as_str_list(metadata.get("fragments"))
    system = str(metadata.get("system") or "react")
    allow_subagents = bool(metadata.get("allow_subagents", False))

    guidance = body.strip() or f"Follow the {display_name} skill instructions."
    resources = _discover_skill_resources(path.parent)
    return Workflow(
        id=workflow_id,
        version=str(metadata.get("version") or "1"),
        display_name=display_name,
        description=description,
        entry_mode=str(metadata.get("entry_mode") or "chat"),
        prompt_profile=PromptProfile(
            system=system,
            fragments=fragments,
            variables={},
        ),
        tool_policy=ToolPolicy(
            allow=allow,
            deny=deny,
            max_risk=max_risk,
            allow_subagents=allow_subagents,
        ),
        memory_policy=MemoryPolicy(
            inject_long_term=bool(metadata.get("inject_long_term", True)),
            allow_save=bool(metadata.get("allow_save", True)),
        ),
        retrieval_policy=RetrievalPolicy(),
        steps=[
            WorkflowStep(
                type="prompt",
                id="skill_instructions",
                prompt=guidance,
            )
        ],
        success_criteria=_as_str_list(metadata.get("success_criteria")) or [
            "Follow the skill instructions in SKILL.md."
        ],
        resources=resources,
        aliases=_as_str_list(metadata.get("aliases")),
        verbs=_as_str_list(metadata.get("verbs")),
        objects=_as_str_list(metadata.get("objects")),
        domains=_as_str_list(metadata.get("domains")),
        examples=_as_str_list(metadata.get("examples")),
        negative_hints=_as_str_list(metadata.get("negative_hints")),
    )


def _parse_skill_markdown(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    frontmatter = text[4:end]
    body = text[text.find("\n", end + 1) + 1:]
    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid SKILL.md frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("Invalid SKILL.md frontmatter: expected mapping")
    return metadata, body


def _validate_skill_markdown_file(path: Path, qualified_id: str) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{qualified_id}: cannot read SKILL.md: {exc}"]
    metadata, body = _parse_skill_markdown(text)
    errors: list[str] = []
    if "name" not in metadata:
        errors.append(f"{qualified_id}: SKILL.md frontmatter missing required 'name'")
    if "description" not in metadata:
        errors.append(f"{qualified_id}: SKILL.md frontmatter missing required 'description'")
    if not body.strip():
        errors.append(f"{qualified_id}: SKILL.md body is empty")
    if len(body.splitlines()) > MAX_SKILL_BODY_LINES:
        errors.append(f"{qualified_id}: SKILL.md body exceeds {MAX_SKILL_BODY_LINES} lines")
    return errors


def _discover_skill_resources(skill_dir: Path) -> list[SkillResource]:
    resources: list[SkillResource] = []
    for dirname, resource_type in RESOURCE_DIRS.items():
        root = skill_dir / dirname
        if not root.exists():
            continue
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            try:
                rel = path.relative_to(skill_dir).as_posix()
                size = path.stat().st_size
            except OSError:
                continue
            resources.append(SkillResource(
                type=resource_type,
                path=rel,
                absolute_path=str(path.resolve()),
                name=path.name,
                size_bytes=size,
            ))
            if len(resources) >= MAX_RESOURCE_COUNT:
                return resources
    return resources


def _title_from_markdown(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _description_from_markdown(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:200]
    return ""


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []
