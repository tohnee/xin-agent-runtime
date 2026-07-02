# -*- coding: utf-8 -*-
"""CLI entrypoint: ``python -m xruntime.eval run``.

Usage::

    python -m xruntime.eval run [--tags offline] [--evals-dir tests/evals]
    python -m xruntime.eval list [--tags offline]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from ._collector import EvalCollector
from ._runner import EvalRunner


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="python -m xruntime.eval",
        description="XRuntime Evals framework — run agent behavior evals.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run collected evals.")
    run_p.add_argument(
        "--evals-dir",
        default="tests/evals",
        help="Directory to scan for eval specs (default: tests/evals).",
    )
    run_p.add_argument(
        "--tags",
        nargs="+",
        default=["offline"],
        help="Only run evals with these tags (default: offline).",
    )
    run_p.add_argument(
        "--target",
        default=None,
        help="Eval target: 'in-process' or a URL. "
        "Defaults to $XRUNTIME_EVAL_TARGET or 'in-process'.",
    )

    list_p = sub.add_parser("list", help="List collected evals.")
    list_p.add_argument(
        "--evals-dir",
        default="tests/evals",
        help="Directory to scan for eval specs.",
    )
    list_p.add_argument(
        "--tags",
        nargs="+",
        default=["offline"],
        help="Filter by tags.",
    )

    args = parser.parse_args()

    if args.command == "list":
        specs = EvalCollector(args.evals_dir).collect(tags=args.tags)
        for s in specs:
            print(f"{s.eval_id:40s}  [{', '.join(s.tags)}]  {s.description}")
        return 0

    if args.command == "run":
        runner = EvalRunner(target=args.target, tags=args.tags)
        return asyncio.run(runner.run(evals_dir=args.evals_dir))

    return 1


if __name__ == "__main__":
    sys.exit(main())
