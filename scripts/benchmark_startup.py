"""Measure fresh-process TUI input readiness and backend HTTP startup."""

import argparse
import builtins
import json
import os
import platform
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def save(path, value):
    """Write timing records without copying runtime logs or configuration."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def worker(args):
    """Run the real CLI with TUI input-readiness instrumentation."""
    sys.path.insert(0, str(REPO))
    started = float(os.environ["AGENTNEXUS_BENCHMARK_START"])
    events = {}
    patched = set()
    original_import = builtins.__import__

    def mark(name):
        events[name] = time.perf_counter() - started
        save(args.result, events)

    def hook(name, globals=None, locals=None, fromlist=(), level=0):
        result = original_import(name, globals, locals, fromlist, level)
        if name in {"agentnexus.app", "agentnexus.app.runtime"} and "runtime" not in patched:
            module = sys.modules.get("agentnexus.app.runtime")
            if module and hasattr(module, "AppRuntime"):
                patched.add("runtime")
                original_build = module.AppRuntime.build.__func__

                def build(cls, *a, **kw):
                    mark("runtime_build_start")
                    runtime = original_build(cls, *a, **kw)
                    mark("runtime_build_end")
                    return runtime

                module.AppRuntime.build = classmethod(build)
        if name == "agentnexus.tui.app" and "tui" not in patched:
            module = sys.modules.get(name)
            if module and hasattr(module, "AgentNexusTUI"):
                patched.add("tui")
                original_run = module.AgentNexusTUI.run

                def run(app, *a, **kw):
                    async def send_key(pilot):
                        import asyncio

                        from textual.events import Key

                        async with asyncio.timeout(args.timeout):
                            while not app.screen.query("#chat-input") or not app.screen.query_one("#chat-input").has_focus:
                                await asyncio.sleep(0.005)
                            composer = app.screen.query_one("#chat-input")
                            mark("key_sent")
                            app._driver.send_message(Key("x", "x"))
                            while composer.text != "x":
                                await asyncio.sleep(0.005)
                            mark("interactive")
                            app.exit()

                    return original_run(app, headless=True, auto_pilot=send_key)

                module.AgentNexusTUI.run = run
        return result

    builtins.__import__ = hook
    try:
        from agentnexus.cli import main

        mark("cli_import_end")
        command = ["tui"] if args.target == "tui" else ["serve", "--port", str(args.port), "--no-auth"]
        main(command)
    finally:
        builtins.__import__ = original_import


def copy_home(source, target):
    """Clone settings, databases and skills while leaving capability caches empty."""
    if (source / "config.yaml").exists():
        shutil.copy2(source / "config.yaml", target / "config.yaml")
    for name in ("memory.db", "rag_catalog.db"):
        if (source / name).exists():
            connection = sqlite3.connect((source / name).resolve().as_uri() + "?mode=ro", uri=True)
            backup = sqlite3.connect(target / name)
            try:
                connection.backup(backup)
            finally:
                backup.close()
                connection.close()
    if (source / "skills").exists():
        shutil.copytree(source / "skills", target / "skills")


def request(opener, port, endpoint, method="GET", timeout=1):
    """Read one JSON response from the benchmark's local backend."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{endpoint}", method=method)
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def measure(args, source_home):
    """Measure one fresh process against a disposable configuration and database copy."""
    with tempfile.TemporaryDirectory(prefix="agentnexus-benchmark-") as directory:
        home = Path(directory)
        copy_home(source_home, home)
        result_path = home / "events.json"
        port = 0
        if args.target == "server":
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--target", args.target,
                   "--result", str(result_path), "--port", str(port), "--timeout", str(args.timeout)]
        env = dict(os.environ, AGENTNEXUS_HOME=str(home), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        sample = {}
        with (home / "worker.log").open("w", encoding="utf-8") as log:
            started = time.perf_counter()
            env["AGENTNEXUS_BENCHMARK_START"] = repr(started)
            process = subprocess.Popen(command, cwd=REPO, env=env, stdout=log, stderr=log)
            try:
                while process.poll() is None and time.perf_counter() - started < args.timeout:
                    if port:
                        try:
                            request(opener, port, "/health", timeout=0.1)
                            sample["health_seconds"] = time.perf_counter() - started
                            break
                        except (OSError, urllib.error.URLError):
                            pass
                    time.sleep(0.025)
                if "health_seconds" in sample:
                    sample["api"] = []
                    for method, endpoint in [("GET", "/api/config/llm/capabilities"),
                                             ("GET", "/api/config/llm/capabilities"),
                                             ("POST", "/api/config/llm/capabilities/refresh")]:
                        before = time.perf_counter()
                        response = request(opener, port, endpoint, method=method, timeout=args.timeout)
                        sample["api"].append({"method": method, "endpoint": endpoint,
                                              "seconds": time.perf_counter() - before,
                                              "source": response["source"], "status": response["discovery_status"]})
                    request(opener, port, "/api/runtime/shutdown", method="POST")
                    process.wait(timeout=8)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            sample["exit_code"] = process.returncode
        sample["events"] = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        metric = sample["events"].get("interactive") if args.target == "tui" else sample.get("health_seconds")
        sample["ready_seconds"] = metric
        if process.returncode != 0 or metric is None:
            sample["error"] = "Worker exited unsuccessfully or readiness was not observed"
        return sample


def main():
    """Collect serial startup samples and save their durations as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["tui", "server"], default="tui")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("AGENTNEXUS_HOME", Path.home() / ".agentnexus")))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.output is None or args.repeat < 1 or args.timeout <= 0:
        parser.error("--output is required; --repeat and --timeout must be positive")
    report = {"measurement_version": 3, "target": args.target, "python": sys.version,
              "platform": platform.platform(), "samples": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index in range(args.repeat):
        sample = measure(args, args.home)
        report["samples"].append(sample)
        save(args.output, report)
        print(f"{index + 1}/{args.repeat}: {sample['ready_seconds']} s; exit={sample['exit_code']}", flush=True)
    values = [sample["ready_seconds"] for sample in report["samples"] if "error" not in sample]
    if len(values) != args.repeat:
        raise SystemExit("Startup measurement failed; inspect the JSON samples")
    report["summary"] = {"count": len(values), "median_seconds": statistics.median(values),
                         "min_seconds": min(values), "max_seconds": max(values)}
    save(args.output, report)
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
