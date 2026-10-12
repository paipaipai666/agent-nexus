"""Verify the frozen backend with an isolated empty Hugging Face cache.

The default run is offline. --download-embedding additionally exercises a real
first download of the pinned embedding snapshot. It never downloads the optional
large reranker. Results and backend logs stay under --out.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx
from package_audit import save
from verify_package import Gateway, backend, chat


def cache_env(root: Path, *, offline: bool) -> dict[str, str]:
    return {
        "HF_HOME": str(root), "HF_HUB_CACHE": str(root / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(root / "hub"), "TRANSFORMERS_CACHE": str(root / "hub"),
        "HF_HUB_OFFLINE": "1" if offline else "0",
        "TRANSFORMERS_OFFLINE": "1" if offline else "0",
    }


def wait_run(client, run_id, *, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/kb/documents/runs/{run_id}")
        if response.status_code == 200:
            run = response.json()
            if run["status"] in {"failed", "completed"}:
                return run
        time.sleep(0.1)
    raise TimeoutError("ingestion did not finish")


def wait_model(client, kind, *, timeout=900):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        response = client.get("/api/kb/models")
        response.raise_for_status()
        state = response.json()["models"][kind]
        if (state["state"], state["message"]) != previous:
            print(f"{kind}: {state['state']}: {state['message']}", flush=True)
            previous = (state["state"], state["message"])
        if state["state"] in {"ready", "failed"}:
            return state
        time.sleep(0.5)
    raise TimeoutError(f"{kind} preparation exceeded {timeout}s")


def upload(client, text):
    response = client.post("/api/kb/documents", files={"file": ("first-use.md", text.encode("utf-8"))})
    response.raise_for_status()
    return wait_run(client, response.json()["run_id"])


def verify_vectors(home: Path, cache: Path):
    import numpy as np

    os.environ.update(cache_env(cache, offline=True))
    os.environ["AGENTNEXUS_HOME"] = str(home)
    from agentnexus.rag.model_assets import prepare_embedding
    from agentnexus.storage.chroma import get_collection

    model, identity = prepare_embedding(allow_download=False)
    rows = get_collection(namespace="default").get(include=["embeddings", "documents"])
    assert rows["ids"], rows
    expected = model.encode(rows["documents"][0], normalize_embeddings=True)
    actual = rows["embeddings"][0]
    error = float(np.max(np.abs(np.array(expected) - np.array(actual))))
    assert len(actual) == 512 and error < 1e-5, error
    return {"identity": identity, "dimension": len(actual), "max_absolute_error": error}


def verify(binary: Path, out: Path, *, download: bool):
    out.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, detail):
        checks.append({"name": name, "success": True, "detail": detail})
        save(out / "checks.json", checks)
        print(f"{name}: PASS", flush=True)

    gateway = Gateway()
    try:
        isolated = out / "offline-cache"
        with backend(binary, out / "offline-home", gateway, env_overrides=cache_env(isolated, offline=True)) as state:
            with httpx.Client(base_url=state["base"], timeout=180, trust_env=False) as client:
                models = client.get("/api/kb/models").json()
                assert models["models"]["embedding"]["state"] == "missing", models
                assert models["models"]["reranker"]["state"] == "missing", models
                check("empty_cache_is_visible", models)
                detail = asyncio.run(chat(state["base"], out / "offline-home/workspace", "plain_first_use"))
                check("chat_works_without_model_download", detail)
                run = upload(client, "First-use delivery fixture")
                assert run["status"] == "failed", run
                assert "OFFLINE" in run["error_message"], run
                documents = client.get("/api/kb/documents").json()
                assert documents == {"documents": [], "total_chunks": 0}, documents
                check("offline_import_fails_without_hash_index", run)
                for _ in range(2):
                    client.post("/api/kb/models/embedding/prepare").raise_for_status()
                    result = wait_model(client, "embedding")
                    assert result["state"] == "failed", result
                check("offline_prepare_and_retry_are_explicit", result)

        if download:
            online_cache = out / "online-cache"
            with backend(binary, out / "online-home", gateway, env_overrides=cache_env(online_cache, offline=False)) as state:
                with httpx.Client(base_url=state["base"], timeout=180, trust_env=False) as client:
                    initial = client.get("/api/kb/models").json()
                    assert all(model["state"] == "missing" for model in initial["models"].values()), initial
                    check("online_first_use_starts_without_models", initial)
                    started = time.perf_counter()
                    response = client.post("/api/kb/documents", files={"file": (
                        "first-use.md",
                        b"First-use delivery fixture: AgentNexus retains sessions and supports tool cancellation.",
                    )})
                    response.raise_for_status()
                    run_id = response.json()["run_id"]
                    result = wait_model(client, "embedding")
                    assert result["state"] == "ready", result
                    check("real_first_embedding_download", {**result, "seconds": time.perf_counter() - started})
                    run = wait_run(client, run_id)
                    assert run["status"] == "completed", run
                    response = client.post("/api/kb/search", json={"query": "sessions tool cancellation"})
                    response.raise_for_status()
                    search = response.json()
                    assert search["results"] and search["ranking_mode"] == "hybrid", search
                    models = client.get("/api/kb/models").json()
                    assert models["models"]["reranker"]["state"] == "missing", models
                    check("first_import_and_explicit_hybrid_search", {"run": run, "search": search})
            logical_bytes = sum(path.stat().st_size for path in online_cache.rglob("*") if path.is_file() and not path.is_symlink())
            check("model_cache_disk_size", {"bytes": logical_bytes, "cache": str(online_cache)})
            with backend(binary, out / "online-home", gateway, env_overrides=cache_env(online_cache, offline=True)) as state:
                with httpx.Client(base_url=state["base"], timeout=180, trust_env=False) as client:
                    response = client.post("/api/kb/search", json={"query": "sessions tool cancellation"})
                    response.raise_for_status()
                    assert response.json()["results"]
                    check("downloaded_model_reused_offline_after_restart", response.json())
            command = subprocess.run(
                [str(binary), "kb", "prepare"],
                env={**os.environ, **cache_env(online_cache, offline=True), "AGENTNEXUS_HOME": str(out / "online-home")},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
            assert command.returncode == 0, command.stderr + command.stdout
            check("frozen_cli_prepares_cached_model_offline", {"exit_code": command.returncode, "output": command.stdout})
            check("frozen_vectors_match_real_downloaded_model", verify_vectors(out / "online-home", online_cache))
    except Exception as exc:
        checks.append({"name": "verification_error", "success": False, "error": str(exc)})
        save(out / "checks.json", checks)
        raise
    finally:
        gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--download-embedding", action="store_true")
    args = parser.parse_args()
    verify(args.binary.resolve(), args.out.resolve(), download=args.download_embedding)
