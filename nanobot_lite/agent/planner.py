"""Task planning and decomposition — nanobot-lite planner module."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
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


# ─── Step status ────────────────────────────────────────────────────────────

class StepStatus(IntEnum):
    PENDING   = 0
    RUNNING   = 1
    DONE      = 2
    FAILED    = 3
    SKIPPED   = 4
    WAITING   = 5   # waiting on a dependency


# ─── Step dataclass ──────────────────────────────────────────────────────────

@dataclass
class PlanStep:
    """A single step in a plan."""
    id: str
    description: str
    tool: str | None = None          # recommended tool name
    args: dict[str, Any] | None = None
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    error: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    depends_on: list[str] = field(default_factory=list)  # step IDs
    alternative_tools: list[str] = field(default_factory=list)
    can_parallel_with: list[str] = field(default_factory=list)
    confidence: float = 0.8           # estimated success probability
    notes: str = ""

    def duration_ms(self) -> float:
        if self.started_at and self.finished_at:
            try:
                s = datetime.fromisoformat(self.started_at)
                e = datetime.fromisoformat(self.finished_at)
                return (e - s).total_seconds() * 1000
            except Exception:
                pass
        return 0.0


# ─── Plan dataclass ──────────────────────────────────────────────────────────

@dataclass
class Plan:
    """A multi-step task plan."""
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "active"            # active | completed | failed | abandoned
    total_retries: int = 0
    parent_plan_id: str | None = None  # for nested plans
    tags: list[str] = field(default_factory=list)

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.DONE)

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)

    @property
    def pending_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.PENDING)

    @property
    def progress_pct(self) -> float:
        if not self.steps:
            return 0.0
        return self.completed_steps / len(self.steps) * 100

    def mark_updated(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def summary(self) -> str:
        done = self.completed_steps
        fail = self.failed_steps
        pend = self.pending_steps
        total = len(self.steps)
        return (
            f"Plan({self.goal[:40]}...) — "
            f"{done}/{total} done, {fail} failed, {pend} pending, "
            f"{self.progress_pct:.0f}% complete"
        )


# ─── Execution tracker ──────────────────────────────────────────────────────

class ExecutionTracker:
    """
    Tracks plan execution, handles retries, backtracking,
    and parallel step coordination.
    """

    def __init__(self, max_total_retries: int = 10):
        self.max_total_retries = max_total_retries
        self._history: list[dict[str, Any]] = []

    def record_attempt(self, plan: Plan, step_id: str, success: bool, error: str = "") -> None:
        self._history.append({
            "plan_goal": plan.goal,
            "step_id": step_id,
            "success": success,
            "error": error,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        plan.total_retries += 0 if success else 1
        plan.mark_updated()

    def can_retry(self, plan: Plan) -> bool:
        return plan.total_retries < self.max_total_retries

    def suggest_alternative(self, failed_step: PlanStep) -> str | None:
        """Suggest an alternative tool or approach based on history."""
        if not self._history:
            return None
        # Find similar failed steps and what succeeded instead
        for entry in reversed(self._history):
            if not entry["success"] and entry["step_id"] != failed_step.id:
                # Could look up tool fallback maps here
                if failed_step.alternative_tools:
                    return failed_step.alternative_tools[0]
        return None


# ─── Planner ────────────────────────────────────────────────────────────────

class TaskPlanner:
    """
    LLM-powered task planner with decomposition, parallel step detection,
    dependency resolution, and execution tracking.
    """

    # Planning prompt template
    PLANNER_PROMPT = """You are a task planner. Given a user goal, decompose it into clear, executable steps.

Rules:
- Each step should be a single, well-defined action
- Use concrete tool names (run_code, read_file, bash, web_search, etc.)
- Mark steps that can run in parallel with [PARALLEL] tag
- Mark steps that depend on previous steps with [DEPENDS_ON: step_id]
- Estimate confidence (0.0-1.0) for each step
- Provide alternative tools if the primary might fail
- Keep step descriptions concise (< 60 words)

