"""Discovery, layering, and trust review for declarative command hooks.

Sources, lowest → highest precedence (matching hooks from all layers run,
mirroring Codex semantics):

1. ``config.yaml`` ``hooks:`` list          — managed, trusted by definition
2. ``~/.agentnexus/hooks.yaml``             — user-global, trusted
3. ``<workspace>/.agentnexus/hooks.yaml``   — project, requires hash trust

Trust: a project hook's fingerprint (sha256 of source path + event +
command) must appear in ``~/.agentnexus/hook_trust.json``. New or changed
project hooks are skipped with a one-time warning until reviewed and
approved via ``nexus hooks trust approve <fingerprint>`` or
``POST /api/hooks/trust``. ``hook_trust: bypass`` disables the gate
(one-off automation only — never enable persistently).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from agentnexus.core.hook_schemas import HookConfig
from agentnexus.tools.workspace import get_effective_workspace

logger = logging.getLogger(__name__)

HOOKS_FILENAME = "hooks.yaml"
_TRUST_FILENAME = "hook_trust.json"

_warned_untrusted: set[str] = set()


@dataclass(frozen=True)
class LoadedHook:
    config: HookConfig
    source: str  # "config" | "user" | "project"
    source_path: Path
    fingerprint: str
    trusted: bool


def agentnexus_home() -> Path:
    return Path(os.environ.get("AGENTNEXUS_HOME", Path.home() / ".agentnexus"))


def fingerprint_for(source_path: Path, config: HookConfig) -> str:
    digest = hashlib.sha256()
    digest.update(str(source_path).encode("utf-8"))
    digest.update(b"\0")
    digest.update(config.event.encode("utf-8"))
    digest.update(b"\0")
    digest.update(config.command.encode("utf-8"))
    return digest.hexdigest()


# ── trust store ────────────────────────────────────────────────────


def trust_file_path() -> Path:
    return agentnexus_home() / _TRUST_FILENAME


def load_trusted_fingerprints() -> set[str]:
    path = trust_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, dict) and isinstance(data.get("trusted"), list):
        return {str(item) for item in data["trusted"]}
    return set()


def save_trusted_fingerprints(fingerprints: set[str]) -> Path:
    path = trust_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"trusted": sorted(fingerprints)}, indent=2)
    fd, tmp_name = __import__("tempfile").mkstemp(dir=path.parent, prefix="hook_trust.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def approve_fingerprint(fingerprint: str) -> None:
    trusted = load_trusted_fingerprints()
    trusted.add(fingerprint)
    save_trusted_fingerprints(trusted)


def revoke_fingerprint(fingerprint: str) -> None:
    trusted = load_trusted_fingerprints()
    trusted.discard(fingerprint)
    save_trusted_fingerprints(trusted)


# ── discovery ──────────────────────────────────────────────────────


def _parse_hook_file(path: Path, source: str, errors: list[str]) -> list[HookConfig]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{path}: unreadable ({exc})")
        return []
    except yaml.YAMLError as exc:
        errors.append(f"{path}: invalid YAML ({exc})")
        return []
    if raw is None:
        return []
    entries = raw.get("hooks") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        errors.append(f"{path}: expected a list or a mapping with a 'hooks' list")
        return []
    configs: list[HookConfig] = []
    for index, entry in enumerate(entries):
        try:
            configs.append(HookConfig.model_validate(entry))
        except Exception as exc:
            errors.append(f"{path}: hooks[{index}] invalid — {exc}")
    return configs


def discover_hooks(
    include_untrusted: bool = False,
) -> tuple[list[LoadedHook], list[str]]:
    """Resolve all hook layers. Returns (loaded, errors).

    With ``include_untrusted=False`` (the default, used on the hot path)
    untrusted project hooks are filtered out — with a one-time warning per
    fingerprint so the log stays readable.
    """
    from agentnexus.core.config import get_settings

    settings = get_settings()
    errors: list[str] = []
    if not getattr(settings, "hooks_enabled", True):
        return [], errors

    layers: list[tuple[str, Path, list[HookConfig]]] = []
    layers.append(("config", agentnexus_home() / "config.yaml", list(settings.hooks)))

    user_file = agentnexus_home() / HOOKS_FILENAME
    if user_file.is_file():
        layers.append(("user", user_file, _parse_hook_file(user_file, "user", errors)))

    workspace = get_effective_workspace()
    project_file = workspace / ".agentnexus" / HOOKS_FILENAME
    if project_file.is_file():
        layers.append(("project", project_file, _parse_hook_file(project_file, "project", errors)))

    bypass = getattr(settings, "hook_trust", "strict") == "bypass"
    trusted_store = load_trusted_fingerprints() if not bypass else None

    loaded: list[LoadedHook] = []
    for source, path, configs in layers:
        for config in configs:
            fingerprint = fingerprint_for(path, config)
            if source == "project" and trusted_store is not None:
                trusted = fingerprint in trusted_store
            else:
                trusted = True
            if not trusted:
                if fingerprint not in _warned_untrusted:
                    _warned_untrusted.add(fingerprint)
                    logger.warning(
                        "Untrusted project hook skipped: %s [%s] — review with "
                        "`nexus hooks trust approve %s`",
                        config.command[:60], config.event, fingerprint[:12],
                    )
                if not include_untrusted:
                    continue
            loaded.append(LoadedHook(config, source, path, fingerprint, trusted))
    return loaded, errors


def discover_hook_configs() -> list[LoadedHook]:
    """Hot-path discovery: trusted hooks only, parse errors logged once."""
    loaded, errors = discover_hooks(include_untrusted=False)
    for message in errors:
        logger.warning("hook discovery: %s", message)
    return loaded
