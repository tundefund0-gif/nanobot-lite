"""Plugin system: discovery, loading, lifecycle hooks + SQLite-backed planner."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

try:
    from loguru import logger
except ImportError:
    import sys as _sys

    class _Dummy:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): print(*a, file=_sys.stderr)
        def error(self, *a, **k): print(*a, file=_sys.stderr)
        def success(self, *a, **k): pass
    logger = _Dummy()

from nanobot_lite.bus.events import InboundMessage, OutboundMessage
from nanobot_lite.providers.base import Message as LLMMessage


# ─── Plugin state enum ────────────────────────────────────────────────────────

class PluginState(str, Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


# ─── Plugin dataclass ─────────────────────────────────────────────────────────

@dataclass
class Plugin:
    """
    A loadable plugin unit.

    Plugins can contribute:
    - tools: list of Tool instances (merged into the tool registry)
    - on_load: async callback(PluginSystem) — called when plugin activates
    - on_unload: async callback(PluginSystem) — called when plugin deactivates
    - on_message: async callback(InboundMessage) — pre-process inbound
    - on_response: async callback(OutboundMessage) — post-process outbound
    - on_startup: async callback() — called once on bot startup
    - on_shutdown: async callback() — called once on bot shutdown
    """
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    state: PluginState = PluginState.DISCOVERED
    # Paths
    path: Path | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # Contributions
    tools: list[Any] = field(default_factory=list)
    on_load: Callable[..., Any] | None = None
    on_unload: Callable[..., Any] | None = None
    on_message: Callable[..., Any] | None = None
    on_response: Callable[..., Any] | None = None
    on_startup: Callable[..., Any] | None = None
    on_shutdown: Callable[..., Any] | None = None
    # Stats
    load_time: float = 0.0
    error_msg: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "state": self.state.value,
            "path": str(self.path) if self.path else "",
            "config": self.config,
            "load_time": self.load_time,
            "error_msg": self.error_msg,
        }


# ─── Plugin discovery ─────────────────────────────────────────────────────────

class PluginFinder:
    """
    Discovers plugins by scanning directories for manifest files.

    Manifest format (JSON, named plugin_manifest.json):
    {
      "id": "my_plugin",
      "name": "My Plugin",
      "version": "1.0.0",
      "description": "Does useful things",
      "author": "Dev Name",
      "entry": "my_plugin/__init__.py",  # optional, defaults to __init__.py
      "config": {}
    }
    """

    def __init__(self, search_paths: list[Path] | None = None):
        self.search_paths: list[Path] = search_paths or []

    def add_search_path(self, path: Path) -> None:
        """Add a directory to scan for plugins."""
        if path not in self.search_paths:
            self.search_paths.append(path)

    def discover(self) -> list[Plugin]:
        """Scan all search paths and return discovered plugins."""
        plugins: list[Plugin] = []

        for base_path in self.search_paths:
            if not base_path.is_dir():
                continue

            for manifest_path in base_path.rglob("plugin_manifest.json"):
                plugin = self._load_manifest(manifest_path)
                if plugin:
                    plugins.append(plugin)

        return plugins

    def _load_manifest(self, manifest_path: Path) -> Plugin | None:
        """Load a single plugin manifest."""
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not data.get("id"):
                logger.warning(f"Plugin manifest missing 'id': {manifest_path}")
                return None

            return Plugin(
                id=data["id"],
                name=data.get("name", data["id"]),
                version=data.get("version", "0.0.0"),
                description=data.get("description", ""),
                author=data.get("author", ""),
                path=manifest_path.parent,
                config=data.get("config", {}),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load plugin manifest {manifest_path}: {e}")
            return None


# ─── Plugin loader ─────────────────────────────────────────────────────────────

class PluginLoader:
    """
    Loads and instantiates plugins from Python modules.

    Entry point convention:
    - Looks for {plugin_id}/__init__.py with an `on_load(plugin_system)` function
    - Or any {entry} specified in the manifest
    """

    @staticmethod
    def load(plugin: Plugin) -> Plugin:
        """Load a plugin's Python module and wire up callbacks."""
        if plugin.path is None:
            plugin.state = PluginState.ERROR
            plugin.error_msg = "No plugin path set"
            return plugin

        start = time.monotonic()
        plugin_dir = plugin.path

        try:
            import importlib.util
            import sys

            # Build a plugin namespace
            spec = importlib.util.spec_from_file_location(
                f"nanobot_plugin_{plugin.id}",
                plugin_dir / "__init__.py",
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load spec for {plugin.id}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"nanobot_plugin_{plugin.id}"] = module
            spec.loader.exec_module(module)

            # Wire up lifecycle callbacks
            plugin.on_load = getattr(module, "on_load", None)
            plugin.on_unload = getattr(module, "on_unload", None)
            plugin.on_message = getattr(module, "on_message", None)
            plugin.on_response = getattr(module, "on_response", None)
            plugin.on_startup = getattr(module, "on_startup", None)
            plugin.on_shutdown = getattr(module, "on_shutdown", None)

            # Collect tools (must be Tool instances)
            plugin.tools = []
            for attr_name in dir(module):
                obj = getattr(module, attr_name, None)
                # Accept anything with a `to_schema` method as a tool
                if hasattr(obj, "to_schema") and hasattr(obj, "handler"):
                    plugin.tools.append(obj)

            plugin.state = PluginState.LOADED
            plugin.load_time = time.monotonic() - start
            logger.info(f"Loaded plugin '{plugin.id}' v{plugin.version} "
                        f"({len(plugin.tools)} tools) in {plugin.load_time:.2f}s")

        except Exception as e:
            plugin.state = PluginState.ERROR
            plugin.error_msg = str(e)
            plugin.load_time = time.monotonic() - start
            logger.error(f"Failed to load plugin '{plugin.id}': {e}")

        return plugin

    @staticmethod
    def unload(plugin: Plugin) -> Plugin:
        """Unload a plugin and remove it from sys.modules."""
        if plugin.path:
            mod_name = f"nanobot_plugin_{plugin.id}"
            import sys
            sys.modules.pop(mod_name, None)
        plugin.state = PluginState.DISABLED
        plugin.on_load = None
        plugin.on_unload = None
        plugin.on_message = None
        plugin.on_response = None
        plugin.on_startup = None
        plugin.on_shutdown = None
        plugin.tools = []
        return plugin


