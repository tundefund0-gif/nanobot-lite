"""Self-awareness and meta-cognition for nanobot-lite."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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


# ─── Awareness levels ───────────────────────────────────────────────────────

@dataclass
class AwarenessLevel:
    """A dimension of self-awareness."""
    name: str
    score: float = 1.0       # 0.0–1.0, how aware we are
    confidence: float = 0.8  # how confident we are in this assessment
    trend: str = "stable"    # improving | declining | stable
    notes: list[str] = field(default_factory=list)

    def bump_note(self, note: str) -> None:
        self.notes.append(f"{datetime.now(timezone.utc).isoformat()[:19]} — {note}")
        # Keep only last 10 notes per dimension
        if len(self.notes) > 10:
            self.notes = self.notes[-10:]


@dataclass
class BehaviorPattern:
    """A recognized pattern in agent behavior."""
    pattern_id: str
    description: str
    frequency: int = 0
    success_rate: float = 0.5
    first_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    context_hints: list[str] = field(default_factory=list)

    def touch(self, success: bool) -> None:
        self.frequency += 1
        if self.frequency > 1:
            self.success_rate = (self.success_rate * (self.frequency - 1) + (1.0 if success else 0.0)) / self.frequency
        self.last_seen = datetime.now(timezone.utc).isoformat()


# ─── Decision record ────────────────────────────────────────────────────────

@dataclass
class DecisionRecord:
    """A recorded decision with reasoning."""
    decision_id: str
    context: str            # what was the situation
    choice: str             # what was decided
    reasoning: str          # why
    outcome: str = ""        # what happened
    success: bool | None = None
    confidence: float = 0.5  # how sure was the agent
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta_level: int = 0      # 0 = base decision, 1 = decision about decisions, etc.


@dataclass
class AwarenessStats:
    """Snapshot of awareness metrics."""
    total_decisions: int = 0
    good_decisions: int = 0
    decision_accuracy: float = 0.5
    avg_confidence: float = 0.5
    active_patterns: int = 0
    self_corrections: int = 0
    awareness_score: float = 0.0
    metacognitive_depth: int = 0


# ─── Self-Awareness Engine ───────────────────────────────────────────────────

class SelfAwareness:
    """
    Meta-cognitive self-awareness module.

    Tracks:
    - Agent confidence and reasoning quality
    - Behavioral patterns (successful and failing)
    - Decision history and outcomes
    - Awareness dimensions (task understanding, tool usage, communication, etc.)
    - Self-correction events

    Provides:
    - Self-assessment reports
    - Confidence scoring for responses
    - Pattern-based guidance for future decisions
    - Anomaly detection (unusual behavior)
    """

    def __init__(self):
        # ── Awareness dimensions ────────────────────────────────────────────────
        self.dimensions: dict[str, AwarenessLevel] = {
            "task_understanding": AwarenessLevel("Task Understanding",
                score=0.8, notes=["Initial awareness of task goals and constraints"]),
            "tool_usage": AwarenessLevel("Tool Usage",
                score=0.8, notes=["Awareness of which tools to use and when"]),

            "reasoning_quality": AwarenessLevel("Reasoning Quality",
                score=0.7, notes=["Quality of multi-step reasoning"]),
            "communication": AwarenessLevel("Communication",
                score=0.9, notes=["Clarity and helpfulness of responses"]),
            "error_recovery": AwarenessLevel("Error Recovery",
                score=0.7, notes=["Ability to recover from failures"]),
            "context_tracking": AwarenessLevel("Context Tracking",
                score=0.6, notes=["Keeping track of conversation state"]),
            "planning": AwarenessLevel("Planning",
                score=0.6, notes=["Ability to plan multi-step tasks"]),
            "self_correction": AwarenessLevel("Self-Correction",
                score=0.5, notes=["Ability to recognize and fix own mistakes"]),
        }

        # ── Behavioral patterns ─────────────────────────────────────────────────
        self._patterns: dict[str, BehaviorPattern] = {}
        self._pattern_counter = 0

        # ── Decision history ────────────────────────────────────────────────────
        self._decisions: list[DecisionRecord] = []
        self._decision_counter = 0
        self.max_decision_history = 200

        # ── Self-correction log ─────────────────────────────────────────────────
        self._corrections: list[dict[str, Any]] = []
        self.max_corrections = 100

        # ── Anomaly detection ──────────────────────────────────────────────────
        self._recent_errors: list[str] = []
        self.max_recent_errors = 50

        # ── Performance counters ───────────────────────────────────────────────
        self._total_turns = 0
        self._successful_turns = 0
        self._confidence_samples: list[float] = []
        self._last_turn_time = time.time()
        self._session_start = time.time()

    # ── Core awareness updates ─────────────────────────────────────────────

    def record_turn(self, success: bool, confidence: float = 0.5) -> None:
        """Record an agent turn outcome for awareness tracking."""
        self._total_turns += 1
        if success:
            self._successful_turns += 1
        self._confidence_samples.append(confidence)
        # Keep last 100 samples
        if len(self._confidence_samples) > 100:
            self._confidence_samples = self._confidence_samples[-100:]
        self._last_turn_time = time.time()

    def record_decision(
        self,
        context: str,
        choice: str,
        reasoning: str,
        confidence: float = 0.5,
        meta_level: int = 0,
    ) -> str:
        """Record a decision for meta-cognitive analysis."""
        self._decision_counter += 1
        decision_id = f"dec_{self._decision_counter}"
        record = DecisionRecord(
            decision_id=decision_id,
            context=context[:200],
            choice=choice[:100],
            reasoning=reasoning[:300],
            confidence=confidence,
            meta_level=meta_level,
        )
        self._decisions.append(record)
        if len(self._decisions) > self.max_decision_history:
            self._decisions = self._decisions[-self.max_decision_history:]
        return decision_id

    def resolve_decision(self, decision_id: str, success: bool, outcome: str = "") -> None:
        """Update a decision record with outcome."""
        for rec in reversed(self._decisions):
            if rec.decision_id == decision_id:
                rec.success = success
                rec.outcome = outcome[:200]
                break

    def record_error(self, error_type: str, tool: str = "", recovery: str = "") -> None:
        """Record an error for pattern analysis."""
        self._recent_errors.append(
            f"{datetime.now(timezone.utc).isoformat()[:19]} | {error_type} | tool={tool}"
        )
        if len(self._recent_errors) > self.max_recent_errors:
            self._recent_errors = self._recent_errors[-self.max_recent_errors:]

        # Track tool-specific error patterns
        self._track_pattern(
            pattern_id=f"error_{tool}_{error_type[:30]}",
            description=f"Error pattern: {error_type} in {tool}",
            success=False,
            context_hints=[error_type, tool, recovery],
        )

    def record_self_correction(
        self,
        what_was_wrong: str,
        what_was_corrected: str,
        success: bool,
    ) -> None:
        """Record a self-correction event."""
        correction = {
            "id": f"corr_{len(self._corrections)+1}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "what_was_wrong": what_was_wrong,
            "what_was_corrected": what_was_corrected,
            "success": success,
        }
        self._corrections.append(correction)
        if len(self._corrections) > self.max_corrections:
            self._corrections = self._corrections[-self.max_corrections:]

        # Update self-correction dimension
        self.dimensions["self_correction"].score = min(1.0,
            self.dimensions["self_correction"].score + (0.05 if success else -0.03))
        self.dimensions["self_correction"].bump_note(
            f"{'✅' if success else '❌'} {what_was_wrong[:60]}"
        )

    # ── Pattern tracking ────────────────────────────────────────────────────

    def _track_pattern(
        self,
        pattern_id: str,
        description: str,
        success: bool,
        context_hints: list[str] | None = None,
    ) -> None:
        """Track a behavioral pattern."""
        if pattern_id not in self._patterns:
            self._pattern_counter += 1
            self._patterns[pattern_id] = BehaviorPattern(
                pattern_id=pattern_id,
                description=description,
                context_hints=context_hints or [],
            )
        self._patterns[pattern_id].touch(success)

    def record_action_pattern(
        self,
        action_type: str,
        tool: str = "",
        success: bool = True,
        context: str = "",
    ) -> None:
        """Record a tool usage pattern."""
        pid = f"action_{tool}_{action_type[:30]}"
        self._track_pattern(
            pattern_id=pid,
            description=f"Action: {action_type} using {tool}",
            success=success,
            context_hints=[action_type, tool, context[:50] if context else ""],
        )

    # ── Awareness dimensions ────────────────────────────────────────────────

    def update_dimension(self, name: str, delta: float, note: str = "") -> None:
        """Update an awareness dimension score by delta."""
        if name not in self.dimensions:
            self.dimensions[name] = AwarenessLevel(name)
        dim = self.dimensions[name]
        dim.score = max(0.0, min(1.0, dim.score + delta))
        if note:
            dim.bump_note(note)

    def set_dimension_confidence(self, name: str, confidence: float) -> None:
        """Update confidence in a dimension assessment."""
        if name in self.dimensions:
            self.dimensions[name].confidence = max(0.0, min(1.0, confidence))

    def set_dimension_trend(self, name: str, trend: str) -> None:
        """Set the trend direction for a dimension."""
        if name in self.dimensions:
            self.dimensions[name].trend = trend

    # ── Confidence scoring ──────────────────────────────────────────────────

    def overall_confidence(self) -> float:
        """Compute overall agent confidence based on recent history."""
        if not self._confidence_samples:
            return 0.5
        # Weighted average, more recent samples weight more
        samples = self._confidence_samples[-20:]
        total_weight = 0.0
        weighted_sum = 0.0
        for i, s in enumerate(samples):
            weight = i + 1
            weighted_sum += s * weight
            total_weight += weight
        return weighted_sum / total_weight if total_weight else 0.5

    def confidence_for_context(self, context: str) -> float:
        """Estimate confidence for a given context based on pattern matching."""
        base = self.overall_confidence()
        # Boost confidence if we have successful patterns for this context
        matching = [
            p for p in self._patterns.values()
            if any(context.lower() in h.lower() for h in p.context_hints)
            and p.success_rate >= 0.7
        ]
        boost = min(0.15, len(matching) * 0.03)
        return min(1.0, base + boost)

    # ── Self-assessment report ──────────────────────────────────────────────

    def generate_report(self) -> str:
        """Generate a comprehensive self-awareness report."""
        lines = ["## 🧠 Self-Awareness Report", ""]

        # Stats snapshot
        stats = self.get_stats()
        lines.append(f"**Session:** {stats['session_duration']} | Turns: {stats['total_turns']} | "
                     f"Accuracy: {stats['decision_accuracy']:.0%} | "
                     f"Corrections: {stats['self_corrections']}")
        lines.append("")

        # Awareness scores
        lines.append("### 🎯 Awareness Dimensions")
        for name, dim in sorted(self.dimensions.items(), key=lambda x: x[1].score):
            bar_len = 10
            filled = int(dim.score * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            trend_icon = {"improving": "📈", "declining": "📉", "stable": "➖"}.get(dim.trend, "➖")
            lines.append(
                f"{trend_icon} **{name.replace('_', ' ').title()}**\n"
                f"   [{bar}] {dim.score:.0%} | conf={dim.confidence:.0%}"
            )
        lines.append("")

        # Behavioral patterns
        lines.append("### 🔁 Behavioral Patterns")
        top_patterns = sorted(
            self._patterns.values(),
            key=lambda p: (p.frequency, p.success_rate),
            reverse=True
        )[:8]
        if not top_patterns:
            lines.append("_No patterns recorded yet._")
        else:
            for p in top_patterns:
                rate_icon = "✅" if p.success_rate >= 0.7 else "⚠️" if p.success_rate >= 0.4 else "❌"
                lines.append(
                    f"{rate_icon} **{p.pattern_id}** (×{p.frequency}, {p.success_rate:.0%} success)\n"
                    f"   {p.description[:80]}"
                )
        lines.append("")

        # Recent corrections
        lines.append("### 🔧 Recent Self-Corrections")
        recent_corr = self._corrections[-5:]
        if not recent_corr:
            lines.append("_No corrections yet._")
        else:
            for c in recent_corr:
                icon = "✅" if c["success"] else "❌"
                lines.append(f"{icon} **{c['what_was_wrong'][:60]}** → {c['what_was_corrected'][:60]}")
        lines.append("")

        # Anomalies / errors
        lines.append("### ⚠️ Recent Errors")
        recent_errs = self._recent_errors[-5:]
        if not recent_errs:
            lines.append("_No recent errors._")
        else:
            for e in recent_errs:
                lines.append(f"  • {e}")
        lines.append("")

        # Decision accuracy
        lines.append("### 📊 Decision Metrics")
        lines.append(
            f"  Total decisions: {stats['total_decisions']}\n"
            f"  Decision accuracy: {stats['decision_accuracy']:.0%}\n"
            f"  Avg confidence: {stats['avg_confidence']:.0%}\n"
            f"  Active patterns: {stats['active_patterns']}\n"
            f"  Awareness score: {stats['awareness_score']:.0%}"
        )

        return "\n".join(lines)

    def get_stats(self) -> AwarenessStats:
        """Get a stats snapshot."""
        total = len(self._decisions)
        good = sum(1 for d in self._decisions if d.success is True)
        conf_samples = self._confidence_samples[-50:] if self._confidence_samples else [0.5]

        session_duration = time.time() - self._session_start
        days = int(session_duration // 86400)
        hours = int((session_duration % 86400) // 3600)
        mins = int((session_duration % 3600) // 60)
        dur_str = f"{days}d {hours}h {mins}m" if days else f"{hours}h {mins}m"

        awareness_score = sum(d.score * d.confidence for d in self.dimensions.values()) / max(1, len(self.dimensions))

        metacog_depth = 0
        if self._decisions:
            metacog_depth = max(d.meta_level for d in self._decisions)

        return AwarenessStats(
            total_decisions=total,
            good_decisions=good,
            decision_accuracy=good / total if total else 0.5,
            avg_confidence=sum(conf_samples) / len(conf_samples) if conf_samples else 0.5,
            active_patterns=len(self._patterns),
            self_corrections=len(self._corrections),
            awareness_score=awareness_score,
            metacognitive_depth=metacog_depth,
        )

    # ── Meta-cognitive guidance ────────────────────────────────────────────

    def suggest_self_check(self, context: str) -> str | None:
        """
        Based on current awareness state, suggest a self-check question
        or guidance for the agent to consider before acting.
        """
        # Check for low-confidence dimensions
        low_dims = [(n, d) for n, d in self.dimensions.items() if d.score < 0.5]
        if low_dims:
            weakest = min(low_dims, key=lambda x: x[1].score)
            prompts = {
                "task_understanding": "⚠️ Low confidence in task understanding. Double-check the goal and constraints before proceeding.",
                "context_tracking": "⚠️ Low context tracking awareness. Verify you remember the conversation state and prior steps.",
                "planning": "⚠️ Low planning awareness. Consider breaking this task into smaller steps.",
                "self_correction": "⚠️ Low self-correction awareness. Be extra careful to verify your work.",
                "tool_usage": "⚠️ Low tool usage awareness. Confirm you have the right tool for this job.",
            }
            return prompts.get(weakest[0], f"⚠️ Uncertain about: {weakest[0]}. Consider reviewing before acting.")

        # Check for declining patterns
        recent_failures = [
            p for p in self._patterns.values()
            if p.last_seen > datetime.now(timezone.utc).isoformat()[:19] and p.success_rate < 0.4
        ]
        if len(recent_failures) >= 3:
            return f"⚠️ Pattern warning: {len(recent_failures)} low-success patterns detected. Consider reviewing approach."

        # Check for decision accuracy drop
        recent = self._decisions[-20:]
        if len(recent) >= 10:
            recent_accuracy = sum(1 for d in recent if d.success is True) / len(recent)
            if recent_accuracy < 0.5:
                return "⚠️ Recent decision accuracy is low. Consider slowing down and double-checking reasoning."

        return None  # No specific warning

    def should_escalate(self, context: str) -> tuple[bool, str]:
        """
        Determine whether this situation warrants escalation
        (asking for clarification, admitting uncertainty, etc.).
        """
        confidence = self.confidence_for_context(context)

        # Check for unknown patterns
        if not any(context.lower() in h.lower() for p in self._patterns.values() for h in p.context_hints):
            if confidence < 0.6:
                return True, "Low confidence with no matching patterns — consider asking for clarification."

        # Check for high error rate
        recent = self._recent_errors[-10:]
        if len(recent) >= 5:
            return True, "High recent error rate — consider asking user for guidance."

        # Check for low task understanding
        if self.dimensions["task_understanding"].score < 0.4:
            return True, "Low task understanding — ask user to clarify requirements."

        return False, ""

    # ── Real-time awareness hooks ─────────────────────────────────────────

    def on_tool_result(self, tool: str, success: bool, error: str = "") -> None:
        """Called after each tool execution for awareness tracking."""
        self.record_action_pattern(
            action_type="tool_execution",
            tool=tool,
            success=success,
            context=error[:100] if error else "",
        )
        if not success:
            self.update_dimension("tool_usage", -0.02, note=f"Tool {tool} failed: {error[:50]}")
        else:
            self.update_dimension("tool_usage", 0.01)

    def on_turn_result(self, success: bool, had_tools: bool, tool_count: int = 0) -> None:
        """Called after each agent turn for awareness tracking."""
        if success:
            self.update_dimension("reasoning_quality", 0.01)
            if had_tools:
                self.update_dimension("tool_usage", 0.005)
        else:
            self.update_dimension("reasoning_quality", -0.02)

        if tool_count > 5:
            self.update_dimension("planning", -0.01, note=f"High tool count: {tool_count}")
        elif tool_count <= 3 and success:
            self.update_dimension("planning", 0.01)

    def on_healing_pass(self, passed: bool, strategy: str) -> None:
        """Called after each healing pass."""
        if passed:
            self.record_self_correction(
                what_was_wrong=f"Initial failure (strategy: {strategy})",
                what_was_corrected="Auto-healed successfully",
                success=True,
            )
        else:
            self.record_self_correction(
                what_was_wrong=f"Healing failed (strategy: {strategy})",
                what_was_corrected="Exhausted all healing passes",
                success=False,
            )
            self.update_dimension("error_recovery", -0.03)

    def on_llm_response(self, has_tool_calls: bool, content_length: int) -> None:
        """Called after each LLM response."""
        if not has_tool_calls and content_length < 50:
            self.update_dimension("communication", -0.01, note="Empty or very short response")

    # ── Pattern query ─────────────────────────────────────────────────────

    def get_successful_patterns(self, min_freq: int = 2, min_rate: float = 0.7) -> list[BehaviorPattern]:
        """Return patterns that have been successful and frequent."""
        return [
            p for p in self._patterns.values()
            if p.frequency >= min_freq and p.success_rate >= min_rate
        ]

    def get_failing_patterns(self, min_freq: int = 2, max_rate: float = 0.3) -> list[BehaviorPattern]:
        """Return patterns that frequently fail — may need attention."""
        return [
            p for p in self._patterns.values()
            if p.frequency >= min_freq and p.success_rate <= max_rate
        ]

    # ── Reset / snapshot ───────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of awareness state."""
        return {
            "dimensions": {
                name: {"score": d.score, "confidence": d.confidence, "trend": d.trend}
                for name, d in self.dimensions.items()
            },
            "pattern_count": len(self._patterns),
            "decision_count": len(self._decisions),
            "correction_count": len(self._corrections),
            "total_turns": self._total_turns,
            "successful_turns": self._successful_turns,
            "overall_confidence": self.overall_confidence(),
            "awareness_score": self.get_stats().awareness_score,
        }

    def reset_session(self) -> None:
        """Reset session-level counters (keep patterns)."""
        self._total_turns = 0
        self._successful_turns = 0
        self._confidence_samples.clear()
        self._session_start = time.time()