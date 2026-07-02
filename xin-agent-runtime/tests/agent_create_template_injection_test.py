# -*- coding: utf-8 -*-
"""TDD Red tests for str.format template injection in ``AgentCreate``.

A malicious ``SubAgentTemplate.system_prompt_template`` containing
attribute-access placeholders such as ``{team_name.__class__}`` would
let an attacker leak Python internals via ``str.format``'s attribute
access. The fix switches the renderer to
:class:`string.Template` with ``safe_substitute``, which only honours
``$name`` placeholders and leaves ``{...}`` text untouched.

Run with::

    python -m pytest tests/agent_create_template_injection_test.py -v
"""
# pylint: disable=protected-access
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app._tools import AgentCreate, TeamCreate
from agentscope.app._types import SubAgentTemplate
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    RedisStorage,
    SessionConfig,
)
from agentscope.agent import ContextConfig, ReActConfig


def _make_storage(fr: fakeredis.aioredis.FakeRedis) -> RedisStorage:
    """Build a fakeredis-backed ``RedisStorage``."""

    class _S(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _S()


def _make_bus(fr: fakeredis.aioredis.FakeRedis) -> RedisMessageBus:
    """Build a fakeredis-backed ``RedisMessageBus``."""

    class _B(RedisMessageBus):
        async def __aenter__(self) -> "RedisMessageBus":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _B()


def _make_agent_record(
    user_id: str,
    name: str,
    source: str = "user",
) -> AgentRecord:
    """Build a minimal ``AgentRecord``."""
    return AgentRecord(
        user_id=user_id,
        source=source,
        data=AgentData(
            name=name,
            system_prompt=f"You are {name}.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


class TestAgentCreateTemplateInjection(IsolatedAsyncioTestCase):
    """Verify ``AgentCreate`` resists ``str.format`` template injection."""

    user_id = "u"

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.storage = await self._stack.enter_async_context(
            _make_storage(self.fr),
        )
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))
        self.leader_agent = _make_agent_record(self.user_id, "leader")
        await self.storage.upsert_agent(self.user_id, self.leader_agent)
        self.leader_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.leader_agent.id,
            config=SessionConfig(workspace_id="ws"),
        )
        await TeamCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
        )(name="alpha", description="team desc")

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    async def _spawn_with_template(
        self,
        system_prompt_template: str,
    ) -> tuple:
        """Run ``AgentCreate`` with a custom template and return the
        ``(chunk, worker_system_prompt)`` tuple."""
        template = SubAgentTemplate(
            type="custom",
            description="custom template",
            system_prompt_template=system_prompt_template,
        )
        tool = AgentCreate(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.leader_session.id,
            agent_id=self.leader_agent.id,
            sub_agent_templates={template.type: template},
        )
        chunk = await tool(
            name="worker",
            description="does work",
            prompt="work",
            subagent_type=template.type,
        )
        sess = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session.id,
        )
        team = await self.storage.get_team(self.user_id, sess.team_id)
        worker_agent_id = team.data.member_ids[-1]
        worker_agent = await self.storage.get_agent(
            self.user_id,
            worker_agent_id,
        )
        return chunk, worker_agent.data.system_prompt

    async def test_brace_class_access_does_not_leak(self) -> None:
        """A template containing ``{team_name.__class__}`` must NOT
        expand to leak Python internals via ``str.format`` attribute
        access. With the ``string.Template`` fix, the ``{...}`` text
        is preserved verbatim instead of being interpreted."""
        template = (
            "Hello {team_name.__class__.__init__.__globals__} "
            "and {__class__}"
        )
        chunk, system_prompt = await self._spawn_with_template(template)
        assert chunk.state.value == "running", chunk
        assert "{team_name.__class__" in system_prompt
        assert "{__class__}" in system_prompt
        assert "<class 'str'>" not in system_prompt
        assert "__builtins__" not in system_prompt

    async def test_dollar_placeholders_substitute(self) -> None:
        """After the fix, ``$member_name`` (the new syntax) is
        substituted with the worker's name."""
        template = (
            "Member $member_name of team $team_name led by $leader_name "
            "($team_description / $member_description)."
        )
        chunk, system_prompt = await self._spawn_with_template(template)
        assert chunk.state.value == "running", chunk
        assert (
            system_prompt == "Member worker of team alpha led by leader "
            "(team desc / does work)."
        )

    async def test_missing_placeholder_does_not_raise(self) -> None:
        """``safe_substitute`` leaves missing placeholders as literal
        text instead of raising ``KeyError`` (str.format's behaviour)."""
        template = "Hello $unknown_name and $member_name"
        chunk, system_prompt = await self._spawn_with_template(template)
        assert chunk.state.value == "running", chunk
        assert system_prompt == "Hello $unknown_name and worker"

    async def test_dollar_not_followed_by_identifier_is_safe(self) -> None:
        """A bare ``$`` (or ``$5``) is not a placeholder and must be
        preserved as-is by ``safe_substitute``."""
        template = "Price $5 - member $member_name"
        chunk, system_prompt = await self._spawn_with_template(template)
        assert chunk.state.value == "running", chunk
        assert system_prompt == "Price $5 - member worker"