# ─── Plugin system ────────────────────────────────────────────────────────────

class PluginSystem:
    """
    Central plugin manager that handles discovery, loading, lifecycle, and hooks.

    Integrates with:
    - ToolRegistry: plugins can contribute tools
    - MessageBus: plugins hook into inbound/outbound processing
    - Planner: tasks created by plugins are stored in SQLite

    Usage:
        ps = PluginSystem(plugins_dir=Path("~/.nanobot_lite/plugins"))
        await ps.discover()
        await ps.load_all()
        await ps.startup()  # calls on_startup hooks
        # ... run agent ...
        await ps.shutdown()  # calls on_shutdown hooks
    """

    def __init__(
        self,
        plugins_dir: Path | None = None,
        db_path: Path | None = None,
    ):
        self.plugins_dir = plugins_dir or (Path.home() / ".nanobot_lite" / "plugins")
        self.db_path = db_path or (Path.home() / ".nanobot_lite" / "plugins.db")

        self._finder = PluginFinder([self.plugins_dir])
        self._plugins: dict[str, Plugin] = {}
        self._tool_registry_ref: Any = None  # set by register_tool_registry
        self._planner: Planner | None = None
        self._started = False

        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite DB for plugin registry and planner."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plugin_registry (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '0.0.0',
                    description TEXT DEFAULT '',
                    author TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'discovered',
                    config TEXT DEFAULT '{}',
                    load_time REAL DEFAULT 0,
                    error_msg TEXT DEFAULT '',
                    loaded_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS planner_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_id TEXT DEFAULT '',
                    task_key TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    subtasks TEXT DEFAULT '[]',
                    result TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS planner_context (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    # ── Tool registry integration ────────────────────────────────────────────

    def register_tool_registry(self, registry: Any) -> None:
        """Allow plugins to register tools into the global tool registry."""
        self._tool_registry_ref = registry

    def register_tool(self, tool: Any) -> None:
        """Register a single tool (from a plugin) into the tool registry."""
        if self._tool_registry_ref is not None:
            self._tool_registry_ref.register(tool)

    # ── Discovery & loading ─────────────────────────────────────────────────

    def discover(self) -> dict[str, Plugin]:
        """Discover all plugins in search paths."""
        for p in self._finder.discover():
            if p.id not in self._plugins:
                self._plugins[p.id] = p
                self._persist_plugin(p)
        return self._plugins

    def discover_plugin(self, path: Path) -> Plugin | None:
        """Discover a single plugin at a given path (no manifest search)."""
        manifest = path / "plugin_manifest.json"
        if not manifest.exists():
            logger.warning(f"No plugin_manifest.json in {path}")
            return None

        plugin = PluginFinder()._load_manifest(manifest)
        if plugin and plugin.id not in self._plugins:
            self._plugins[plugin.id] = plugin
            self._persist_plugin(plugin)
        return plugin

    async def load_plugin(self, plugin_id: str) -> Plugin | None:
        """Load (import) a single plugin by id."""
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            return None

        plugin = PluginLoader.load(plugin)
        if plugin.state == PluginState.LOADED:
            self._persist_plugin(plugin)
        return plugin

    async def load_all(self) -> list[Plugin]:
        """Load all discovered plugins."""
        loaded = []
        for p in self._plugins.values():
            if p.state not in (PluginState.LOADED, PluginState.ACTIVE):
                p = await self.load_plugin(p.id)
                if p and p.state == PluginState.LOADED:
                    loaded.append(p)
        return loaded

    async def unload_plugin(self, plugin_id: str) -> Plugin | None:
        """Unload a plugin, calling its on_unload hook."""
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            return None

        if plugin.on_unload is not None:
            try:
                if asyncio.iscoroutinefunction(plugin.on_unload):
                    await plugin.on_unload(self)
                else:
                    plugin.on_unload(self)
            except Exception as e:
                logger.error(f"on_unload hook failed for '{plugin_id}': {e}")

        # Remove tools from registry
        if self._tool_registry_ref and plugin.tools:
            for tool in plugin.tools:
                self._tool_registry_ref.unregister(tool.name)

        plugin = PluginLoader.unload(plugin)
        self._persist_plugin(plugin)
        return plugin

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Call on_startup hooks on all loaded plugins (once per bot run)."""
        if self._started:
            return
        self._started = True

        for plugin in self._plugins.values():
            if plugin.state not in (PluginState.LOADED, PluginState.ACTIVE):
                continue
            plugin.state = PluginState.ACTIVE
            if plugin.on_startup is not None:
                try:
                    if asyncio.iscoroutinefunction(plugin.on_startup):
                        await plugin.on_startup()
                    else:
                        plugin.on_startup()
                    logger.info(f"Plugin '{plugin.id}' startup complete")
                except Exception as e:
                    logger.error(f"on_startup hook failed for '{plugin.id}': {e}")
                    plugin.state = PluginState.ERROR
                    plugin.error_msg = str(e)
        logger.info(f"PluginSystem startup — {len(self._plugins)} plugins discovered")

    async def shutdown(self) -> None:
        """Call on_shutdown hooks on all active plugins."""
        for plugin in self._plugins.values():
            if plugin.state != PluginState.ACTIVE:
                continue
            if plugin.on_shutdown is not None:
                try:
                    if asyncio.iscoroutinefunction(plugin.on_shutdown):
                        await plugin.on_shutdown()
                    else:
                        plugin.on_shutdown()
                except Exception as e:
                    logger.error(f"on_shutdown hook failed for '{plugin.id}': {e}")
        logger.info("PluginSystem shutdown complete")

    # ── Hooks ──────────────────────────────────────────────────────────────

    async def dispatch_inbound(self, msg: InboundMessage) -> InboundMessage:
        """Run on_message hooks across all active plugins. Pass through last result."""
        for plugin in self._plugins.values():
            if plugin.state != PluginState.ACTIVE or plugin.on_message is None:
                continue
            try:
                if asyncio.iscoroutinefunction(plugin.on_message):
                    result = await plugin.on_message(msg)
                else:
                    result = plugin.on_message(msg)
                if result is not None:
                    msg = result
            except Exception as e:
                logger.error(f"on_message hook failed for '{plugin.id}': {e}")
        return msg

    async def dispatch_response(self, msg: OutboundMessage) -> OutboundMessage:
        """Run on_response hooks across all active plugins."""
        for plugin in self._plugins.values():
            if plugin.state != PluginState.ACTIVE or plugin.on_response is None:
                continue
            try:
                if asyncio.iscoroutinefunction(plugin.on_response):
                    result = await plugin.on_response(msg)
                else:
                    result = plugin.on_response(msg)
                if result is not None:
                    msg = result
            except Exception as e:
                logger.error(f"on_response hook failed for '{plugin.id}': {e}")
        return msg

    async def call_load_hooks(self) -> None:
        """Call on_load hooks on all newly loaded plugins."""
        for plugin in self._plugins.values():
            if plugin.state != PluginState.LOADED or plugin.on_load is None:
                continue
            try:
                if asyncio.iscoroutinefunction(plugin.on_load):
                    await plugin.on_load(self)
                else:
                    plugin.on_load(self)
                plugin.state = PluginState.ACTIVE
            except Exception as e:
                logger.error(f"on_load hook failed for '{plugin.id}': {e}")
                plugin.state = PluginState.ERROR
                plugin.error_msg = str(e)

    # ── Planner integration ─────────────────────────────────────────────────

    def get_planner(self) -> Planner:
        """Get (or create) the planner with SQLite backing."""
        if self._planner is None:
            self._planner = Planner(db_path=self.db_path)
        return self._planner

    # ── Persistence ─────────────────────────────────────────────────────────

    def _persist_plugin(self, plugin: Plugin) -> None:
        """Save plugin state to SQLite."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO plugin_registry
                (id, name, version, description, author, state, config,
                 load_time, error_msg, loaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plugin.id, plugin.name, plugin.version, plugin.description,
                plugin.author, plugin.state.value, json.dumps(plugin.config),
                plugin.load_time, plugin.error_msg,
                now if plugin.state in (PluginState.LOADED, PluginState.ACTIVE) else None,
            ))
            conn.commit()

    def list_plugins(self) -> list[Plugin]:
        """Return all known plugins."""
        return list(self._plugins.values())

    def get_plugin(self, plugin_id: str) -> Plugin | None:
        """Get a plugin by id."""
        return self._plugins.get(plugin_id)


