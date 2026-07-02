# -*- coding: utf-8 -*-
"""TDD tests for WorkflowAuditLogger (P3-D Task 3).

Covers audit logging for workflow execution — records every step
execution with tenant isolation and compliance-relevant fields:

* ``action``: ``workflow.step.executed`` / ``workflow.started`` /
  ``workflow.completed`` / ``workflow.failed``
* ``workflow_id``, ``step_id``, ``agent``, ``status``,
  ``duration_ms``, ``tenant_id``, ``timestamp``
* In-memory sink (default) + file sink (JSONL)
* Tenant isolation: queries only return entries for the specified
  tenant
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from xruntime._runtime._workflow._sdk import (
    FunctionExecutor,
    run_workflow,
)


# ── 1. WorkflowAuditLogger construction ───────────────────────


class TestWorkflowAuditLoggerConstruction:
    """WorkflowAuditLogger — construction."""

    def test_memory_sink_default(self) -> None:
        """默认 in-memory sink."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        assert logger is not None
        assert logger.get_entries() == []

    def test_file_sink_construction(self) -> None:
        """file sink 构造."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            logger = WorkflowAuditLogger(sink="file", file_path=str(path))
            assert logger is not None


# ── 2. Audit entry recording ──────────────────────────────────


class TestWorkflowAuditEntryRecording:
    """WorkflowAuditLogger — record_step / record_workflow_event."""

    def test_record_step_execution(self) -> None:
        """record_step 记录 step 执行审计条目."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_step(
            workflow_id="wf-1",
            step_id="s1",
            agent="coder",
            status="COMPLETED",
            duration_ms=123.4,
            tenant_id="tenant-1",
        )
        entries = logger.get_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e["action"] == "workflow.step.executed"
        assert e["workflow_id"] == "wf-1"
        assert e["step_id"] == "s1"
        assert e["agent"] == "coder"
        assert e["status"] == "COMPLETED"
        assert e["duration_ms"] == 123.4
        assert e["tenant_id"] == "tenant-1"
        assert "timestamp" in e

    def test_record_workflow_started(self) -> None:
        """record_workflow_event 记录 workflow.started."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_workflow_event(
            action="workflow.started",
            workflow_id="wf-1",
            tenant_id="tenant-1",
        )
        entries = logger.get_entries()
        assert len(entries) == 1
        assert entries[0]["action"] == "workflow.started"
        assert entries[0]["workflow_id"] == "wf-1"

    def test_record_workflow_completed(self) -> None:
        """record_workflow_event 记录 workflow.completed."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_workflow_event(
            action="workflow.completed",
            workflow_id="wf-1",
            tenant_id="tenant-1",
            status="COMPLETED",
        )
        entries = logger.get_entries()
        assert entries[0]["action"] == "workflow.completed"

    def test_record_workflow_failed(self) -> None:
        """record_workflow_event 记录 workflow.failed."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_workflow_event(
            action="workflow.failed",
            workflow_id="wf-1",
            tenant_id="tenant-1",
            status="FAILED",
        )
        entries = logger.get_entries()
        assert entries[0]["action"] == "workflow.failed"


# ── 3. Tenant isolation ───────────────────────────────────────


class TestWorkflowAuditTenantIsolation:
    """WorkflowAuditLogger — tenant isolation."""

    def test_get_entries_by_tenant(self) -> None:
        """get_entries(tenant_id=) 只返回该 tenant 的条目."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_step(
            "wf-1",
            "s1",
            "a",
            "COMPLETED",
            100.0,
            "tenant-A",
        )
        logger.record_step(
            "wf-2",
            "s2",
            "a",
            "COMPLETED",
            200.0,
            "tenant-B",
        )
        logger.record_step(
            "wf-3",
            "s3",
            "a",
            "FAILED",
            50.0,
            "tenant-A",
        )

        tenant_a = logger.get_entries(tenant_id="tenant-A")
        tenant_b = logger.get_entries(tenant_id="tenant-B")
        assert len(tenant_a) == 2
        assert len(tenant_b) == 1
        assert all(e["tenant_id"] == "tenant-A" for e in tenant_a)

    def test_get_entries_by_workflow(self) -> None:
        """get_entries(workflow_id=) 按 workflow 过滤."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_step("wf-1", "s1", "a", "COMPLETED", 100.0, "t1")
        logger.record_step("wf-1", "s2", "a", "COMPLETED", 200.0, "t1")
        logger.record_step("wf-2", "s3", "a", "COMPLETED", 300.0, "t1")

        wf1 = logger.get_entries(workflow_id="wf-1")
        assert len(wf1) == 2
        assert all(e["workflow_id"] == "wf-1" for e in wf1)

    def test_get_entries_combined_filter(self) -> None:
        """get_entries(tenant + workflow) 组合过滤."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_step("wf-1", "s1", "a", "COMPLETED", 100.0, "tA")
        logger.record_step("wf-1", "s2", "a", "COMPLETED", 200.0, "tB")
        logger.record_step("wf-2", "s3", "a", "COMPLETED", 300.0, "tA")

        result = logger.get_entries(tenant_id="tA", workflow_id="wf-1")
        assert len(result) == 1
        assert result[0]["step_id"] == "s1"


