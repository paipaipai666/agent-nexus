"""CLI command: nexus serve — start the HTTP/WebSocket API server."""

import json
import logging

import typer

from . import app, console

logger = logging.getLogger(__name__)


@app.command("serve")
def serve(
    port: int = typer.Option(18765, help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    no_auth: bool = typer.Option(False, help="Disable API key authentication"),
):
    """Start the HTTP/WebSocket API server for desktop GUI."""
    import uvicorn

    from agentnexus.server.app import create_app

    try:
        fastapi_app = create_app()

        auth_token = None
        if not no_auth:
            from agentnexus.server.auth import generate_token
            auth_token = generate_token()

        ready_msg = json.dumps({"status": "ready", "port": port, "auth_token": auth_token})
        print(ready_msg, flush=True)

        uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
    except OSError as e:
        if "address already in use" in str(e).lower() or "only one usage" in str(e).lower():
            console.print(f"[red]Port {port} is already in use. Try --port with a different number.[/red]")
        else:
            console.print(f"[red]Server failed to start: {e}[/red]")
        raise SystemExit(1)
    except Exception as e:
        logger.exception("Unexpected server startup error")
        console.print(f"[red]Server failed to start. Check logs for details.[/red]")
        raise SystemExit(1)
