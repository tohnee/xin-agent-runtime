#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XRuntime 日志异常分析器
从日志文件中自动提取 RBAC 和 Knowledge 相关的异常记录。

使用方法:
    python xruntime_log_analyzer.py /var/log/xruntime/gateway.log
    python xruntime_log_analyzer.py /var/log/xruntime/ --format json
    python xruntime_log_analyzer.py /var/log/xruntime/ --alert --threshold 10
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Iterator


@dataclass
class RbacViolation:
    """RBAC 违规记录"""

    timestamp: str = ""
    session_id: str = ""
    role: str = ""
    tool_name: str = ""
    message: str = ""
    severity: str = "warning"

    def to_dict(self) -> dict:
        return {
            "type": "rbac_violation",
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "role": self.role,
            "tool_name": self.tool_name,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class KnowledgeError:
    """Knowledge 检索错误"""

    timestamp: str = ""
    tenant_id: str = ""
    error_type: str = ""
    message: str = ""
    query_preview: str = ""
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "type": "knowledge_error",
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "error_type": self.error_type,
            "message": self.message,
            "query_preview": self.query_preview,
            "severity": self.severity,
        }


@dataclass
class LogAnalysisResult:
    """日志分析结果"""

    rbac_violations: list[RbacViolation] = field(default_factory=list)
    knowledge_errors: list[KnowledgeError] = field(default_factory=list)
    total_lines_processed: int = 0
    file_analyzed: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "rbac_violation_count": len(self.rbac_violations),
            "knowledge_error_count": len(self.knowledge_errors),
            "total_lines_processed": self.total_lines_processed,
            "files_analyzed": len(self.file_analyzed),
        }


class XRuntimeLogParser:
    """XRuntime 日志解析器"""

    # RBAC 日志匹配模式
    RBAC_DENIED_PATTERN = re.compile(
        r"\[RBAC-DENIED\]\s+"
        r"session=(?P<session_id>[^\s,]+)"
        r"(?:,\s*role=(?P<role>[^\s,]+))?"
        r"(?:,\s*tool=(?P<tool>[^\s,]+))?"
        r".*?"
        r"(?P<message>Access DENIED.*?)?$",
    )

    RBAC_CHECK_PATTERN = re.compile(
        r"\[RBAC-CHECK\]\s+"
        r"session=(?P<session_id>[^\s,]+)"
        r"(?:,\s*role=(?P<role>[^\s,]+))?"
        r"(?:,\s*tool=(?P<tool>[^\s,]+))?",
    )

    # Knowledge 日志匹配模式
    KNOWLEDGE_ERROR_PATTERN = re.compile(
        r"\[KNOWLEDGE-ERROR\]\s+"
        r"tenant=(?P<tenant_id>[^\s,]+)"
        r"(?:,\s*retrieval\s+failed:\s*(?P<error>.+?))?$",
    )

    KNOWLEDGE_EMPTY_PATTERN = re.compile(
        r"\[KNOWLEDGE-EMPTY\]\s+" r"tenant=(?P<tenant_id>[^\s,]+)",
    )

    # 时间戳匹配
    TIMESTAMP_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
        r"|"
        r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})",
    )

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.result = LogAnalysisResult()

    def _extract_timestamp(self, line: str) -> str:
        """从日志行中提取时间戳"""
        match = self.TIMESTAMP_PATTERN.search(line)
        if match:
            return match.group(1) or match.group(2) or ""
        return ""

    def _parse_rbac_line(self, line: str) -> RbacViolation | None:
        """解析 RBAC 相关日志行"""
        denied_match = self.RBAC_DENIED_PATTERN.search(line)
        if denied_match:
            return RbacViolation(
                timestamp=self._extract_timestamp(line),
                session_id=denied_match.group("session_id") or "",
                role=denied_match.group("role") or "",
                tool_name=denied_match.group("tool") or "",
                message=denied_match.group("message") or "RBAC Access DENIED",
                severity="warning",
            )
        return None

    def _parse_knowledge_line(self, line: str) -> KnowledgeError | None:
        """解析 Knowledge 相关日志行"""
        error_match = self.KNOWLEDGE_ERROR_PATTERN.search(line)
        if error_match:
            return KnowledgeError(
                timestamp=self._extract_timestamp(line),
                tenant_id=error_match.group("tenant_id") or "",
                error_type="retrieval_failure",
                message=error_match.group("error")
                or "Unknown retrieval error",
                query_preview="",
                severity="error",
            )

        empty_match = self.KNOWLEDGE_EMPTY_PATTERN.search(line)
        if empty_match:
            return KnowledgeError(
                timestamp=self._extract_timestamp(line),
                tenant_id=empty_match.group("tenant_id") or "",
                error_type="no_results",
                message="No relevant knowledge found for query",
                query_preview="",
                severity="info",
            )
        return None

    def parse_line(self, line: str) -> None:
        """解析单行日志"""
        self.result.total_lines_processed += 1

        # 检查 RBAC 违规
        rbac_violation = self._parse_rbac_line(line)
        if rbac_violation:
            self.result.rbac_violations.append(rbac_violation)
            if self.verbose:
                print(
                    f"[RBAC VIOLATION] {rbac_violation.session_id} - "
                    f"Role: {rbac_violation.role}, Tool: {rbac_violation.tool_name}",
                )

        # 检查 Knowledge 错误
        knowledge_error = self._parse_knowledge_line(line)
        if knowledge_error:
            self.result.knowledge_errors.append(knowledge_error)
            if self.verbose:
                print(
                    f"[KNOWLEDGE ERROR] Tenant: {knowledge_error.tenant_id} - "
                    f"{knowledge_error.message}",
                )

    def parse_file(self, file_path: Path) -> None:
        """解析单个日志文件"""
        if self.verbose:
            print(f"Parsing: {file_path}")

        self.result.file_analyzed.append(str(file_path))

        # 处理 gzip 压缩文件
        opener = gzip.open if file_path.suffix == ".gz" else open
        mode = "rt" if file_path.suffix == ".gz" else "r"

        try:
            with opener(
                file_path, mode, encoding="utf-8", errors="ignore"
            ) as f:
                for line in f:
                    self.parse_line(line)
        except Exception as e:
            print(f"Error parsing {file_path}: {e}", file=sys.stderr)

    def parse_directory(self, dir_path: Path) -> None:
        """解析目录中的所有日志文件"""
        log_patterns = ["*.log", "*.log.gz", "*.txt", "*.txt.gz"]

        for pattern in log_patterns:
            for log_file in sorted(dir_path.rglob(pattern)):
                if log_file.is_file():
                    self.parse_file(log_file)

    def get_result(self) -> LogAnalysisResult:
        """获取分析结果"""
        return self.result