# ── 4. File sink ──────────────────────────────────────────────


class TestWorkflowAuditFileSink:
    """WorkflowAuditLogger — file sink (JSONL)."""

    def test_file_sink_writes_jsonl(self) -> None:
        """file sink 写入 JSONL 格式."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            logger = WorkflowAuditLogger(
                sink="file",
                file_path=str(path),
            )
            logger.record_step(
                "wf-1",
                "s1",
                "a",
                "COMPLETED",
                100.0,
                "t1",
            )
            logger.record_step(
                "wf-1",
                "s2",
                "a",
                "FAILED",
                50.0,
                "t1",
            )

            # file 应该有 2 行 JSONL
            content = path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            assert len(lines) == 2
            e0 = json.loads(lines[0])
            assert e0["workflow_id"] == "wf-1"
            assert e0["step_id"] == "s1"
            e1 = json.loads(lines[1])
            assert e1["step_id"] == "s2"

    def test_file_sink_appends(self) -> None:
        """file sink 追加模式(不覆盖)."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            logger1 = WorkflowAuditLogger(
                sink="file",
                file_path=str(path),
            )
            logger1.record_step(
                "wf-1",
                "s1",
                "a",
                "COMPLETED",
                100.0,
                "t1",
            )

            logger2 = WorkflowAuditLogger(
                sink="file",
                file_path=str(path),
            )
            logger2.record_step(
                "wf-1",
                "s2",
                "a",
                "COMPLETED",
                200.0,
                "t1",
            )

            content = path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            assert len(lines) == 2  # 2 个条目


# ── 5. Integration with run_workflow ─────────────────────────


