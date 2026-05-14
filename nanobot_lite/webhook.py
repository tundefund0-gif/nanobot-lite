"""HTTP webhook trigger server — triggers agent actions via HTTP callbacks."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

try:
    from loguru import logger
except ImportError:
    import sys as _sys
    class _Dummy:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): print(*a, file=_sys.stderr)
        def warning(self, *a, **k): print(*a, file=_sys.stderr)
        def error(self, *a, **k): print(*a, file=_sys.stderr)
        def success(self, *a, **k): pass
    logger = _Dummy()


# ─── Handler registry ─────────────────────────────────────────────────────────

@dataclass
class WebhookHandler:
    """A registered webhook handler."""
    name: str
    path: str
    func: Callable[..., Any]
    auth_required: bool = True
    methods: list[str] | None = None  # ["POST"] or None for all


class WebhookServer:
    """
    Tiny HTTP server that triggers agent actions via webhooks.
    Built with stdlib http.server — no aiohttp dependency needed.

    Endpoints:
      GET  /health              → health check
      POST /webhook/<name>      → trigger named webhook
      POST /broadcast           → broadcast to all sessions

    Config (in config.yaml):
      webhook:
        enabled: true
        port: 8080
        host: "0.0.0.0"
        token: "secret-token"    # X-Webhook-Token header required
    """

    def __init__(
        self,
        bus: Any | None = None,
        config: Any | None = None,
        port: int = 8080,
        host: str = "0.0.0.0",
        token: str | None = None,
    ):
        self.bus = bus
        self.config = config
        self.port = port
        self.host = host
        self.token = token or os.environ.get("WEBHOOK_TOKEN", "")
        self._handlers: dict[str, WebhookHandler] = {}
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._running = False

        # Register built-in handlers
        self._register_builtin()

    def _register_builtin(self) -> None:
        self.register_trigger(
            "health",
            "/health",
            lambda req: self._health_response(),
            auth_required=False,
            methods=["GET"],
        )
        self.register_trigger(
            "broadcast",
            "/broadcast",
            self._handle_broadcast,
            auth_required=True,
            methods=["POST"],
        )

    def register_trigger(
        self,
        name: str,
        path: str,
        func: Callable[..., Any],
        auth_required: bool = True,
        methods: list[str] | None = None,
    ) -> None:
        """Register a webhook handler."""
        if not path.startswith("/"):
            path = "/" + path
        handler = WebhookHandler(
            name=name,
            path=path,
            func=func,
            auth_required=auth_required,
            methods=methods,
        )
        self._handlers[path] = handler
        logger.info(f"Registered webhook: {path} ({name})")

    def unregister(self, path: str) -> bool:
        if path in self._handlers:
            del self._handlers[path]
            return True
        return False

    # ── Built-in handlers ─────────────────────────────────────────────────────

    def _health_response(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "nanobot-lite",
            "version": "0.6.0",
            "webhooks": len(self._handlers),
        }

    def _handle_broadcast(self, request: dict[str, Any]) -> dict[str, Any]:
        """Broadcast message to all active sessions via the bus."""
        if not self.bus:
            return {"success": False, "error": "No message bus configured"}

        message = request.get("message", "")
        user_filter = request.get("user_id")

        # Enqueue a broadcast message
        from nanobot_lite.bus.events import Message, MessageRole
        msg = Message(
            role=MessageRole.SYSTEM,
            content=f"[BROADCAST] {message}",
            name="webhook",
        )

        try:
            self.bus.inbound.put_nowait(msg)
            return {"success": True, "queued": True, "user_filter": user_filter}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Start / stop ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the webhook server in a background thread."""
        if self._running:
            logger.warning("Webhook server already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        logger.success(f"Webhook server started on {self.host}:{self.port}")

    def stop(self) -> None:
        """Stop the webhook server."""
        self._running = False
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Webhook server stopped")

    def _run_server(self) -> None:
        import http.server
        import socketserver

        class _Handler(http.server.BaseHTTPRequestHandler):
            _server: WebhookServer = self  # type: ignore

            def log_message(self, fmt, *args):
                pass  # suppress default logging

            def _check_auth(self) -> bool:
                token = self.headers.get("X-Webhook-Token", "")
                if self._server.token and token != self._server.token:
                    self.send_error(401, "Unauthorized")
                    return False
                return True

            def _json_response(self, data: dict[str, Any], status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_GET(self) -> None:
                self._handle_request("GET")

            def do_POST(self) -> None:
                self._handle_request("POST")

            def _handle_request(self, method: str) -> None:
                path = self.path.split("?")[0]

                # Find handler
                handler = self._server._handlers.get(path)
                if not handler:
                    self.send_error(404, "Not Found")
                    return

                if handler.methods and method not in handler.methods:
                    self.send_error(405, "Method Not Allowed")
                    return

                if handler.auth_required and not self._check_auth():
                    return

                # Parse body for POST
                body = {}
                if method == "POST":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        if length:
                            raw = self.rfile.read(length).decode("utf-8", errors="replace")
                            body = json.loads(raw) if raw else {}
                    except Exception as e:
                        logger.warning(f"Webhook parse error: {e}")
                        body = {}

                # Call handler
                try:
                    result = handler.func(body)
                    self._json_response(result if isinstance(result, dict) else {"result": result})
                except Exception as e:
                    logger.error(f"Webhook handler error: {e}")
                    self._json_response({"error": str(e)}, 500)

        class _TCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        try:
            self._server = _TCPServer((self.host, self.port), _Handler)
            self._server.serve_forever(poll_interval=0.5)
        except Exception as e:
            logger.error(f"Webhook server error: {e}")
        finally:
            self._running = False

    # ── CLI helpers ────────────────────────────────────────────────────────────

    def test_trigger(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Test a webhook by name (for CLI use)."""
        path = f"/webhook/{name}"
        handler = self._handlers.get(path)
        if not handler:
            return {"success": False, "error": f"Unknown webhook: {name}"}

        try:
            result = handler.func(payload or {})
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_webhooks(self) -> list[dict[str, Any]]:
        """Return list of registered webhook info."""
        return [
            {"name": h.name, "path": h.path, "auth_required": h.auth_required, "methods": h.methods}
            for h in self._handlers.values()
        ]


# ─── CLI integration helpers ──────────────────────────────────────────────────

def build_webhook_server(config: Any, bus: Any | None = None) -> WebhookServer | None:
    """Build a webhook server from config (if enabled)."""
    try:
        wh_cfg = config.webhook
        if not getattr(wh_cfg, "enabled", False):
            return None
        return WebhookServer(
            bus=bus,
            config=config,
            port=getattr(wh_cfg, "port", 8080),
            host=getattr(wh_cfg, "host", "0.0.0.0"),
            token=getattr(wh_cfg, "token", ""),
        )
    except Exception:
        return None