def print_text_report(result: LogAnalysisResult) -> None:
    """打印文本格式报告"""
    print("=" * 80)
    print("XRuntime 日志异常分析报告")
    print("=" * 80)
    print(f"\n分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"分析文件数: {result.summary()['files_analyzed']}")
    print(f"处理日志行数: {result.summary()['total_lines_processed']:,}")
    print()

    # RBAC 违规报告
    print("-" * 80)
    print(f"RBAC 权限违规: {len(result.rbac_violations)} 条")
    print("-" * 80)

    if result.rbac_violations:
        # 按角色聚合统计
        role_stats: dict[str, int] = {}
        tool_stats: dict[str, int] = {}

        for v in result.rbac_violations:
            role_stats[v.role] = role_stats.get(v.role, 0) + 1
            tool_stats[v.tool_name] = tool_stats.get(v.tool_name, 0) + 1

        print("\n按角色统计:")
        for role, count in sorted(role_stats.items(), key=lambda x: -x[1]):
            print(f"  {role or 'unknown':<20} {count:>4} 次")

        print("\n按工具统计 (前10):")
        for tool, count in sorted(tool_stats.items(), key=lambda x: -x[1])[
            :10
        ]:
            print(f"  {tool or 'unknown':<40} {count:>4} 次")

        print("\n最近的违规记录 (前10):")
        for v in result.rbac_violations[-10:]:
            print(f"\n  [{v.timestamp}]")
            print(f"    Session: {v.session_id}")
            print(f"    Role:    {v.role}")
            print(f"    Tool:    {v.tool_name}")
    else:
        print("\n  ✅ 未发现 RBAC 权限违规记录")

    print()

    # Knowledge 错误报告
    print("-" * 80)
    print(f"Knowledge 检索异常: {len(result.knowledge_errors)} 条")
    print("-" * 80)

    if result.knowledge_errors:
        # 按租户统计
        tenant_stats: dict[str, int] = {}
        type_stats: dict[str, int] = {}

        for e in result.knowledge_errors:
            tenant_stats[e.tenant_id] = tenant_stats.get(e.tenant_id, 0) + 1
            type_stats[e.error_type] = type_stats.get(e.error_type, 0) + 1

        print("\n按错误类型统计:")
        for err_type, count in sorted(type_stats.items(), key=lambda x: -x[1]):
            print(f"  {err_type:<30} {count:>4} 次")

        print("\n按租户统计:")
        for tenant, count in sorted(tenant_stats.items(), key=lambda x: -x[1])[
            :10
        ]:
            print(f"  {tenant or 'unknown':<20} {count:>4} 次")

        print("\n最近的错误记录 (前10):")
        for e in result.knowledge_errors[-10:]:
            print(f"\n  [{e.timestamp}] [{e.severity.upper()}]")
            print(f"    Tenant:  {e.tenant_id}")
            print(f"    Type:    {e.error_type}")
            print(f"    Message: {e.message}")
    else:
        print("\n  ✅ 未发现 Knowledge 检索异常")

    print()
    print("=" * 80)
    print("分析完成")
    print("=" * 80)


def print_json_report(result: LogAnalysisResult) -> None:
    """打印 JSON 格式报告"""
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": result.summary(),
        "rbac_violations": [v.to_dict() for v in result.rbac_violations],
        "knowledge_errors": [e.to_dict() for e in result.knowledge_errors],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def print_csv_report(result: LogAnalysisResult) -> None:
    """打印 CSV 格式报告"""
    print("type,timestamp,session_id/tenant_id,role/error_type,tool/message")

    for v in result.rbac_violations:
        print(f"rbac,{v.timestamp},{v.session_id},{v.role},{v.tool_name}")

    for e in result.knowledge_errors:
        print(
            f"knowledge,{e.timestamp},{e.tenant_id},{e.error_type},{e.message}"
        )


def check_alert_threshold(result: LogAnalysisResult, threshold: int) -> bool:
    """检查是否超过告警阈值"""
    has_alert = False

    if len(result.rbac_violations) >= threshold:
        print(
            f"⚠️  ALERT: RBAC 违规次数 ({len(result.rbac_violations)}) 超过阈值 ({threshold})"
        )
        has_alert = True

    error_count = sum(
        1 for e in result.knowledge_errors if e.severity == "error"
    )
    if error_count >= threshold:
        print(f"⚠️  ALERT: Knowledge 错误次数 ({error_count}) 超过阈值 ({threshold})")
        has_alert = True

    return has_alert


def main():
    parser = argparse.ArgumentParser(
        description="XRuntime 日志异常分析器 - 提取 RBAC 和 Knowledge 相关异常",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s /var/log/xruntime/gateway.log
  %(prog)s /var/log/xruntime/ --format json
  %(prog)s /var/log/xruntime/ --alert --threshold 10
  %(prog)s /var/log/xruntime/ --verbose
        """,
    )
    parser.add_argument(
        "path",
        help="日志文件路径或目录路径",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["text", "json", "csv"],
        default="text",
        help="输出格式 (默认: text)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="显示详细处理过程",
    )
    parser.add_argument(
        "--alert",
        "-a",
        action="store_true",
        help="启用告警模式，超过阈值时返回非零退出码",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=int,
        default=10,
        help="告警阈值 (默认: 10)",
    )

    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"错误: 路径不存在: {path}", file=sys.stderr)
        sys.exit(1)

    # 解析日志
    parser_obj = XRuntimeLogParser(verbose=args.verbose)

    if path.is_file():
        parser_obj.parse_file(path)
    else:
        parser_obj.parse_directory(path)

    result = parser_obj.get_result()

    # 输出报告
    if args.format == "json":
        print_json_report(result)
    elif args.format == "csv":
        print_csv_report(result)
    else:
        print_text_report(result)

    # 告警检查
    exit_code = 0
    if args.alert:
        has_alert = check_alert_threshold(result, args.threshold)
        exit_code = 2 if has_alert else 0

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
