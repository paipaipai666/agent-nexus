"""Required execution state survives without optional observability."""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def test_execution_state_survives_reopen_without_observability(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = dedent(
        """
        import importlib.abc
        import sys

        class MissingObservability(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "agentnexus.observability" or fullname.startswith("agentnexus.observability."):
                    raise ModuleNotFoundError(fullname)
                return None

        sys.meta_path.insert(0, MissingObservability())
        from agentnexus.storage.timeline import TimelineStore

        store = TimelineStore(sys.argv[1])
        run_id, created = store.claim_submission("session", "request", "work", [])
        assert created is True
        recovery = {"session_id": "session", "run_id": run_id, "answer": "completed once", "persisted": True}
        store.save_run_record(recovery)
        message = store.create_collaboration_message({
            "session_id": "session", "run_id": run_id, "request_id": "child-request",
            "sender": "child", "recipient": "parent", "subagent_id": "child",
            "child_run_id": "attempt", "kind": "question", "content": "Which format?",
            "question_id": "",
        })
        store.apply_collaboration_messages([message["id"]], 3)

        reopened = TimelineStore(sys.argv[1])
        assert reopened.claim_submission("session", "request", "work", []) == (run_id, False)
        assert reopened.get_submission("session", "request")["question"] == "work"
        assert reopened.get_run_record(run_id) == recovery
        saved = reopened.list_collaboration_messages("session", "child")
        assert len(saved) == 1
        assert (saved[0]["content"], saved[0]["delivery"], saved[0]["step_id"]) == ("Which format?", "applied", 3)
        assert not any(name == "agentnexus.observability" or name.startswith("agentnexus.observability.") for name in sys.modules)
        print("Durable execution state survived reopen")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "execution.db")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Durable execution state survived reopen"
