from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from safefix.action_parser import ActionParseError, ActionParser
from safefix.config import SafeFixSettings
from safefix.context import ContextBuilder
from safefix.domain import (
    Action,
    ApplyPatchAction,
    BudgetState,
    DecisionOutcome,
    FinishAction,
    FeedbackCategory,
    RunSnapshot,
    RunStatus,
    RunValidationAction,
    StopDecision,
    Task,
    TaskMode,
    ToolResult,
    action_digest,
)
from safefix.feedback import FeedbackEngine
from safefix.llm.base import LLMClient
from safefix.run_store import RunNotFound, RunStore


class AgentLoop:
    def __init__(
        self,
        *,
        llm: LLMClient,
        context: ContextBuilder,
        action_parser: ActionParser,
        policy: Any,
        approvals: Any,
        tools: Any,
        feedback: FeedbackEngine,
        run_store: RunStore,
        audit: Any,
        settings: SafeFixSettings,
        approval_ttl_seconds: int = 900,
    ) -> None:
        self.llm = llm
        self.context = context
        self.action_parser = action_parser
        self.policy = policy
        self.approvals = approvals
        self.tools = tools
        self.feedback = feedback
        self.run_store = run_store
        self.audit = audit
        self.settings = settings
        self._approval_ttl_seconds = approval_ttl_seconds
        self._pending: dict[str, tuple[str, Action]] = {}
        self._approval_capabilities: dict[str, str] = {}

    def take_approval_capability(self, approval_id: str) -> str | None:
        """Return a pending approval capability once, for a trusted UI boundary."""
        return self._approval_capabilities.pop(approval_id, None)

    async def start(
        self,
        task: Task | None = None,
        *,
        project: str | None = None,
        description: str | None = None,
        workspace_root: str | None = None,
    ) -> RunSnapshot:
        now = datetime.now(UTC)
        if task is None:
            if project is None or description is None:
                raise TypeError("start requires a Task or project and description")
            task = Task(
                id=str(uuid4()),
                project_id=project,
                workspace_root=workspace_root or project,
                description=description,
                mode=TaskMode.LOCAL,
                created_at=now,
            )
        budget_settings = self.settings.budget
        snapshot = RunSnapshot(
            run_id=str(uuid4()),
            task_id=task.id,
            project_id=task.project_id,
            workspace_root=task.workspace_root,
            description=task.description,
            status=RunStatus.CREATED,
            repair_round=0,
            step_count=0,
            budget=BudgetState(
                max_steps=budget_settings.total_steps,
                remaining_steps=budget_settings.total_steps,
                max_repair_rounds=budget_settings.repair_rounds,
                remaining_repairs=budget_settings.repair_rounds,
                deadline_at=now + timedelta(seconds=budget_settings.wall_time_seconds),
            ),
            version=0,
            created_at=now,
            updated_at=now,
        )
        self.run_store.create(snapshot)
        snapshot = self.run_store.transition(
            snapshot.run_id, RunStatus.RUNNING, expected_version=snapshot.version
        )
        return await self._run(snapshot)

    async def resume_approved(
        self, approval_id: str, plaintext_token: str
    ) -> RunSnapshot:
        run_id, action = self._pending[approval_id]
        self.approvals.approve(approval_id, plaintext_token, action)
        snapshot = self._required_snapshot(run_id)
        snapshot = self._save(
            snapshot.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "pending_approval_id": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        del self._pending[approval_id]
        snapshot = await self._dispatch(snapshot, action)
        return await self._run(snapshot)

    async def resume_rejected(
        self, approval_id: str, plaintext_token: str
    ) -> RunSnapshot:
        run_id, action = self._pending[approval_id]
        self.approvals.reject(approval_id, plaintext_token)
        snapshot = self._required_snapshot(run_id)
        result = ToolResult.failure(
            action.id, "POLICY_DENIED", "approval request was rejected"
        )
        snapshot = snapshot.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "pending_approval_id": None,
                "updated_at": datetime.now(UTC),
            }
        )
        del self._pending[approval_id]
        snapshot = self._record_feedback(snapshot, (result,))
        return await self._run(snapshot)

    async def cancel(self, run_id: str) -> RunSnapshot:
        snapshot = self._required_snapshot(run_id)
        if snapshot.status in {
            RunStatus.SUCCESS,
            RunStatus.BLOCKED,
            RunStatus.NO_PROGRESS,
            RunStatus.BUDGET_EXCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return snapshot
        if snapshot.pending_approval_id is not None:
            self.approvals.cancel(snapshot.pending_approval_id)
            self._pending.pop(snapshot.pending_approval_id, None)
            self._approval_capabilities.pop(snapshot.pending_approval_id, None)
        return self._save(
            snapshot.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "pending_approval_id": None,
                    "stop_reason": "cancelled by user",
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def _run(self, snapshot: RunSnapshot) -> RunSnapshot:
        model_settings = {
            "endpoint": str(self.settings.llm.endpoint),
            "model": self.settings.llm.model,
        }
        while snapshot.status is RunStatus.RUNNING:
            stop_history = snapshot.feedback_history
            if (
                stop_history
                and stop_history[-1].category
                is FeedbackCategory.VALIDATION_SUCCESS
            ):
                stop_history = ()
            stop = self.feedback.should_stop(
                stop_history,
                snapshot.budget,
                snapshot.action_digests,
            )
            if stop is not None:
                return self._stop(snapshot, stop)
            try:
                response = await self.llm.complete(
                    self.context.build(snapshot), model_settings
                )
                action = self.action_parser.parse(response.text)
            except ActionParseError as exc:
                snapshot = self._record_parse_failure(snapshot, exc.feedback)
                continue
            except Exception:
                return self._stop(
                    snapshot, StopDecision(code=RunStatus.FAILED, reason="model call failed")
                )

            if (
                isinstance(action, ApplyPatchAction)
                and snapshot.budget.remaining_repairs == 0
            ):
                return self._stop(
                    snapshot,
                    StopDecision(
                        code=RunStatus.BUDGET_EXCEEDED,
                        reason="repair budget exhausted",
                    ),
                )
            snapshot = self._consume_action_budget(snapshot, action)
            try:
                self.audit.append(
                    snapshot.run_id, "ACTION", action.model_dump(mode="json")
                )
                decision = self.policy.decide(action)
                self.audit.append(
                    snapshot.run_id,
                    "POLICY_DECISION",
                    decision.model_dump(mode="json"),
                )
            except Exception:
                return self._stop(
                    snapshot,
                    StopDecision(code=RunStatus.FAILED, reason="governance unavailable"),
                )

            if decision.outcome is DecisionOutcome.DENY:
                result = ToolResult.failure(
                    action.id, "POLICY_DENIED", decision.explanation
                )
                snapshot = self._record_feedback(snapshot, (result,))
                continue
            if decision.outcome is DecisionOutcome.REQUIRE_APPROVAL:
                try:
                    challenge = self.approvals.request(
                        snapshot.run_id,
                        action,
                        decision.risk_level,
                        decision.rule_ids,
                        self._approval_ttl_seconds,
                    )
                except Exception:
                    return self._stop(
                        snapshot,
                        StopDecision(
                            code=RunStatus.FAILED, reason="approval service unavailable"
                        ),
                    )
                self._pending[challenge.id] = (snapshot.run_id, action)
                self._approval_capabilities[challenge.id] = challenge.token
                return self._save(
                    snapshot.model_copy(
                        update={
                            "status": RunStatus.AWAITING_APPROVAL,
                            "pending_approval_id": challenge.id,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
            snapshot = await self._dispatch(snapshot, action)
        return snapshot

    async def _dispatch(self, snapshot: RunSnapshot, action: Action) -> RunSnapshot:
        if isinstance(action, FinishAction):
            results = await self._run_validators(action.id)
            snapshot = self._record_feedback(snapshot, results)
            if (
                snapshot.status is RunStatus.RUNNING
                and results
                and all(result.success for result in results)
            ):
                return self._stop(
                    snapshot,
                    StopDecision(
                        code=RunStatus.SUCCESS,
                        reason="validation succeeded",
                    ),
                )
            return snapshot
        try:
            result = await self.tools.dispatch(action)
        except Exception:
            result = ToolResult.failure(
                action.id, "TOOL_ERROR", "tool execution failed"
            )
        changed_files = tuple(dict.fromkeys(snapshot.changed_files + result.changed_files))
        snapshot = snapshot.model_copy(
            update={
                "latest_tool_result": result,
                "changed_files": changed_files,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            self.audit.append(
                snapshot.run_id,
                "TOOL_RESULT",
                result.model_dump(mode="json"),
            )
        except Exception:
            return self._stop(
                snapshot,
                StopDecision(code=RunStatus.FAILED, reason="audit unavailable"),
            )
        if not result.success or isinstance(action, RunValidationAction):
            return self._record_feedback(snapshot, (result,), audit_results=False)
        if isinstance(action, ApplyPatchAction):
            return self._record_feedback(
                snapshot, await self._run_validators(action.id)
            )
        return self._save(snapshot)

    async def _run_validators(self, action_id: str) -> tuple[ToolResult, ...]:
        results: list[ToolResult] = []
        for validator in self.settings.validators:
            action = RunValidationAction(
                id=f"{action_id}:{validator.id}",
                reason="run configured validator",
                validator_id=validator.id,
            )
            try:
                results.append(await self.tools.dispatch(action))
            except Exception:
                results.append(
                    ToolResult.failure(
                        action.id, "TOOL_ERROR", "validator execution failed"
                    )
                )
        return tuple(results)

    def _consume_action_budget(
        self, snapshot: RunSnapshot, action: Action
    ) -> RunSnapshot:
        is_repair = isinstance(action, ApplyPatchAction)
        budget = snapshot.budget.model_copy(
            update={
                "remaining_steps": max(0, snapshot.budget.remaining_steps - 1),
                "remaining_repairs": max(
                    0, snapshot.budget.remaining_repairs - (1 if is_repair else 0)
                ),
            }
        )
        return snapshot.model_copy(
            update={
                "step_count": snapshot.step_count + 1,
                "repair_round": snapshot.repair_round + (1 if is_repair else 0),
                "budget": budget,
                "action_digests": snapshot.action_digests + (action_digest(action),),
                "updated_at": datetime.now(UTC),
            }
        )

    def _record_parse_failure(
        self, snapshot: RunSnapshot, message: str
    ) -> RunSnapshot:
        budget = snapshot.budget.model_copy(
            update={"remaining_steps": max(0, snapshot.budget.remaining_steps - 1)}
        )
        result = ToolResult.failure("model", "MODEL_OUTPUT_INVALID", message)
        updated = snapshot.model_copy(
            update={
                "step_count": snapshot.step_count + 1,
                "budget": budget,
                "updated_at": datetime.now(UTC),
            }
        )
        return self._record_feedback(updated, (result,), audit_results=False)

    def _record_feedback(
        self,
        snapshot: RunSnapshot,
        results: tuple[ToolResult, ...],
        *,
        audit_results: bool = True,
    ) -> RunSnapshot:
        feedback = self.feedback.from_results(
            results,
            snapshot.changed_files,
            snapshot.budget.remaining_steps,
            snapshot.budget.remaining_repairs,
        )
        try:
            if audit_results:
                for result in results:
                    self.audit.append(
                        snapshot.run_id,
                        "TOOL_RESULT",
                        result.model_dump(mode="json"),
                    )
            self.audit.append(
                snapshot.run_id,
                "FEEDBACK",
                feedback.model_dump(mode="json"),
            )
        except Exception:
            return self._stop(
                snapshot,
                StopDecision(code=RunStatus.FAILED, reason="audit unavailable"),
            )
        return self._save(
            snapshot.model_copy(
                update={
                    "latest_tool_result": results[-1],
                    "feedback_history": snapshot.feedback_history + (feedback,),
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    def _stop(self, snapshot: RunSnapshot, decision: StopDecision) -> RunSnapshot:
        return self._save(
            snapshot.model_copy(
                update={
                    "status": decision.code,
                    "stop_reason": decision.reason,
                    "pending_approval_id": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    def _save(self, snapshot: RunSnapshot) -> RunSnapshot:
        return self.run_store.save_snapshot(
            snapshot, expected_version=snapshot.version
        )

    def _required_snapshot(self, run_id: str) -> RunSnapshot:
        snapshot = self.run_store.get(run_id)
        if snapshot is None:
            raise RunNotFound("run does not exist")
        return snapshot
