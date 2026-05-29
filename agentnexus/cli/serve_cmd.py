"""CLI command: nexus serve — start the HTTP/WebSocket API server."""

import json

import typer

from . import app


@app.command("serve")
def serve(
    port: int = typer.Option(18765, help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    no_auth: bool = typer.Option(False, help="Disable API key authentication"),
):
    """Start the HTTP/WebSocket API server for desktop GUI."""
    import uvicorn

    from agentnexus.server.app import create_app

    fastapi_app = create_app()

    auth_token = None
    if not no_auth:
        from agentnexus.server.auth import generate_token
        auth_token = generate_token()

    ready_msg = json.dumps({"status": "ready", "port": port, "auth_token": auth_token})
    print(ready_msg, flush=True)

    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
