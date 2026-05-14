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

from nanobot_lite.agent.healer import get_healer, ToolHealth, PerErrorCircuitBreaker


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
        from nanobot_lite.agent.healer import get_healer
        healer = get_healer()
        # Build a richer health report
        lines = ["## 🏥 Tool Health\n"]
        tools = list(healer.health._tools.values())
        if not tools:
            lines.append("(no health data yet)")
            return "\n".join(lines)

        for h in sorted(tools, key=lambda x: x.health_score):
            age_ok = ""
            if h.last_success:
                secs = int(time.time() - h.last_success)
                age_ok = f" | last OK {secs}s ago"
            age_fail = ""
            if h.last_failure:
                secs = int(time.time() - h.last_failure)
                age_fail = f" | last fail {secs}s ago"
            top = h.top_errors(3)
            err_str = ""
            if top:
                err_str = "\n  Top errors: " + ", ".join(f"{e}({c})" for e, c in top)
            lines.append(
                f"{h.status} **{h.name}**\n"
                f"  score={h.health_score:.2f} | "
                f"calls={h.total_calls} | fails={h.failures} | "
                f"healed={h.heals_success}/{h.heals_failed} | "
                f"avg_passes={h.avg_heal_passes:.1f}"
                f"{age_ok}{age_fail}{err_str}"
            )
        return "\n".join(lines)

    @staticmethod
    def _circuits() -> str:
        healer = get_healer()
        report = healer.circuit_breaker.report()

        lines = []
        for tool, et_map in sorted(report.items()):
            lines.append(f"### `{tool}`")
            for et, state in sorted(et_map.items()):
                open_ = state.get("open", False)
                status = "🔴 OPEN" if open_ else "🟢 CLOSED"
                failures = state.get("failures", 0)
                last = state.get("last_failure")
                age = ""
                if last:
                    try:
                        dt = datetime.fromisoformat(last)
                        secs_ago = int(time.time() - dt.timestamp())
                        age = f" ({secs_ago}s ago)"
                    except Exception:
                        age = f" ({last})"
                et_label = et if et != "_global_" else "all errors"
                lines.append(
                    f"  {status} **{et_label}** — "
                    f"failures={failures}{age}"
                )
        return "\n".join(lines) if lines else (
            "🟢 All circuits closed — no tool failures detected."
        )

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
            workspace = registry.get_context("workspace", os.getcwd())
            restrict = registry.get_context("restrict_to_workspace", True)
        except Exception:
            workspace = os.getcwd()
            restrict = True

        lines = [
            f"**Root:** `{workspace}`",
            f"**Restricted:** {restrict}",
            f"**Absolute:** `{Path(workspace).resolve()}`",
        ]

        wp = Path(workspace) if workspace != "unknown" else None
        if wp and wp.exists():
            try:
                all_files = list(wp.rglob("*"))
                dirs = [f for f in all_files if f.is_dir()]
                files = [f for f in all_files if f.is_file()]
                lines.append(f"**Contents:** {len(files)} file(s), {len(dirs)} dir(s)")

                # Python files
                py_files = [f for f in files if f.suffix == ".py"]
                lines.append(f"**Python files:** {len(py_files)}")

                # Config files
                config_files = [
                    f for f in files
                    if f.suffix in (".yaml", ".yml", ".toml", ".json", ".env")
                    or f.name.startswith(".env")
                ]
                if config_files:
                    lines.append(f"**Config files:** {len(config_files)}")
                    for cf in config_files[:5]:
                        lines.append(f"  - `{cf.relative_to(wp)}`")

                # Git status
                git_dir = wp / ".git"
                if git_dir.exists():
                    lines.append(f"**Git:** ✅ initialized")
                else:
                    lines.append(f"**Git:** ❌ not initialized")
            except PermissionError:
                lines.append("_(permission denied to enumerate)_")
        else:
            lines.append("⚠️ Workspace does not exist!")

        return "\n".join(lines)

    @staticmethod
    def _environment() -> str:
        lines = ["## 🌐 Environment\n"]

        # Key env vars
        key_vars = [
            "PYTHONPATH", "HOME", "USER", "PATH", "LANG",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY",
            "TELEGRAM_BOT_TOKEN", "TG_API_ID", "TG_API_HASH",
        ]
        for var in key_vars:
            val = os.environ.get(var, "")
            if val:
                if any(s in var for s in ("KEY", "TOKEN", "HASH", "SECRET")):
                    val = val[:6] + "***" if len(val) > 6 else "***"
                lines.append(f"**{var}:** `{val}`")
            else:
                lines.append(f"**{var}:** _(not set)_")

        # Config file
        config_path = Path.home() / ".nanobot_lite" / "config.yaml"
        if config_path.exists():
            lines.append(f"\n**Config:** ✅ `{config_path}`")
        else:
            lines.append(f"\n**Config:** ❌ not found")

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
            "health":     d._health,
            "circuits":   d._circuits,
            "memory":     d._memory,
            "system":     d._system,
            "workspace":  d._workspace,
            "runtime":    d._runtime,
            "rollbacks":  d._rollbacks,
            "env":        d._environment,
        }
        fn = section_map.get(section)
        result = fn() if fn else "Unknown section"
    else:
        result = d.run_all()

    return result
