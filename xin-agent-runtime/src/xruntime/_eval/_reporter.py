# -*- coding: utf-8 -*-
"""Reporters — render eval results to stdout / file.

Three reporters ship in the MVP:

* :class:`ConsoleReporter` — ANSI-colored stdout (REPL-friendly).
* :class:`JUnitReporter` — JUnit XML for CI (dorny/test-reporter).
* :class:`JsonReporter` — structured JSON with full trace.

All reporters implement :meth:`Reporter.report(results)`.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Any

from ._models import EvalResult, EvalStatus


# ANSI color codes
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


class Reporter:
    """Base reporter interface."""

    def report(self, results: list[EvalResult]) -> None:
        """Render results to the configured sink.

        Args:
            results (`list[EvalResult]`): The results to render.
        """
        raise NotImplementedError


class ConsoleReporter(Reporter):
    """Print results to stdout with ANSI colors.

    Format mirrors pytest's ``.`` / ``F`` / ``E`` progress bar followed
    by a per-eval breakdown.
    """

    def report(self, results: list[EvalResult]) -> None:
        # Progress bar
        chars = []
        for r in results:
            if r.status == EvalStatus.PASSED:
                chars.append(f"{_GREEN}.{_RESET}")
            elif r.status == EvalStatus.FAILED:
                chars.append(f"{_RED}F{_RESET}")
            elif r.status == EvalStatus.ERROR:
                chars.append(f"{_YELLOW}E{_RESET}")
            else:
                chars.append("s")
        print("".join(chars))
        print()

        # Detailed breakdown
        for r in results:
            color = _GREEN if r.status == EvalStatus.PASSED else _RED
            print(
                f"{color}{r.status.value.upper():6s}{_RESET} "
                f"{r.eval_id} ({r.duration_ms}ms)",
            )
            if r.status != EvalStatus.PASSED:
                for a in r.assertions:
                    if not a.passed:
                        print(
                            f"       {_RED}assert{_RESET} {a.name}: {a.message}",
                        )
            if r.trace.get("exception"):
                print(f"       {_YELLOW}error{_RESET} {r.trace['exception']}")

        # Summary
        passed = sum(1 for r in results if r.status == EvalStatus.PASSED)
        total = len(results)
        print()
        print(f"{_BOLD}{passed}/{total} passed{_RESET}")


class JsonReporter(Reporter):
    """Write structured JSON to a file.

    Each result is serialized as a dict with ``eval_id``, ``status``,
    ``description``, ``assertions``, ``trace``, ``duration_ms``.
    """

    def __init__(self, path: str = "eval-results.json") -> None:
        self.path = path

    def report(self, results: list[EvalResult]) -> None:
        data = [self._serialize(r) for r in results]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _serialize(self, r: EvalResult) -> dict[str, Any]:
        return {
            "eval_id": r.eval_id,
            "description": r.description,
            "status": r.status.value,
            "assertions": [
                {
                    "name": a.name,
                    "passed": a.passed,
                    "message": a.message,
                    "evidence": a.evidence,
                }
                for a in r.assertions
            ],
            "trace": r.trace,
            "duration_ms": r.duration_ms,
        }


class JUnitReporter(Reporter):
    """Write JUnit XML to a file.

    Each domain becomes a ``<testsuite>``; each eval becomes a
    ``<testcase>``.  Failed evals get a ``<failure>`` child with the
    assertion messages; errored evals get ``<error>``.
    """

    def __init__(self, path: str = "eval-results.xml") -> None:
        self.path = path

    def report(self, results: list[EvalResult]) -> None:
        # Group by domain
        suites: dict[str, list[EvalResult]] = {}
        for r in results:
            domain = (
                r.eval_id.split(".", 1)[0] if "." in r.eval_id else "default"
            )
            suites.setdefault(domain, []).append(r)

        root = ET.Element("testsuites")
        for domain, evals in suites.items():
            suite = ET.SubElement(
                root,
                "testsuite",
                {
                    "name": domain,
                    "tests": str(len(evals)),
                    "failures": str(
                        sum(1 for e in evals if e.status == EvalStatus.FAILED),
                    ),
                    "errors": str(
                        sum(1 for e in evals if e.status == EvalStatus.ERROR),
                    ),
                },
            )
            for r in evals:
                name = (
                    r.eval_id.split(".", 1)[-1]
                    if "." in r.eval_id
                    else r.eval_id
                )
                tc = ET.SubElement(
                    suite,
                    "testcase",
                    {
                        "name": name,
                        "classname": domain,
                        "time": f"{r.duration_ms / 1000:.3f}",
                    },
                )
                if r.status == EvalStatus.FAILED:
                    fails = [a for a in r.assertions if not a.passed]
                    msg = "; ".join(f"{a.name}: {a.message}" for a in fails)
                    ET.SubElement(tc, "failure", {"message": msg}).text = msg
                elif r.status == EvalStatus.ERROR:
                    exc = r.trace.get("exception", "unknown error")
                    ET.SubElement(
                        tc, "error", {"message": str(exc)}
                    ).text = str(exc)

        tree = ET.ElementTree(root)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "wb") as f:
            tree.write(f, encoding="utf-8", xml_declaration=True)
