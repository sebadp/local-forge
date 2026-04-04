"""Agent session data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class AgentStatus(StrEnum):
    RUNNING = "running"
    WAITING_USER = "waiting_user"  # HITL: esperando aprobación del usuario
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    """A single step in the agent's plan."""

    id: int
    description: str
    worker_type: str = "general"  # reader | analyzer | coder | reporter | general
    tools: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | in_progress | done | failed
    result: str | None = None
    depends_on: list[int] = field(default_factory=list)


@dataclass
class AgentPlan:
    """Structured plan created by the planner agent."""

    objective: str
    context_summary: str = ""
    tasks: list[TaskStep] = field(default_factory=list)
    current_task_idx: int = 0
    replans: int = 0
    max_replans: int = 3

    def next_task(self) -> TaskStep | None:
        """Return the next pending task whose dependencies are satisfied.

        Also marks tasks as failed if any of their dependencies failed
        (cascade failure — no point running a task whose input is missing).
        """
        done_ids = {t.id for t in self.tasks if t.status == "done"}
        failed_ids = {t.id for t in self.tasks if t.status == "failed"}

        # First pass: cascade-fail tasks blocked by failed dependencies
        changed = True
        while changed:
            changed = False
            for task in self.tasks:
                if task.status != "pending":
                    continue
                blocked_by = [d for d in task.depends_on if d in failed_ids]
                if blocked_by:
                    task.status = "failed"
                    task.result = f"Blocked: dependency #{blocked_by[0]} failed"
                    failed_ids.add(task.id)
                    changed = True

        # Second pass: find runnable task
        for task in self.tasks:
            if task.status != "pending":
                continue
            if all(dep_id in done_ids for dep_id in task.depends_on):
                return task
        return None

    def ready_tasks(self) -> list[TaskStep]:
        """Return all pending tasks whose dependencies are met (can run in parallel)."""
        done_ids = {t.id for t in self.tasks if t.status == "done"}
        failed_ids = {t.id for t in self.tasks if t.status == "failed"}

        # Propagate failures first
        changed = True
        while changed:
            changed = False
            for task in self.tasks:
                if task.status != "pending":
                    continue
                blocked_by = [d for d in task.depends_on if d in failed_ids]
                if blocked_by:
                    task.status = "failed"
                    task.result = f"Blocked: dependency #{blocked_by[0]} failed"
                    failed_ids.add(task.id)
                    changed = True

        ready = []
        for task in self.tasks:
            if task.status != "pending":
                continue
            if all(dep_id in done_ids for dep_id in task.depends_on):
                ready.append(task)
        return ready

    def all_done(self) -> bool:
        return all(t.status in ("done", "failed") for t in self.tasks)

    def has_failures(self) -> bool:
        """Return True if any task failed."""
        return any(t.status == "failed" for t in self.tasks)

    def success_count(self) -> int:
        """Return the number of successfully completed tasks."""
        return sum(1 for t in self.tasks if t.status == "done")

    def to_markdown(self) -> str:
        """Render the plan as a markdown checklist for task plan injection."""
        lines = [f"Objective: {self.objective}"]
        if self.context_summary:
            lines.append(f"Context: {self.context_summary}")
        lines.append("")
        for t in self.tasks:
            mark = "x" if t.status == "done" else ("!" if t.status == "failed" else " ")
            deps = f" (after #{','.join(str(d) for d in t.depends_on)})" if t.depends_on else ""
            lines.append(f"- [{mark}] #{t.id} [{t.worker_type}] {t.description}{deps}")
        return "\n".join(lines)


@dataclass
class AgentSession:
    session_id: str
    phone_number: str
    objective: str  # El pedido original del usuario
    status: AgentStatus = AgentStatus.RUNNING
    iteration: int = 0
    max_iterations: int = 15
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    context_messages: list = field(default_factory=list)
    task_plan: str | None = None  # task.md content (actualizado por el agente durante la sesión)
    plan: AgentPlan | None = None  # Structured plan (planner-orchestrator)
    scratchpad: str = ""  # Persistent notes between reactive rounds (injected as system message)