class TestWorkflowAuditIntegration:
    """WorkflowAuditLogger — integration with run_workflow."""

    @pytest.mark.asyncio
    async def test_workflow_run_audits_each_step(self) -> None:
        """run_workflow with audit= 参数记录每个 step."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        audit = WorkflowAuditLogger()
        wf = Workflow(
            id="wf-audit-1",
            name="Audit 1",
            steps=[
                WorkflowStep(id="s1", name="S1", agent="coder", prompt="p"),
                WorkflowStep(
                    id="s2",
                    name="S2",
                    agent="reviewer",
                    prompt="p",
                    depends_on=["s1"],
                ),
            ],
        )
        executor = FunctionExecutor(lambda s, c: f"out-{s.id}")
        result = await run_workflow(wf, executor, audit=audit)

        assert result.status == WorkflowStatus.COMPLETED
        entries = audit.get_entries()
        # 2 个 step 条目 + 可能的 workflow started/completed
        step_entries = [
            e for e in entries if e["action"] == "workflow.step.executed"
        ]
        assert len(step_entries) == 2
        assert step_entries[0]["step_id"] == "s1"
        assert step_entries[1]["step_id"] == "s2"

    @pytest.mark.asyncio
    async def test_audit_without_logger_works(self) -> None:
        """不传 audit 时 workflow 仍正常执行."""
        wf = Workflow(
            id="wf-no-audit",
            name="No Audit",
            steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
        )
        executor = FunctionExecutor(lambda s, c: "ok")
        result = await run_workflow(wf, executor, audit=None)
        assert result.status == WorkflowStatus.COMPLETED


# ── 6. Compliance checks ─────────────────────────────────────


class TestWorkflowAuditCompliance:
    """WorkflowAuditLogger — compliance field validation."""

    def test_entry_has_all_required_fields(self) -> None:
        """审计条目包含所有合规必需字段."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_step(
            "wf-1",
            "s1",
            "coder",
            "COMPLETED",
            duration_ms=123.4,
            tenant_id="tenant-1",
        )
        entries = logger.get_entries()
        e = entries[0]

        # 合规必需字段
        required_fields = [
            "timestamp",
            "tenant_id",
            "workflow_id",
            "step_id",
            "agent",
            "status",
            "duration_ms",
            "action",
        ]
        for field in required_fields:
            assert field in e, f"Missing required field: {field}"

    def test_no_api_key_in_audit_entries(self) -> None:
        """审计条目不含 api_key(安全合规)."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger()
        logger.record_step(
            "wf-1",
            "s1",
            "coder",
            "COMPLETED",
            duration_ms=123.4,
            tenant_id="tenant-1",
        )
        import json

        text = json.dumps(logger.get_entries())
        assert "api_key" not in text
        assert "secret" not in text.lower()

    def test_file_sink_without_path_raises(self) -> None:
        """file sink 不传 file_path 抛 ValueError."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        with pytest.raises(ValueError, match="file_path"):
            WorkflowAuditLogger(sink="file")

    def test_max_entries_drops_oldest(self) -> None:
        """超过 max_entries 时丢弃最旧条目."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger(max_entries=3)
        for i in range(5):
            logger.record_step(
                f"wf-{i}",
                "s1",
                "a",
                "COMPLETED",
                100.0,
                "t1",
            )
        entries = logger.get_entries()
        assert len(entries) == 3  # 只保留最新 3 个
        # 最旧的 wf-0 和 wf-1 被丢弃
        ids = [e["workflow_id"] for e in entries]
        assert "wf-0" not in ids
        assert "wf-1" not in ids
        assert "wf-2" in ids
        assert "wf-4" in ids

    def test_file_sink_get_entries_returns_empty(self) -> None:
        """file sink 的 get_entries() 返回空(只写不读)."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            logger = WorkflowAuditLogger(
                sink="file",
                file_path=str(path),
            )
            logger.record_step(
                "wf-1",
                "s1",
                "a",
                "COMPLETED",
                100.0,
                "t1",
            )
            # file sink 不支持 get_entries
            assert logger.get_entries() == []

    def test_file_sink_write_error_logged(self) -> None:
        """file sink 写入失败时不崩溃(仅 log)."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        # 指向一个不存在的目录(写入会失败)
        logger = WorkflowAuditLogger(
            sink="file",
            file_path="/nonexistent/path/audit.jsonl",
        )
        # 不应抛异常
        logger.record_step(
            "wf-1",
            "s1",
            "a",
            "COMPLETED",
            100.0,
            "t1",
        )

    def test_unknown_sink_silently_drops(self) -> None:
        """未知 sink 静默丢弃条目."""
        from xruntime._runtime._workflow._audit import (
            WorkflowAuditLogger,
        )

        logger = WorkflowAuditLogger(sink="unknown")
        logger.record_step(
            "wf-1",
            "s1",
            "a",
            "COMPLETED",
            100.0,
            "t1",
        )
        # unknown sink → get_entries 返回空
        assert logger.get_entries() == []
