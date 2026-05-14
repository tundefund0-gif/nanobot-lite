"""Self-diagnostic tool — agent can diagnose its own state."""
from __future__ import annotations

import asyncio
import gc
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.agent.healer import get_healer, ToolHealth


# ─── Diagnostic sections ─────────────────────────────────────────────────────

class SelfDiagnostician:
    """
    Provides self-diagnostic information about the agent's internal state,
    tool health, circuit breakers, and system resources.
    """

    @staticmethod
    def run_all() -> str:
        """Run all diagnostic checks and return a formatted report."""
        sections = [
            ("🏥 Tool Health",       SelfDiagnostician._health),
            ("⚡ Circuit Breakers",    SelfDiagnostician._circuits),
            ("💾 Memory & GC",        SelfDiagnostician._memory),
            ("🖥️  System Info",       SelfDiagnostician._system),
            ("📁 Workspace",           SelfDiagnostician._workspace),
            ("🔧 Runtime Stats",      SelfDiagnostician._runtime),
            ("🔄 Rollback Backups",   SelfDiagnostician._rollbacks),
        ]

        report_lines = [
            "## 🔍 Self-Diagnostic Report",
            f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_\n",
        ]

        for title, fn in sections:
            try:
                result = fn()
                report_lines.append(f"### {title}\n{result}\n")
            except Exception as e:
                report_lines.append(f"### {title}\n⚠️ Error running diagnostics: {e}\n")

        return "\n".join(report_lines)

    @staticmethod
    def _health() -> str:
        healer = get_healer()
        return healer.health_report()

    @staticmethod
    def _circuits() -> str:
        healer = get_healer()
        report = healer.circuit_report()

        lines = []
        for tool, state in sorted(report.items()):
            status = "🔴 OPEN" if state["open"] else ("🟡 HALF-OPEN" if state["half_open"] else "🟢 CLOSED")
            lines.append(
                f"**{tool}**: {status} — "
                f"failures={state['failures']}, "
                f"last={state['last_failure'] or 'never'}"
            )
        return "\n".join(lines) if lines else "🟢 All circuits closed — no tool failures detected."

    @staticmethod
    def _memory() -> str:
        lines = []

        # Python memory
        try:
            import psutil
            process = psutil.Process()
            mem = process.memory_info()
            lines.append(
                f"Process RSS: {mem.rss // (1024*1024)}MB | "
                f"VMS: {mem.vms // (1024*1024)}MB"
            )
        except ImportError:
            lines.append("(psutil not available)")

        # GC stats
        gc.collect()
        stats = gc.get_stats()
        if stats:
            total_alloc = sum(s.get("total_allocated_size", 0) for s in stats)
            lines.append(f"GC: {len(gc.get_objects())} objects tracked, "
                         f"{total_alloc // (1024*1024)}MB allocated")

        # /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                content = f.read()
            total_k = int(next((l for l in content.splitlines() if l.startswith("MemTotal:")), "0").split()[1])
            avail_k = int(next((l for l in content.splitlines() if l.startswith("MemAvailable:")), "0").split()[1])
            used_mb = (total_k - avail_k) // 1024
            total_mb = total_k // 1024
            pct = used_mb / total_mb * 100
            bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
            lines.append(f"\nSystem RAM: [{bar}] {pct:.0f}% used")
            lines.append(f"  Used: {used_mb}MB | Total: {total_mb}MB | Available: {avail_k//1024}MB")
        except Exception:
            pass

        return "\n".join(lines) if lines else "No memory info available."

    @staticmethod
    def _system() -> str:
        lines = [
            f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
            f"Python: {platform.python_version()}",
            f"Hostname: {platform.node()}",
            f"Python executable: {sys.executable}",
        ]

        # CPU
        try:
            cpu_count = os.cpu_count() or 1
            lines.append(f"CPU cores: {cpu_count}")

            try:
                with open("/proc/cpuinfo") as f:
                    content = f.read()
                model_match = content.split("model name")[1].split("\n")[0].split(":", 1)[1].strip() \
                    if "model name" in content else None
                if model_match:
                    lines.append(f"CPU: {model_match}")
            except Exception:
                pass
        except Exception:
            pass

        # Uptime
        try:
            with open("/proc/uptime") as f:
                uptime_sec = float(f.read().split()[0])
            days = int(uptime_sec // 86400)
            hours = int((uptime_sec % 86400) // 3600)
            mins = int((uptime_sec % 3600) // 60)
            lines.append(f"Uptime: {days}d {hours}h {mins}m")
        except Exception:
            pass

        return "\n".join(lines)

    @staticmethod
    def _workspace() -> str:
        try:
            from nanobot_lite.tools.base import get_registry
            registry = get_registry()
            workspace = registry.get_context("workspace", "unknown")
            restrict = registry.get_context("restrict_to_workspace", True)
        except Exception:
            workspace = os.getcwd()
            restrict = True

        lines = [f"Path: `{workspace}`", f"Restricted: {restrict}"]

        wp = Path(workspace) if workspace != "unknown" else None
        if wp and wp.exists():
            try:
                all_files = list(wp.rglob("*"))
                dirs = [f for f in all_files if f.is_dir()]
                files = [f for f in all_files if f.is_file()]
                lines.append(f"Contents: {len(files)} file(s), {len(dirs)} dir(s)")
            except PermissionError:
                lines.append("(permission denied to enumerate)")
        else:
            lines.append("⚠️ Workspace does not exist!")

        return "\n".join(lines)

    @staticmethod
    def _runtime() -> str:
        lines = [
            f"Python path: {sys.executable}",
            f"sys.path entries: {len(sys.path)}",
            f"Loaded modules: {len(sys.modules)}",
        ]

        # Check for key modules
        key_modules = ["loguru", "httpx", "aiohttp", "anthropic"]
        for mod in key_modules:
            status = "✅" if mod in sys.modules else "❌"
            lines.append(f"  {status} {mod}")

        return "\n".join(lines)

    @staticmethod
    def _rollbacks() -> str:
        healer = get_healer()
        rb = healer.rollback
        lines = []

        if not rb._backups:
            return "📂 No backups yet."

        for file_path, backups in sorted(rb._backups.items()):
            lines.append(f"**{Path(file_path).name}** ({len(backups)} backup(s)):")
            for b in backups:
                age = datetime.now(timezone.utc).timestamp() - b.stat().st_mtime
                age_str = f"{int(age // 60)}m ago" if age < 3600 else f"{int(age // 3600)}h ago"
                lines.append(f"  • {b.name} ({b.stat().st_size} bytes, {age_str})")

        return "\n".join(lines) if lines else "📂 No backups yet."


# ─── Diagnostic tool ───────────────────────────────────────────────────────────

async def run_diagnostics(section: str | None = None) -> str:
    """
    Run agent self-diagnostics.
    If section is specified, run only that section.
    Otherwise, run all.
    """
    available = {
        "health":   "Tool Health",
        "circuits": "Circuit Breakers",
        "memory":   "Memory & GC",
        "system":   "System Info",
        "workspace":"Workspace",
        "runtime":  "Runtime Stats",
        "rollbacks":"Rollback Backups",
        "all":      "Full Report",
    }

    if section and section not in available:
        return (
            f"Unknown section: {section}\n\n"
            f"Available sections: {', '.join(available.keys())}"
        )

    d = SelfDiagnostician()
    if section and section != "all":
        section_map = {
            "health":    d._health,
            "circuits":  d._circuits,
            "memory":    d._memory,
            "system":    d._system,
            "workspace": d._workspace,
            "runtime":   d._runtime,
            "rollbacks": d._rollbacks,
        }
        fn = section_map.get(section)
        result = fn() if fn else "Unknown section"
    else:
        result = d.run_all()

    return result
