# -*- coding: utf-8 -*-
"""EvalCollector — scan a directory for ``@define_eval`` specs.

The collector imports every ``.py`` file in ``evals_dir`` (non-recursive
in MVP — subdirectories are Phase 2).  Importing the module triggers
the ``@define_eval`` decorator, which appends to the module-level
``_REGISTRY`` in :mod:`._define`.  The collector then reads the
registry and filters by tags.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Iterable

from ._define import _clear_registry, _get_registry
from ._models import EvalSpec


class EvalCollector:
    """Scan a directory for eval specs.

    Args:
        evals_dir (`str`): Directory containing ``test_*.py`` files.
    """

    def __init__(self, evals_dir: str) -> None:
        self.evals_dir = evals_dir

    def collect(
        self,
        tags: list[str] | None = None,
    ) -> list[EvalSpec]:
        """Collect eval specs, optionally filtering by tags.

        Args:
            tags (`list[str] | None`):
                If given, only specs whose ``tags`` intersect this
                list are returned.  ``None`` returns all specs.

        Returns:
            `list[EvalSpec]`: The collected specs.
        """
        _clear_registry()
        self._import_modules()
        specs = _get_registry()
        if tags is None:
            return specs
        tag_set = set(tags)
        return [s for s in specs if tag_set & set(s.tags)]

    def _import_modules(self) -> None:
        """Import every ``test_*.py`` in ``evals_dir``."""
        if not os.path.isdir(self.evals_dir):
            return
        for fname in sorted(os.listdir(self.evals_dir)):
            if not fname.endswith(".py"):
                continue
            if fname.startswith("_"):
                continue
            path = os.path.join(self.evals_dir, fname)
            mod_name = f"_xruntime_eval_{fname[:-3]}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