# ─── Planner ──────────────────────────────────────────────────────────────────

@dataclass
class Task:
    """A single task in the planner, persisted to SQLite."""
    id: int | None = None
    plugin_id: str = ""
    task_key: str = ""
    description: str = ""
    status: str = "pending"  # pending | running | done | failed
    priority: int = 0
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    result: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plugin_id": self.plugin_id,
            "task_key": self.task_key,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "subtasks": self.subtasks,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=d.get("id"),
            plugin_id=d.get("plugin_id", ""),
            task_key=d.get("task_key", ""),
            description=d.get("description", ""),
            status=d.get("status", "pending"),
            priority=d.get("priority", 0),
            subtasks=json.loads(d.get("subtasks", "[]")) if isinstance(d.get("subtasks"), str) else d.get("subtasks", []),
            result=d.get("result", ""),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=d.get("updated_at", datetime.now(timezone.utc).isoformat()),
            completed_at=d.get("completed_at"),
        )


class Planner:
    """
    Task planning system backed by SQLite.

    Planner features:
    - Create tasks with sub-task decomposition (LLM-driven or manual)
    - Track task status, priority, and results
    - Context store for cross-task state
    - Auto-save to SQLite on every mutation
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (Path.home() / ".nanobot_lite" / "planner.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        """Ensure planner tables exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS planner_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_id TEXT DEFAULT '',
                    task_key TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    subtasks TEXT DEFAULT '[]',
                    result TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS planner_context (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON planner_tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_plugin ON planner_tasks(plugin_id)
            """)
            conn.commit()

    # ── Task CRUD ───────────────────────────────────────────────────────────

    def create_task(
        self,
        task_key: str,
        description: str,
        plugin_id: str = "",
        priority: int = 0,
        subtasks: list[dict[str, Any]] | None = None,
    ) -> Task:
        """Create and persist a new task."""
        now = datetime.now(timezone.utc).isoformat()
        task = Task(
            task_key=task_key,
            description=description,
            plugin_id=plugin_id,
            priority=priority,
            subtasks=subtasks or [],
            created_at=now,
            updated_at=now,
        )
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                INSERT INTO planner_tasks
                (plugin_id, task_key, description, status, priority, subtasks,
                 result, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.plugin_id, task.task_key, task.description, task.status,
                task.priority, json.dumps(task.subtasks), task.result,
                task.created_at, task.updated_at,
            ))
            task.id = cur.lastrowid
            conn.commit()
        return task

    def get_task(self, task_id: int) -> Task | None:
        """Get a task by id."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return Task.from_dict(dict(row)) if row else None

    def get_task_by_key(self, task_key: str) -> Task | None:
        """Get a task by its unique key."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM planner_tasks WHERE task_key = ?", (task_key,)
            ).fetchone()
        return Task.from_dict(dict(row)) if row else None

    def list_tasks(
        self,
        status: str | None = None,
        plugin_id: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """List tasks with optional filters."""
        query = "SELECT * FROM planner_tasks WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if plugin_id:
            query += " AND plugin_id = ?"
            params.append(plugin_id)
        query += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [Task.from_dict(dict(r)) for r in rows]

    def update_task(self, task: Task) -> Task:
        """Update a task's fields and persist."""
        now = datetime.now(timezone.utc).isoformat()
        task.updated_at = now
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE planner_tasks SET
                    description = ?, status = ?, priority = ?,
                    subtasks = ?, result = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
            """, (
                task.description, task.status, task.priority,
                json.dumps(task.subtasks), task.result,
                task.updated_at, task.completed_at, task.id,
            ))
            conn.commit()
        return task

    def mark_done(self, task_id: int, result: str = "") -> Task | None:
        """Mark a task as completed."""
        task = self.get_task(task_id)
        if not task:
            return None
        now = datetime.now(timezone.utc).isoformat()
        task.status = "done"
        task.result = result
        task.completed_at = now
        task.updated_at = now
        return self.update_task(task)

    def mark_failed(self, task_id: int, error: str = "") -> Task | None:
        """Mark a task as failed."""
        task = self.get_task(task_id)
        if not task:
            return None
        now = datetime.now(timezone.utc).isoformat()
        task.status = "failed"
        task.result = error
        task.updated_at = now
        return self.update_task(task)

    def delete_task(self, task_id: int) -> bool:
        """Delete a task."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM planner_tasks WHERE id = ?", (task_id,))
            conn.commit()
        return cur.rowcount > 0

    def clear_completed(self, older_than_days: int = 7) -> int:
        """Delete completed tasks older than N days. Returns count deleted."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM planner_tasks WHERE status IN ('done','failed') "
                "AND completed_at < ?",
                (cutoff.isoformat(),),
            )
            conn.commit()
        return cur.rowcount

    # ── Sub-task decomposition ──────────────────────────────────────────────

    def decompose(
        self,
        task_id: int,
        provider: Any,
        max_tokens: int = 4096,
    ) -> list[dict[str, Any]]:
        """
        Use the LLM to decompose a task into subtasks.

        Sends the task description to the LLM and expects a JSON list of:
        [{"step": 1, "description": "...", "action": "..."}, ...]
        """
        task = self.get_task(task_id)
        if not task:
            return []

        prompt = (
            f"Decompose the following task into clear, sequential steps. "
            f"Return ONLY a JSON list with this format: "
            f'[{{"step": 1, "description": "...", "action": "..."}}]\n\n'
            f"Task: {task.description}"
        )
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            response = asyncio.run(provider.chat(
                messages=messages,
                tools=None,
                max_tokens=max_tokens,
            ))
            raw = response.content.strip()
            # Try to extract JSON
            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if json_match:
                subtasks = json.loads(json_match.group(0))
                # Persist
                task.subtasks = subtasks
                self.update_task(task)
                return subtasks
        except Exception as e:
            logger.error(f"Planner decompose failed: {e}")
        return []

    # ── Context store ──────────────────────────────────────────────────────

    def set_context(self, key: str, value: str) -> None:
        """Store a key-value pair for cross-task context."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO planner_context (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, now))
            conn.commit()

    def get_context(self, key: str, default: str = "") -> str:
        """Get a context value."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM planner_context WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def delete_context(self, key: str) -> None:
        """Delete a context key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM planner_context WHERE key = ?", (key,))
            conn.commit()

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return planner statistics."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM planner_tasks").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM planner_tasks WHERE status='pending'"
            ).fetchone()[0]
            running = conn.execute(
                "SELECT COUNT(*) FROM planner_tasks WHERE status='running'"
            ).fetchone()[0]
            done = conn.execute(
                "SELECT COUNT(*) FROM planner_tasks WHERE status='done'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM planner_tasks WHERE status='failed'"
            ).fetchone()[0]
        return {
            "total": total,
            "pending": pending,
            "running": running,
            "done": done,
            "failed": failed,
        }