Output format (JSON):
{{
  "goal": "...",
  "steps": [
    {{
      "id": "step_1",
      "description": "...",
      "tool": "...",
      "args": {{}},
      "confidence": 0.9,
      "depends_on": [],
      "alternative_tools": [],
      "notes": ""
    }}
  ],
  "tags": []
}}
"""

    def __init__(self, llm_callable=None):
        """
        Args:
            llm_callable: async function(messages, tools) -> LLMResponse.
                          If None, planner returns a minimal single-step fallback.
        """
        self._llm = llm_callable
        self._plans: dict[str, Plan] = {}
        self._tracker = ExecutionTracker()
        self._step_counter = 0

    # ── Plan creation ────────────────────────────────────────────────────────

    async def create_plan(self, goal: str, context: list[dict[str, Any]] | None = None) -> Plan:
        """
        Create a plan for a given goal, optionally using LLM for decomposition.
        Falls back to a simple single-step plan if no LLM is available.
        """
        self._step_counter += 1
        plan_id = f"plan_{self._step_counter}_{int(time.time())}"

        if self._llm is None:
            # No LLM — simple single-step fallback
            plan = Plan(goal=goal)
            plan.steps = [
                PlanStep(
                    id=f"{plan_id}_1",
                    description=goal,
                    tool=None,
                    args={},
                    confidence=0.5,
                )
            ]
            self._plans[plan_id] = plan
            return plan

        # LLM-powered decomposition
        system_msg = {"role": "system", "content": self.PLANNER_PROMPT}
        user_msg = {"role": "user", "content": f"Goal: {goal}"}

        # Include context if available
        if context:
            ctx_str = "\n".join(
                f"[{m['role']}]: {m['content'][:300]}"
                for m in context[-5:] if isinstance(m, dict)
            )
            user_msg["content"] += f"\n\nRecent context:\n{ctx_str}"

        try:
            response = await self._llm(messages=[system_msg, user_msg], tools=None)
            plan = self._parse_plan_response(goal, response.content, plan_id)
        except Exception as e:
            logger.error(f"[planner] LLM planning failed: {e}, using fallback")
            plan = self._parse_plan_response(goal, "", plan_id)

        self._plans[plan_id] = plan
        logger.info(f"[planner] Created {plan_id}: {len(plan.steps)} steps for '{goal[:50]}'")
        return plan

    def _parse_plan_response(self, goal: str, raw: str, plan_id: str) -> Plan:
        """Parse LLM JSON output into a Plan."""
        plan = Plan(goal=goal)

        # Try to extract JSON from response
        json_str = raw.strip()
        # Handle markdown code blocks
        if "```json" in json_str:
            json_str = re.split(r"```json", json_str, maxsplit=1)[1]
            json_str = json_str.split("```")[0]
        elif "```" in json_str:
            json_str = re.split(r"```", json_str, maxsplit=1)[1]
            json_str = json_str.split("```")[0]

        # Find JSON object
        try:
            # Try direct parse first
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Try finding JSON in the text
            brace_start = json_str.find("{")
            brace_end = json_str.rfind("}")
            if brace_start != -1 and brace_end != -1:
                try:
                    data = json.loads(json_str[brace_start:brace_end + 1])
                except json.JSONDecodeError:
                    # Fallback: single step
                    data = None

        if data is None:
            # Fallback single step
            plan.steps = [PlanStep(id=f"{plan_id}_1", description=goal, confidence=0.5)]
            return plan

        plan.tags = data.get("tags", [])

        for step_data in data.get("steps", []):
            step = PlanStep(
                id=step_data.get("id", f"{plan_id}_{len(plan.steps)+1}"),
                description=step_data.get("description", ""),
                tool=step_data.get("tool"),
                args=step_data.get("args", {}),
                confidence=step_data.get("confidence", 0.7),
                depends_on=step_data.get("depends_on", []),
                alternative_tools=step_data.get("alternative_tools", []),
                notes=step_data.get("notes", ""),
            )
            plan.steps.append(step)

        if not plan.steps:
            plan.steps = [PlanStep(id=f"{plan_id}_1", description=goal, confidence=0.5)]

        return plan

    # ── Plan execution ───────────────────────────────────────────────────────

    async def execute_plan(
        self,
        plan: Plan,
        executor,       # AgentLoop-like object with tools.execute()
        bus,            # MessageBus for events
    ) -> tuple[bool, str]:
        """
        Execute a plan step by step, respecting dependencies.

        Returns (success, summary_message).
        """
        logger.info(f"[planner] Executing {plan.summary()}")

        # Resolve dependencies — mark steps as WAITING if deps not done
        self._resolve_dependencies(plan)

        # Mark all non-waiting pending steps as RUNNING
        for step in plan.steps:
            if step.status == StepStatus.PENDING and not step.depends_on:
                step.status = StepStatus.RUNNING
                step.started_at = datetime.now(timezone.utc).isoformat()

        for step in plan.steps:
            # Skip completed or skipped
            if step.status == StepStatus.DONE:
                continue
            if step.status == StepStatus.SKIPPED:
                continue

            # Wait for dependencies
            while step.status == StepStatus.WAITING:
                deps_done = all(
                    s.status == StepStatus.DONE
                    for s in plan.steps
                    if s.id in step.depends_on
                )
                if deps_done:
                    step.status = StepStatus.RUNNING
                    step.started_at = datetime.now(timezone.utc).isoformat()
                    break
                # Check if any dependency failed
                failed_dep = next(
                    (s for s in plan.steps if s.id in step.depends_on and s.status == StepStatus.FAILED),
                    None
                )
                if failed_dep:
                    step.status = StepStatus.SKIPPED
                    step.notes = f"Skipped due to failed dependency: {failed_dep.id}"
                    break

            if step.status == StepStatus.SKIPPED:
                continue

            step.status = StepStatus.RUNNING
            if not step.started_at:
                step.started_at = datetime.now(timezone.utc).isoformat()

            # Execute step
            success, result, error = await self._execute_step(step, executor, plan)

            step.finished_at = datetime.now(timezone.utc).isoformat()

            if success:
                step.status = StepStatus.DONE
                step.result = result
                logger.info(f"[planner] Step {step.id} completed: {step.description[:40]}")
                self._tracker.record_attempt(plan, step.id, success=True)
            else:
                if step.retry_count < step.max_retries and self._tracker.can_retry(plan):
                    step.retry_count += 1
                    step.status = StepStatus.PENDING
                    step.started_at = None
                    logger.info(f"[planner] Retrying step {step.id} (attempt {step.retry_count})")
                    self._tracker.record_attempt(plan, step.id, success=False, error=error)
                    continue
                else:
                    step.status = StepStatus.FAILED
                    step.error = error
                    logger.error(f"[planner] Step {step.id} FAILED: {error[:100]}")
                    self._tracker.record_attempt(plan, step.id, success=False, error=error)

            plan.mark_updated()

        # Determine overall outcome
        plan.status = "completed" if plan.failed_steps == 0 else (
            "failed" if all(s.status in (StepStatus.FAILED, StepStatus.SKIPPED) for s in plan.steps)
            else "partial"
        )

        summary = self._build_summary(plan)
        return plan.failed_steps == 0, summary

    async def _execute_step(
        self,
        step: PlanStep,
        executor,
        plan: Plan,
    ) -> tuple[bool, str, str]:
        """Execute a single plan step via the tool executor."""
        tool_name = step.tool
        args = step.args or {}

        if not tool_name:
            # No tool — assume reasoning step, just return success with description
            return True, step.description, ""

        # Try primary tool, then fallbacks
        tools_to_try = [tool_name] + step.alternative_tools

        for attempt_tool in tools_to_try:
            try:
                # Put tool call event on bus
                if hasattr(executor, "bus") and executor.bus:
                    from nanobot_lite.bus.events import ToolCallEvent, ToolResultEvent
                    user_id = getattr(executor.store, "user_id", "planner") if hasattr(executor, "store") else "planner"
                    chat_id = getattr(executor.store, "chat_id", "planner") if hasattr(executor, "store") else "planner"
                    await executor.bus.inbound.put(ToolCallEvent(
                        tool_name=attempt_tool,
                        arguments=args,
                        user_id=user_id,
                        chat_id=chat_id,
                    ))

                result = await executor.tools.execute(attempt_tool, args)

                if hasattr(executor, "bus") and executor.bus:
                    await executor.bus.inbound.put(ToolResultEvent(
                        tool_name=attempt_tool,
                        result=result.content,
                        success=result.success,
                    ))

                if result.success:
                    return True, result.content, ""
                else:
                    # Try next fallback
                    if attempt_tool != tools_to_try[-1]:
                        logger.info(f"[planner] Tool {attempt_tool} failed, trying fallback...")
                        continue
                    return False, result.content, result.error or "Tool execution failed"

            except Exception as e:
                if attempt_tool != tools_to_try[-1]:
                    logger.info(f"[planner] Exception in {attempt_tool}: {e}, trying fallback...")
                    continue
                return False, "", str(e)

        return False, "", "All tools exhausted"

    def _resolve_dependencies(self, plan: Plan) -> None:
        """Mark steps that depend on incomplete steps as WAITING."""
        step_map = {s.id: s for s in plan.steps}
        for step in plan.steps:
            if step.depends_on:
                unmet = [
                    dep_id for dep_id in step.depends_on
                    if dep_id in step_map and step_map[dep_id].status != StepStatus.DONE
                ]
                if unmet:
                    step.status = StepStatus.WAITING

    def _build_summary(self, plan: Plan) -> str:
        """Build a human-readable summary of plan execution."""
        lines = [f"**Plan: {plan.goal}**\n"]

        for step in plan.steps:
            icon = {
                StepStatus.DONE: "✅",
                StepStatus.FAILED: "❌",
                StepStatus.SKIPPED: "⏭️",
                StepStatus.WAITING: "⏳",
                StepStatus.PENDING: "⏸️",
                StepStatus.RUNNING: "🔄",
            }.get(step.status, "?")

            dur = f" ({step.duration_ms():.0f}ms)" if step.duration_ms() > 0 else ""
            retry_info = f" (retry {step.retry_count})" if step.retry_count else ""
            lines.append(f"{icon} {step.id}: {step.description[:50]}{dur}{retry_info}")

            if step.result and len(step.result) < 200:
                lines.append(f"   → {step.result[:150]}")
            elif step.error:
                lines.append(f"   → ❌ {step.error[:150]}")

        lines.append(f"\n📊 {plan.progress_pct:.0f}% complete | {plan.failed_steps} failed | {plan.total_retries} retries")
        return "\n".join(lines)

    # ── Plan management ──────────────────────────────────────────────────────

    def get_plan(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def list_plans(self) -> list[Plan]:
        return list(self._plans.values())

    def abort_plan(self, plan_id: str) -> bool:
        plan = self._plans.get(plan_id)
        if not plan:
            return False
        plan.status = "abandoned"
        for step in plan.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.WAITING):
                step.status = StepStatus.SKIPPED
                step.notes = "Plan aborted"
        plan.mark_updated()
        logger.info(f"[planner] Aborted plan {plan_id}")
        return True

    # ── LLM integration for planning ─────────────────────────────────────────

    async def refine_plan(
        self,
        plan: Plan,
        feedback: str,
        llm=None,
    ) -> Plan:
        """
        Use LLM to refine a plan based on execution feedback.
        Returns a new or updated plan.
        """
        if not llm:
            return plan

        refinement_prompt = f"""Current plan goal: {plan.goal}

Steps so far:
{self._build_summary(plan)}

Feedback / error: {feedback}

Suggest a refined plan. Output JSON with updated steps.
"""

        try:
            response = await llm(
                messages=[
                    {"role": "system", "content": self.PLANNER_PROMPT},
                    {"role": "user", "content": refinement_prompt},
                ],
                tools=None,
            )
            # Parse updated plan but keep successful steps
            updated = self._parse_plan_response(plan.goal, response.content, plan.steps[0].id.split("_")[0] + "_refined")

            # Preserve results from completed steps
            for old_step in plan.steps:
                if old_step.status == StepStatus.DONE:
                    for new_step in updated.steps:
                        if new_step.id == old_step.id or new_step.description == old_step.description:
                            new_step.status = StepStatus.DONE
                            new_step.result = old_step.result
                            new_step.started_at = old_step.started_at
                            new_step.finished_at = old_step.finished_at

            plan.steps = updated.steps
            plan.mark_updated()
        except Exception as e:
            logger.error(f"[planner] Plan refinement failed: {e}")

        return plan