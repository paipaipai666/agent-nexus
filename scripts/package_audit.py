"""Build isolated Windows packages and retain the inputs and size evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def decision(out: Path, phase: str, action: str, why: str, evidence: Path, result: str) -> None:
    path = out / "decisions.tsv"
    if not path.exists():
        path.write_text("ts\tphase\tdecision\twhy\tevidence\tresult\n", encoding="utf-8")
    values = [datetime.now(timezone.utc).isoformat(), phase, action, why, str(evidence), result]
    cells = []
    for value in values:
        cell = value.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        cells.append("'" + cell if cell.startswith(("=", "+", "-", "@")) else cell)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\t".join(cells) + "\n")


def run(out: Path, label: str, command: list[str], *, cwd: Path = REPO, env: dict | None = None) -> None:
    log = out / f"{label}.log"
    started = time.perf_counter()
    print(f"{label}: started; log={log}", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.run(["rtk", "proxy", *command], cwd=cwd, env=env, stdout=stream,
                                 stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elapsed = time.perf_counter() - started
    save(out / f"{label}.json", {"command": command, "cwd": str(cwd), "exit_code": process.returncode,
                                "seconds": elapsed, "log": str(log)})
    print(f"{label}: exit={process.returncode}, seconds={elapsed:.1f}", flush=True)
    if process.returncode:
        print(log.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
        raise SystemExit(process.returncode)


def snapshot(out: Path) -> None:
    installed = sorted({d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}.items())
    save(out / "source-environment.json", {"python": sys.executable, "version": sys.version,
                                          "packages": dict(installed)})
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = list(project["dependencies"])
    for extra in ("rag", "server", "search", "tui"):
        requirements.extend(project["optional-dependencies"][extra])
    names = {re.split(r"[<>=~!;\[]", item, maxsplit=1)[0].strip() for item in requirements}
    names.update({"torch", "transformers", "numpy", "scipy", "scikit-learn", "huggingface-hub",
                  "pyinstaller", "pyinstaller-hooks-contrib"})
    constraints = [f"{name}=={importlib.metadata.version(name)}" for name in sorted(names)]
    (out / "constraints.txt").write_text("\n".join(constraints) + "\n", encoding="utf-8")
    run(out, "source-git-head", ["git", "rev-parse", "HEAD"])
    run(out, "source-git-diff", ["git", "diff"])
    files = [*REPO.joinpath("agentnexus").rglob("*.py"), REPO / "pyproject.toml", REPO / "agentnexus.spec",
             REPO / "desktop/package.json", REPO / "desktop/package-lock.json", REPO / "desktop/electron-builder.yml"]
    save(out / "source-sha256.json", {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in files})
    decision(out, "baseline", "固定当前源码和已测依赖版本", "避免把依赖升级误计为裁剪收益",
             out / "source-environment.json", "saved")


def setup(out: Path, offline: bool) -> None:
    if not (out / "constraints.txt").exists():
        snapshot(out)
    python = out / "env/Scripts/python.exe"
    if not python.exists():
        run(out, "create-env", ["uv", "venv", "--python", sys.executable, str(out / "env")])
    command = ["uv", "pip", "install", "--python", str(python), "--constraint", str(out / "constraints.txt"),
               "--editable", str(REPO) + "[all]", "pyinstaller==6.20.0", "pyinstaller-hooks-contrib==2026.5",
               "setuptools>=77.0.3,<81"]
    if offline:
        command.append("--offline")
    label = "install-offline" if offline else "install-online"
    run(out, label, command)
    run(out, "environment-freeze", ["uv", "pip", "freeze", "--python", str(python)])
    run(out, "environment-check", ["uv", "pip", "check", "--python", str(python)])
    decision(out, "clean-build", "只安装项目 all 运行依赖与构建工具", "隔离全局环境的可选和开发包",
             out / "environment-freeze.log", "installed")


def build(out: Path, variant: str, spec: Path | None) -> None:
    target = out / variant
    target.mkdir(exist_ok=True)
    chosen = spec or REPO / "agentnexus.spec"
    source = chosen.read_text(encoding="utf-8")
    if "project_root = Path(SPECPATH)" not in source:
        raise ValueError("Packaging specs must declare project_root = Path(SPECPATH)")
    source = source.replace("project_root = Path(SPECPATH)", "project_root = Path(" + repr(str(REPO)) + ")")
    staged = target / "input.spec"
    staged.write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTNEXUS_")}
    env.update(PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    run(out, f"build-{variant}", [str(out / "env/Scripts/python.exe"), "-m", "PyInstaller", str(staged),
                                 "--noconfirm", "--distpath", str(target / "dist"),
                                 "--workpath", str(target / "work")], env=env)
    inventory = sizes(target / "dist/agentnexus")
    save(target / "size.json", inventory)
    decision(out, "build", f"构建 {variant} 后端", "记录实际产物而非推算依赖体积",
             target / "size.json", f"{inventory['bytes'] / 1024**2:.1f} MiB")


def inspect_paths(out: Path, spec: Path) -> None:
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "collect_data_globs")
    rows = []
    for arg in call.args:
        pattern, _ = ast.literal_eval(arg)
        for path in REPO.glob(pattern):
            if path.is_file():
                relative = path.relative_to(REPO)
                rows.append({"source": str(relative), "exists_from_cwd": path.exists(),
                             "exists_from_spec_directory": (spec.parent / relative).exists()})
    save(out / "path-census.json", {"premise": "relative paths were assumed to resolve from the working directory",
                                    "cwd": str(REPO), "spec_directory": str(spec.parent), "data_paths": rows,
                                    "cwd_found": sum(r["exists_from_cwd"] for r in rows),
                                    "spec_found": sum(r["exists_from_spec_directory"] for r in rows)})
    decision(out, "paths", "核对工作目录与 spec 目录下的资源路径", "入口和资源必须共享显式项目根目录",
             out / "path-census.json", f"cwd {len(rows)}, spec {sum(r['exists_from_spec_directory'] for r in rows)}")


def sizes(path: Path) -> dict:
    def total(folder: Path) -> tuple[int, int]:
        if folder.is_file():
            return folder.stat().st_size, 1
        files = [p for p in folder.rglob("*") if p.is_file()]
        return sum(p.stat().st_size for p in files), len(files)

    if not path.exists():
        raise FileNotFoundError(path)
    size, count = total(path)
    children = []
    if path.is_dir():
        root = path / "_internal" if (path / "_internal").is_dir() else path
        for child in root.iterdir():
            child_size, child_count = total(child)
            children.append({"name": child.name, "bytes": child_size, "files": child_count})
    return {"path": str(path), "bytes": size, "files": count,
            "children": sorted(children, key=lambda item: item["bytes"], reverse=True)}


def desktop(out: Path, variant: str) -> None:
    import yaml

    source = REPO / "desktop"
    project = out / "desktop-project"
    project.mkdir(exist_ok=True)
    for name in ("src", "electron", "public"):
        if (source / name).is_dir():
            shutil.copytree(source / name, project / name, dirs_exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix in {".json", ".ts", ".js", ".cjs", ".html"}:
            shutil.copy2(path, project / path.name)
    env = dict(os.environ, CSC_IDENTITY_AUTO_DISCOVERY="false")
    if not (project / "node_modules").is_dir():
        run(out, "desktop-install", ["npm", "ci", "--no-audit", "--no-fund"], cwd=project, env=env)
    if not (project / "dist/index.html").exists():
        run(out, "desktop-typecheck", ["node", "node_modules/typescript/bin/tsc", "--noEmit"], cwd=project, env=env)
        run(out, "desktop-vite", ["node", "node_modules/vite/bin/vite.js", "build"], cwd=project, env=env)
    config = yaml.safe_load((source / "electron-builder.yml").read_text(encoding="utf-8"))
    target = out / variant
    target.mkdir(exist_ok=True)
    config["directories"]["output"] = str(target / "desktop")
    backend = target / "dist/agentnexus"
    config["win"]["extraResources"] = [
        {"from": str(backend / "agentnexus.exe"), "to": "backend/agentnexus.exe"},
        {"from": str(backend / "_internal"), "to": "backend/_internal"},
    ]
    config["artifactName"] = f"AgentNexus-${{version}}-{variant}-setup.${{ext}}"
    config_path = target / "electron-builder.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    run(out, f"desktop-{variant}", ["node", "node_modules/electron-builder/cli.js", "--win", "--x64",
                                   "--publish", "never", "--config", str(config_path)], cwd=project, env=env)
    installers = [{"name": p.name, "bytes": p.stat().st_size,
                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (target / "desktop").glob("*.exe")]
    save(target / "desktop-size.json", {"installers": installers,
                                       "unpacked": sizes(target / "desktop/win-unpacked")})
    decision(out, "desktop", f"构建 {variant} Electron 与 NSIS 安装包", "前后使用同一前端与 Electron 依赖",
             target / "desktop-size.json", "built")


def identity(out: Path, variant: str) -> None:
    target = out / variant
    source = target / "dist/agentnexus"
    bundled = target / "desktop/win-unpacked/resources/backend"

    def manifest(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    original = manifest(source)
    packaged = manifest(bundled)
    missing = sorted(original.keys() - packaged.keys())
    extra = sorted(packaged.keys() - original.keys())
    mismatched = sorted(name for name in original.keys() & packaged.keys() if original[name] != packaged[name])
    asar = target / "desktop/win-unpacked/resources/app.asar"
    result = {"backend_files": len(original), "missing": missing, "extra": extra,
              "mismatched": mismatched, "app_asar_sha256": hashlib.sha256(asar.read_bytes()).hexdigest()}
    save(target / "identity.json", result)
    if missing or extra or mismatched:
        raise SystemExit(f"desktop backend differs from verified bundle: {target / 'identity.json'}")
    decision(out, "identity", f"核对 {variant} 安装包后端文件", "安装包须包含同一份已验证后端",
             target / "identity.json", f"{len(original)} files identical")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "setup", "build", "desktop", "identity", "paths", "size"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.action == "snapshot":
        snapshot(out)
    elif args.action == "paths":
        if args.spec is None:
            parser.error("paths requires --spec")
        inspect_paths(out, args.spec.resolve())
    elif args.action == "setup":
        setup(out, args.offline)
    elif args.action in {"build", "desktop", "identity"}:
        if not args.variant or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.variant):
            parser.error("variant must be a simple lowercase directory name")
        if args.action == "desktop":
            desktop(out, args.variant)
        elif args.action == "identity":
            identity(out, args.variant)
        else:
            build(out, args.variant, args.spec.resolve() if args.spec else None)
    else:
        if args.path is None:
            parser.error("size requires --path")
        print(json.dumps(sizes(args.path.resolve()), indent=2))


if __name__ == "__main__":
    main()
