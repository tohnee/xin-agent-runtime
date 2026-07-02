# -*- coding: utf-8 -*-
"""TDD Red tests for str.format template injection in ``Agent.summary``.

``Agent._compress_context_impl`` formats the user-supplied
``ContextConfig.summary_template`` with ``res.content`` (model output)
via ``str.format``. That allows attribute-access placeholders such as
``{task_overview.__class__.__init__.__globals__}`` to leak Python
internals whenever the model output contains a string field — even
though the model itself isn't actively malicious.

The fix switches the renderer to :class:`string.Template` with
``safe_substitute``, which only honours ``$name`` placeholders and
leaves ``{...}`` text untouched.

Run with::

    python -m pytest tests/agent_summary_template_injection_test.py -v
"""
# pylint: disable=protected-access
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, ContextConfig
from agentscope.message import AssistantMsg, UserMsg
from agentscope.model import StructuredResponse
from agentscope.state import AgentState
from agentscope.tool import Toolkit


class TestAgentSummaryTemplateInjection(IsolatedAsyncioTestCase):
    """Verify ``Agent._compress_context_impl`` resists ``str.format``
    template injection via the user-supplied ``summary_template``."""

    async def _compress_with(
        self,
        summary_template: str,
        structured_content: dict,
    ) -> str:
        """Drive ``Agent.compress_context`` with a custom
        ``summary_template`` and the given structured model output, then
        return the rendered ``state.summary`` string."""
        model = MockModel(context_size=100)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
                summary_template=summary_template,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )
        model.set_structured_response(
            StructuredResponse(content=structured_content),
        )
        await agent.compress_context()
        return agent.state.summary

    async def test_brace_class_access_does_not_leak(self) -> None:
        """A ``summary_template`` containing attribute-access braces
        such as ``{task_overview.__class__.__init__.__globals__}`` must
        NOT expand to leak Python internals via ``str.format``. With the
        ``string.Template`` fix, the ``{...}`` text is preserved
        verbatim."""
        summary = await self._compress_with(
            summary_template=(
                "Summary {task_overview.__class__.__init__.__globals__} "
                "and {__class__}"
            ),
            structured_content={
                "task_overview": "T",
                "current_state": "C",
                "important_discoveries": "I",
                "next_steps": "N",
                "context_to_preserve": "P",
            },
        )
        assert "{task_overview.__class__" in summary
        assert "{__class__}" in summary
        assert "<class 'str'>" not in summary
        assert "__builtins__" not in summary

    async def test_dollar_placeholders_substitute(self) -> None:
        """After the fix, ``$field`` (the new syntax) substitutes
        correctly with the structured model output."""
        summary = await self._compress_with(
            summary_template=(
                "T=$task_overview C=$current_state "
                "I=$important_discoveries N=$next_steps P=$context_to_preserve"
            ),
            structured_content={
                "task_overview": "T",
                "current_state": "C",
                "important_discoveries": "I",
                "next_steps": "N",
                "context_to_preserve": "P",
            },
        )
        assert summary == "T=T C=C I=I N=N P=P"
