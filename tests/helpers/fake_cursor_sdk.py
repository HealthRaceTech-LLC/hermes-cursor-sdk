"""Rich in-memory test double for cursor_sdk."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Any


@dataclass
class ModelParameterValue:
    value: Any


@dataclass
class ModelSelection:
    id: str
    params: dict[str, Any] | None = None


@dataclass
class LocalAgentOptions:
    cwd: str
    setting_sources: list[str] | None = None


@dataclass
class CloudRepository:
    id: str | None = None
    url: str | None = None
    ref: str = "main"
    starting_ref: str | None = None

    def __post_init__(self) -> None:
        if self.starting_ref:
            self.ref = self.starting_ref
        if self.id is None:
            self.id = self.url or "repo"
        if self.url is None:
            self.url = self.id


@dataclass
class CloudAgentOptions:
    repos: list[CloudRepository]
    auto_create_pr: bool = False
    skip_reviewer_request: bool = True
    env_names: list[str] | None = None


@dataclass
class AgentOptions:
    api_key: str
    model: Any
    local: LocalAgentOptions | None = None
    cloud: CloudAgentOptions | None = None


@dataclass
class FakeTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class FakeAssistantPayload:
    content: list[FakeTextBlock]


@dataclass
class FakeMessage:
    type: str
    message: FakeAssistantPayload


class FakeRun:
    def __init__(
        self, sdk: FakeCursorSDK, agent_id: str, prompt: str, *, status: str = "running"
    ) -> None:
        self.sdk = sdk
        self.agent_id = agent_id
        self.id = sdk.next_run_id()
        self.run_id = self.id
        self.prompt = prompt
        self.status = status
        self.cancelled = False
        self.usage = {
            "input_tokens": len(prompt.split()),
            "output_tokens": len(sdk.response_text.split()),
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }
        sdk.runs[self.id] = self

    def wait(self, timeout: int | None = None) -> FakeRun:
        self.sdk.calls.append({"method": "run.wait", "run_id": self.id, "timeout": timeout})
        if self.status not in {"error", "failed", "cancelled"}:
            self.status = "finished"
        return self

    def text(self) -> str:
        return self.sdk.response_text

    def messages(self) -> list[FakeMessage]:
        return [
            FakeMessage(
                type="assistant",
                message=FakeAssistantPayload(content=[FakeTextBlock(text=self.sdk.response_text)]),
            )
        ]

    def cancel(self) -> None:
        self.cancelled = True
        self.status = "cancelled"

    def supports(self, op: str) -> bool:
        return op in {"cancel", "conversation"}


class FakeAgent:
    def __init__(
        self,
        sdk: FakeCursorSDK,
        options: AgentOptions | dict[str, Any],
        *,
        agent_id: str | None = None,
    ) -> None:
        self.sdk = sdk
        self.options = options
        runtime = "cloud" if _value(options, "cloud") else "local"
        self.runtime = runtime
        self.agent_id = agent_id or sdk.next_agent_id(runtime)
        self.id = self.agent_id
        self.status = "ready"
        self.closed = False
        sdk.agents[self.agent_id] = self

    def __enter__(self) -> FakeAgent:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def send(self, prompt: str, **kwargs: Any) -> FakeRun:
        self.sdk.calls.append(
            {"method": "agent.send", "agent_id": self.agent_id, "prompt": prompt, "kwargs": kwargs}
        )
        run = FakeRun(self.sdk, self.agent_id, prompt)
        self.sdk.agent_runs.setdefault(self.agent_id, []).append(run.id)
        return run

    def close(self) -> None:
        self.closed = True

    def archive(self) -> None:
        self.status = "archived"

    def delete(self) -> None:
        self.status = "deleted"


class FakeAgentNamespace:
    def __init__(self, sdk: FakeCursorSDK) -> None:
        self.sdk = sdk

    def create(
        self, options: AgentOptions | dict[str, Any], client: Any | None = None
    ) -> FakeAgent:
        self.sdk.calls.append({"method": "Agent.create", "options": options, "client": client})
        return FakeAgent(self.sdk, options)

    def prompt(
        self, prompt: str, options: AgentOptions | dict[str, Any], client: Any | None = None
    ) -> FakeRun:
        self.sdk.calls.append(
            {"method": "Agent.prompt", "prompt": prompt, "options": options, "client": client}
        )
        agent = FakeAgent(self.sdk, options)
        return agent.send(prompt)

    def resume(
        self, agent_id: str, options: AgentOptions | dict[str, Any], client: Any | None = None
    ) -> FakeAgent:
        self.sdk.calls.append(
            {"method": "Agent.resume", "agent_id": agent_id, "options": options, "client": client}
        )
        return self.sdk.agents.get(agent_id) or FakeAgent(self.sdk, options, agent_id=agent_id)

    def get(self, agent_id: str, **_kwargs: Any) -> FakeAgent:
        return self.sdk.agents[agent_id]

    def get_run(self, run_id: str, **_kwargs: Any) -> FakeRun:
        return self.sdk.runs[run_id]


class FakeModels:
    def __init__(self, sdk: FakeCursorSDK) -> None:
        self.sdk = sdk

    def list(self, *_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.sdk.calls.append({"method": "Cursor.models.list", "kwargs": kwargs})
        return self.sdk.models


class FakeRepositories:
    def __init__(self, sdk: FakeCursorSDK) -> None:
        self.sdk = sdk

    def list(self, *_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.sdk.calls.append({"method": "Cursor.repositories.list", "kwargs": kwargs})
        return self.sdk.repositories


class FakeCursorNamespace:
    def __init__(self, sdk: FakeCursorSDK) -> None:
        self.models = FakeModels(sdk)
        self.repositories = FakeRepositories(sdk)


class FakeBridgeAgents:
    def __init__(self, sdk: FakeCursorSDK) -> None:
        self.sdk = sdk

    def get(self, agent_id: str) -> FakeAgent:
        return self.sdk.agents[agent_id]

    def get_run(self, run_id: str, **_kwargs: Any) -> FakeRun:
        return self.sdk.runs[run_id]

    def list(self, **_kwargs: Any) -> list[FakeAgent]:
        return list(self.sdk.agents.values())


class FakeBridgeClient:
    def __init__(self, sdk: FakeCursorSDK, workspace: str | None = None, **kwargs: Any) -> None:
        self.sdk = sdk
        self.workspace = workspace
        self.kwargs = kwargs
        self.agents = FakeBridgeAgents(sdk)
        self.closed = False

    def __enter__(self) -> FakeBridgeClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True


class FakeCursorClientNamespace:
    def __init__(self, sdk: FakeCursorSDK) -> None:
        self.sdk = sdk

    def launch_bridge(self, workspace: str | None = None, **kwargs: Any) -> FakeBridgeClient:
        self.sdk.calls.append(
            {"method": "CursorClient.launch_bridge", "workspace": workspace, "kwargs": kwargs}
        )
        return FakeBridgeClient(self.sdk, workspace, **kwargs)


class FakeCursorSDK:
    def __init__(self, response_text: str = "ok") -> None:
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []
        self.models = [
            {
                "id": "composer-2.5",
                "name": "composer-2.5",
                "parameters": {
                    "reasoning_effort": {"type": "string", "values": ["low", "medium", "high"]},
                    "max_tokens": {"type": "integer"},
                },
            }
        ]
        self.repositories = [
            {"id": "repo-1", "name": "repo-1", "url": "git@example.com:repo-1.git"}
        ]
        self.agents: dict[str, FakeAgent] = {}
        self.runs: dict[str, FakeRun] = {}
        self.agent_runs: dict[str, list[str]] = {}
        self._local_counter = count(1)
        self._cloud_counter = count(1)
        self._run_counter = count(1)
        self.Agent = FakeAgentNamespace(self)
        self.Cursor = FakeCursorNamespace(self)
        self.CursorClient = FakeCursorClientNamespace(self)
        self.AgentOptions = AgentOptions
        self.LocalAgentOptions = LocalAgentOptions
        self.CloudAgentOptions = CloudAgentOptions
        self.CloudRepository = CloudRepository
        self.ModelSelection = ModelSelection
        self.ModelParameterValue = ModelParameterValue

    def next_agent_id(self, runtime: str) -> str:
        if runtime == "cloud":
            return f"bc-{next(self._cloud_counter)}"
        return f"local-{next(self._local_counter)}"

    def next_run_id(self) -> str:
        return f"run-{next(self._run_counter)}"

    def run(self, prompt: str, **options: Any) -> Any:
        self.calls.append({"method": "run", "prompt": prompt, "options": options})
        return type("FakeCursorResponse", (), {"text": self.response_text, "metadata": {}})()


def _value(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
