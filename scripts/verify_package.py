"""Verify a frozen backend through HTTP, WebSocket, and persisted data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psutil
import yaml
from package_audit import decision, save
from websockets.asyncio.client import connect


class Gateway:
    def __init__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                text = "\n".join(str(m.get("content", "")) for m in body["messages"])
                matches = re.findall(r"BENCH_ID=(\w+)", text)
                nonce = matches[-1] if matches else "unknown"
                tools = {t["function"]["name"] for t in body.get("tools", [])}
                fn, args = None, {}
                if not any(m.get("role") == "tool" for m in body["messages"]):
                    if nonce.startswith("file"):
                        fn, args = "file_read", {"path": "fixture.txt"}
                    elif nonce.startswith("shell"):
                        fn, args = "shell_exec", {"command": "rtk proxy cmd /c echo PACKAGED_SHELL_OK"}
                    elif nonce.startswith("mcp"):
                        fn = next((name for name in tools if "audit_echo" in name), "missing_mcp_tool")
                        args = {"text": "PACKAGED_MCP_OK"}
                if fn and fn not in tools:
                    self.send_error(500, f"Missing tool: {fn}")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()

                def chunk(delta, finish=None):
                    data = {"id": "chatcmpl-package", "object": "chat.completion.chunk", "created": int(time.time()),
                            "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
                    self.wfile.flush()

                if fn:
                    chunk({"tool_calls": [{"index": 0, "id": "call_package", "type": "function",
                                           "function": {"name": fn, "arguments": json.dumps(args)}}]})
                    chunk({}, "tool_calls")
                else:
                    chunk({"content": "BENCH_OK_" + nonce})
                    chunk({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def backend(binary, home, gateway, *, mcp=False):
    home.mkdir(parents=True, exist_ok=True)
    workspace = home / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "fixture.txt").write_text("BENCH_FILE_CONTENT: 314159\n", encoding="utf-8")
    config = {"llm_model_id": "agnes-3.0-flash", "llm_base_url": gateway.base,
              "model_tool_calling": True, "model_json_mode": False, "model_thinking": False,
              "max_agent_steps": 4, "enable_query_rewrite": False, "enable_multi_query": False,
              "enable_contextual_retrieval": False, "mcp_enabled": mcp, "wiki_enabled": mcp}
    if mcp:
        fixture = home / "mcp_fixture.py"
        fixture.write_text(
            'from mcp.server.fastmcp import FastMCP\napp = FastMCP("package-audit")\n'
            '@app.tool()\ndef audit_echo(text: str) -> str:\n    return text\n'
            'if __name__ == "__main__":\n    app.run(transport="stdio")\n', encoding="utf-8")
        config["mcp_servers"] = [{"name": "audit", "command": sys.executable, "args": [str(fixture)],
                                  "transport": "stdio", "enabled": True, "cwd": str(workspace)}]
        config["capabilities"] = {"states": {"mcp": {"enabled": True}}, "mcp_servers": {"audit": True}}
    if not (home / "config.yaml").exists():
        (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTNEXUS_") and k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(AGENTNEXUS_HOME=str(home), AGENTNEXUS_LLM_API_KEY="package-test-key", PYTHONIOENCODING="utf-8",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = home / f"server-{uuid.uuid4().hex[:8]}.log"
    started = time.perf_counter()
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(["rtk", "proxy", str(binary), "serve", "--port", str(port), "--no-auth"],
                               cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    state = {"home": str(home), "base": base, "pid": process.pid, "log": str(log_path)}
    try:
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            while time.perf_counter() - started < 90:
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
                try:
                    response = client.get(base + "/health")
                    if response.status_code in {200, 503}:
                        health = response.json()
                        state["health"] = health
                        state["health_http_status"] = response.status_code
                        checks = health.get("checks", {})
                        expected = checks if not mcp else {name: check for name, check in checks.items() if name != "mcp"}
                        if expected and all(check.get("status") == "ok" for check in expected.values()):
                            state["startup_ms"] = (time.perf_counter() - started) * 1000
                            break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise TimeoutError(f"packaged backend startup exceeded 90s; health={state.get('health')}")
        yield state
    finally:
        try:
            family = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            family = []
        for child in reversed(family):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate() if process.poll() is None else None
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        _, alive = psutil.wait_procs(family, timeout=3)
        for child in alive:
            child.kill()
        log.close()
        save(home / f"process-{process.pid}.json", state)


async def chat(base, workspace, nonce, *, sid=None):
    if sid is None:
        with httpx.Client(trust_env=False) as client:
            response = client.post(base + "/api/session", json={"workspace": str(workspace)})
            response.raise_for_status()
            sid = response.json()["session_id"]
    events, tool_results, answer = [], [], ""
    started = time.perf_counter()
    async with connect(base.replace("http:", "ws:") + f"/api/ws/agent/{sid}", ping_interval=None) as ws:
        await ws.send(json.dumps({"type": "send_message", "content": f"BENCH_ID={nonce}\nExecute the requested benchmark task."}))
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
            events.append(event)
            if event["type"] == "confirm_request":
                await ws.send(json.dumps({"type": "confirm", "approved": True}))
            if event["type"] == "tool_result":
                tool_results.append(event)
            if event["type"] == "answer":
                answer = event.get("content", "")
                assert not event.get("error"), event
            if event["type"] == "error":
                raise RuntimeError(event)
            if event["type"] == "done":
                break
    assert "BENCH_OK_" + nonce in answer, answer
    seqs = [event["seq"] for event in events if isinstance(event.get("seq"), int) and event["seq"] > 0]
    assert all(b > a for a, b in zip(seqs, seqs[1:]))
    return {"session_id": sid, "answer": answer, "tool_results": tool_results,
            "total_ms": (time.perf_counter() - started) * 1000}


def fixtures(home):
    import pymupdf

    result = []
    text = "PACKAGED_KNOWLEDGE_TOKEN: AgentNexus persists sessions and supports tool cancellation."
    md = home / "fixture.md"
    md.write_text("# Packaging\n\n" + text, encoding="utf-8")
    result.append(md)
    pdf_path = home / "fixture.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((40, 40), text)
    pdf.save(pdf_path)
    pdf.close()
    result.append(pdf_path)
    docx = home / "fixture.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    result.append(docx)
    xlsx = home / "fixture.xlsx"
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                         '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                         '<sheets><sheet name="Packaging" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                         f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{text}</t></is></c></row></sheetData></worksheet>')
    result.append(xlsx)
    return result, text


def verify(binary, out, starts, startup_only):
    out.mkdir(parents=True, exist_ok=True)
    gateway = Gateway()
    checks, start_rows = [], []

    def check(name, action):
        started = time.perf_counter()
        try:
            detail = action()
            row = {"name": name, "success": True, "detail": detail}
        except Exception as exc:
            row = {"name": name, "success": False, "error": str(exc)}
        row["ms"] = (time.perf_counter() - started) * 1000
        checks.append(row)
        save(out / "checks.json", checks)
        print(f"{name}: {'PASS' if row['success'] else 'FAIL'} ({row['ms']:.0f} ms)", flush=True)
        return row

    try:
        for i in range(starts):
            with backend(binary, out / f"startup-{i}", gateway) as state:
                start_rows.append(state)
            save(out / "startup.json", start_rows)
            print(f"startup {i + 1}/{starts}: {state['startup_ms']:.0f} ms", flush=True)
        if startup_only:
            return
        home = out / "feature-home"
        with backend(binary, home, gateway, mcp=True) as state:
            base = state["base"]
            workspace = home / "workspace"
            time.sleep(8)
            with httpx.Client(base_url=base, timeout=180, trust_env=False) as client:
                def request(method, path, **kwargs):
                    response = client.request(method, path, **kwargs)
                    assert response.status_code == 200, response.text
                    return response.json()

                row = check("chat", lambda: asyncio.run(chat(base, workspace, "plain_package")))
                if not row["success"]:
                    raise SystemExit(1)
                plain = row["detail"]
                sid = plain["session_id"]
                for kind, marker in [("file", "314159"), ("shell", "PACKAGED_SHELL_OK"), ("mcp", "PACKAGED_MCP_OK")]:
                    def tool_check(kind=kind, marker=marker):
                        result = asyncio.run(chat(base, workspace, kind + "_package"))
                        assert result["tool_results"], result
                        assert marker in json.dumps(result["tool_results"]), result
                        assert all(not event.get("error") for event in result["tool_results"]), result
                        return result
                    check(kind + "_tool", tool_check)
                def mcp_status():
                    result = request("GET", "/api/mcp/status")
                    assert result["connected_count"] == 1, result
                    assert result["tool_count"] >= 1, result
                    return result
                check("mcp_status", mcp_status)
                def health_check():
                    result = request("GET", "/health")
                    assert result["status"] == "ok", result
                    assert result["checks"]["mcp"]["healthy"] == 1, result
                    return result
                check("health_with_mcp", health_check)
                def builtin_skills():
                    result = request("GET", "/api/skills")
                    assert "code_review" in json.dumps(result), result
                    return result
                check("builtin_skills", builtin_skills)
                files, text = fixtures(home)
                for path in files:
                    def ingestion(path=path):
                        result = request("POST", "/api/kb/documents", files={"file": (path.name, path.read_bytes())})
                        run_id = result["run_id"]
                        deadline = time.perf_counter() + 120
                        while time.perf_counter() < deadline:
                            response = client.get("/api/kb/documents/runs/" + run_id)
                            if response.status_code == 404:
                                time.sleep(0.1)
                                continue
                            assert response.status_code == 200, response.text
                            run = response.json()
                            if run["status"] in {"completed", "failed"}:
                                assert run["status"] == "completed", run
                                assert run["chunks_written"] > 0, run
                                return run
                            time.sleep(0.1)
                        raise TimeoutError("ingestion did not finish")
                    check("ingest_" + path.suffix[1:], ingestion)

                def retrieval():
                    result = request("POST", "/api/kb/search", json={"query": "PACKAGED_KNOWLEDGE_TOKEN", "top_k": 5,
                                     "namespace": "default", "view": "default"})
                    assert result["results"], result
                    assert "PACKAGED_KNOWLEDGE_TOKEN" in json.dumps(result), result
                    assert len(result["results"]) > 1, result
                    assert all(row.get("source") == "rerank" for row in result["results"]), result
                    server_log = Path(state["log"]).read_text(encoding="utf-8", errors="replace")
                    assert "Failed to load reranker model" not in server_log, server_log[-4000:]
                    assert "reranker disabled" not in server_log, server_log[-4000:]
                    return result
                check("retrieval_with_reranker", retrieval)

                def wiki():
                    result = request("POST", "/api/wiki/ingest", json={"source_text": text, "source_uri": "fixture.md"})
                    assert result["statement_count"] > 0, result
                    page = request("GET", "/api/wiki/pages/" + result["page_id"])
                    assert text in page["content"], page
                    return {"page": page, "query": request("POST", "/api/wiki/query", json={"question": "PACKAGED_KNOWLEDGE_TOKEN"})}
                check("wiki", wiki)
                check("history", lambda: request("GET", "/api/memory/short/history", params={"session_id": sid}))
                save(out / "model-requests.json", gateway.requests)
        with backend(binary, home, gateway, mcp=True) as restored:
            with httpx.Client(base_url=restored["base"], trust_env=False, timeout=15) as client:
                def recover():
                    result = client.get("/api/memory/short/history", params={"session_id": sid}).json()
                    assert "BENCH_OK_plain_package" in json.dumps(result), result
                    sessions = client.get("/api/sessions").json()
                    assert sid in json.dumps(sessions), sessions
                    return result
                check("session_recovery", recover)
        def vectors_match():
            import numpy as np

            os.environ["AGENTNEXUS_HOME"] = str(home)
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            from agentnexus.rag.embeddings import embed_texts, get_embedding_model
            from agentnexus.storage.chroma import get_collection

            model = get_embedding_model()
            assert type(model).__name__ == "SentenceTransformer", type(model).__name__
            records = get_collection(namespace="default").get(include=["embeddings", "documents"])
            assert records["ids"], records
            expected = embed_texts([records["documents"][0]])[0]
            actual = records["embeddings"][0]
            error = float(np.max(np.abs(np.array(expected) - np.array(actual))))
            assert error < 1e-5, f"packaged embeddings differ from real model: {error}"
            return {"dimension": len(actual), "max_absolute_error": error, "vector_count": len(records["ids"])}
        check("real_embedding_vectors", vectors_match)
        save(out / "summary.json", {"checks": len(checks), "success": sum(row["success"] for row in checks),
                                   "binary": str(binary), "startups": start_rows})
        decision(out, "verification", "通过公开接口验证冻结后端", "裁剪后功能须与基线一致", out / "checks.json",
                 f"{sum(row['success'] for row in checks)}/{len(checks)} passed")
        if any(not row["success"] for row in checks):
            raise SystemExit(1)
    finally:
        gateway.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--starts", type=int, default=0)
    parser.add_argument("--startup-only", action="store_true")
    args = parser.parse_args()
    verify(args.binary.resolve(), args.out.resolve(), args.starts, args.startup_only)
