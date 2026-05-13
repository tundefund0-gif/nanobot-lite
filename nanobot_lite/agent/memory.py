"""Session memory: persistence and context management."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.providers.base import Message
from nanobot_lite.utils.helpers import ensure_dir, estimate_tokens


@dataclass
class SessionMessage:
    """A single message in a session."""
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tool_call_id: str | None = None
    tool_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_name:
            d["tool_name"] = self.tool_name
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionMessage":
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", datetime.utcnow().isoformat()),
            tool_call_id=d.get("tool_call_id"),
            tool_name=d.get("tool_name"),
        )


@dataclass
class Session:
    """A chat session with message history."""
    session_key: str
    messages: list[SessionMessage] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    turn_count: int = 0

    def add_message(self, role: str, content: str, **kwargs) -> None:
        self.messages.append(SessionMessage(role=role, content=content, **kwargs))
        self.updated_at = datetime.utcnow().isoformat()
        if role == "user":
            self.turn_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Session":
        return cls(
            session_key=d["session_key"],
            messages=[SessionMessage.from_dict(m) for m in d.get("messages", [])],
            created_at=d.get("created_at", datetime.utcnow().isoformat()),
            updated_at=d.get("updated_at", datetime.utcnow().isoformat()),
            turn_count=d.get("turn_count", 0),
        )


class SessionStore:
    """
    File-based session storage.

    Each session is stored as a JSON file in the sessions directory.
    Uses atomic writes with fsync for durability.
    """

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)
        ensure_dir(self.session_dir)

    def _session_path(self, session_key: str) -> Path:
        """Get the file path for a session."""
        # Sanitize session key for filesystem
        safe_key = "".join(c if c.isalnum() or c in "._-" else "_" for c in session_key)
        return self.session_dir / f"{safe_key}.json"

    def load(self, session_key: str) -> Session | None:
        """Load a session from disk."""
        path = self._session_path(session_key)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text())
            return Session.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to load session {session_key}: {e}")
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk atomically."""
        path = self._session_path(session.session_key)
        tmp_path = path.with_suffix(".tmp")

        try:
            data = json.dumps(session.to_dict(), indent=2, ensure_ascii=False)
            tmp_path.write_text(data, encoding="utf-8")

            # Atomic rename
            if hasattr(os, "replace"):
                os.replace(tmp_path, path)
            else:
                tmp_path.rename(path)

        except Exception as e:
            logger.error(f"Failed to save session {session.session_key}: {e}")
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def delete(self, session_key: str) -> bool:
        """Delete a session."""
        path = self._session_path(session_key)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self) -> list[str]:
        """List all session keys."""
        sessions = []
        for f in self.session_dir.glob("*.json"):
            try:
                key = f.stem
                sessions.append(key)
            except Exception:
                continue
        return sessions

    def get_stats(self, session_key: str) -> dict[str, Any] | None:
        """Get session statistics without loading full content."""
        session = self.load(session_key)
        if not session:
            return None

        total_tokens = sum(estimate_tokens(m.content) for m in session.messages)
        return {
            "session_key": session_key,
            "message_count": len(session.messages),
            "turn_count": session.turn_count,
            "estimated_tokens": total_tokens,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }


class ContextBuilder:
    """Builds the context window for the LLM from session history."""

    def __init__(self, session: Session, system_prompt: str, max_tokens: int = 160000):
        self.session = session
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        # Reserve ~10% for system prompt and tool schemas
        self.turns_max = max_tokens - estimate_tokens(system_prompt) - 2000

    def build(
        self,
        prepend_system: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Build a list of message dicts for the LLM.

        Includes system prompt, conversation history, respecting token budget.
        """
        messages: list[dict[str, Any]] = []

        # System prompt
        if prepend_system:
            messages.append({"role": "system", "content": self.system_prompt})

        # Conversation messages
        available_tokens = self.turns_max
        included_messages: list[SessionMessage] = []

        # Start from most recent, work backwards
        for msg in reversed(self.session.messages):
            msg_tokens = estimate_tokens(msg.content) + 10  # overhead
            if available_tokens - msg_tokens < 0:
                break
            available_tokens -= msg_tokens
            included_messages.insert(0, msg)

        # Convert to dicts
        for msg in included_messages:
            d = msg.to_dict()
            # Remove None values
            d = {k: v for k, v in d.items() if v is not None}
            messages.append(d)

        logger.debug(f"Context: {len(messages)} messages, ~{self.turns_max - available_tokens} tokens")
        return messages

    def needs_compaction(self) -> bool:
        """Check if session needs compaction."""
        total = sum(estimate_tokens(m.content) for m in self.session.messages)
        return total > self.turns_max * 0.85

    def compact(self, keep_recent: int = 10) -> None:
        """
        Compact the session by summarizing older messages.

        Keeps the most recent `keep_recent` messages and summarizes
        everything before that.
        """
        if len(self.session.messages) <= keep_recent:
            return

        recent = self.session.messages[-keep_recent:]
        older = self.session.messages[:-keep_recent]

        # Create a summary
        summary_content = self._summarize_messages(older)

        # Keep system message if present
        system_msgs = [m for m in older if m.role == "system"]
        other_msgs = [m for m in older if m.role != "system"]

        # Clear and rebuild
        self.session.messages = system_msgs + other_msgs[:3]  # keep some context

        # Add summary as a system message
        self.session.messages.append(SessionMessage(
            role="system",
            content=f"[Earlier conversation summarized: {summary_content}]",
        ))

        # Add recent messages back
        self.session.messages.extend(recent)

        logger.info(f"Compacted {len(older)} messages into summary")

    def _summarize_messages(self, messages: list[SessionMessage]) -> str:
        """Create a summary of older messages."""
        topics = []
        for msg in messages:
            content = msg.content[:100]
            if msg.role == "user":
                topics.append(f"User asked about: {content}")
            elif msg.role == "assistant":
                topics.append(f"Assistant responded about: {content}")

        if len(topics) <= 5:
            return "; ".join(topics)
        return f"Conversation covered {len(topics)} exchanges. " + "; ".join(topics[:5]) + "..."
