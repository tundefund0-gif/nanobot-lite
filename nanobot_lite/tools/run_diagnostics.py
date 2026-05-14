"""Self-diagnostic tool — exposes full system health check."""
from __future__ import annotations

from typing import Any

from nanobot_lite.agent.healer import get_healer
from nanobot_lite.agent.self_diagnosis import run_diagnostics


def create_diagnostics_tools() -> list:
    return [_RunDiagnosticsTool()]


class _RunDiagnosticsTool:
    name = "run_diagnostics"
    description = (
        "Run a full self-diagnostic checkup: health scores, circuit breaker "
        "status, recent failures, rollback history, and system state."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": (
                    "Specific section to check: 'health', 'circuits', 'paths', "
                    "'rollbacks', 'system', 'all'. Default: 'all'"
                ),
            }
        },
        "required": [],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        import asyncio
        section = arguments.get("section", "all")

        # Run diagnostics in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_diagnostics, section)

        # Append healer health report
        healer = get_healer()
        if section in ("all", "health"):
            result += "\n\n" + healer.health_report()
        if section in ("all", "circuits"):
            import json
            result += "\n\n## ⚡ Circuit Breaker\n```json\n"
            result += json.dumps(healer.circuit_breaker.report(), indent=2)
            result += "\n```"

        return result
