"""Hermes-facing Cursor SDK client."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from hermes_cursor_sdk.config import Settings, load_settings, require_api_key
from hermes_cursor_sdk.errors import (
    BusyError,
    CursorSDKUnavailableError,
    InvalidArgsError,
    InvalidRepositoryError,
    ResourceNotFoundError,
    RunFailedError,
    UnsupportedError,
    map_exception,
)
from hermes_cursor_sdk.models import (
    CursorPrompt,
    CursorResult,
    normalize_model,
    normalize_repository,
    resolve_model_selection,
)
from hermes_cursor_sdk.results import ResultDict, error_result, extract_assistant_text, ok_result
from hermes_cursor_sdk.store import StateStore

TERMINAL_STATUSES = {
    "finished",
    "completed",
    "succeeded",
    "error",
    "failed",
    "cancelled",
    "canceled",
}
BUSY_STATUSES = {"queued", "pending", "running", "in_progress"}


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            value = getattr(obj, name)
            return value() if callable(value) and name.startswith("get_") else value
    return default


def _id(obj: Any, *names: str) -> str | None:
    value = _value(obj, *names)
    return str(value) if value is not None else None


def _call_list(resource: Any, api_key: str) -> Any:
    try:
        return resource.list(api_key=api_key)
    except TypeError:
        try:
            return resource.list({"api_key": api_key})
        except TypeError:
            return resource.list()


def _items(value: Any, *names: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    for name in (*names, "items", "data"):
        nested = _value(value, name)
        if nested is not None:
            return list(nested)
    return [value]


class CursorClient:  # pragma: no cover - legacy compatibility adapter
    """Compatibility client used by older plugin/provider entry points."""

    def __init__(self, config: Settings | None = None, sdk: Any | None = None) -> None:
        self.settings = config or load_settings()
        self._sdk = sdk

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            try:
                self._sdk = importlib.import_module("cursor_sdk")
            except ImportError as exc:  # pragma: no cover - optional runtime dependency
                raise CursorSDKUnavailableError("cursor-sdk is not importable") from exc
        return self._sdk

    def run(self, prompt: str | CursorPrompt, **options: Any) -> CursorResult:
        request = prompt if isinstance(prompt, CursorPrompt) else CursorPrompt(prompt=str(prompt))
        model = options.pop("model", request.model or self.settings.default_model)
        runner = getattr(self.sdk, "run", None)
        if callable(runner):
            response = runner(request.prompt, model=model, **options)
            return _legacy_result(response)

        cwd = request.workspace or self.settings.bridge_cwd or Path.cwd()
        response = CursorSDKClient(self.settings, sdk=self.sdk).run_local(
            prompt=request.prompt,
            cwd=cwd,
            model=model,
            params=options,
        )
        return _legacy_result(response)


def _legacy_result(value: Any) -> CursorResult:  # pragma: no cover - legacy CursorClient helper
    if isinstance(value, CursorResult):
        return value
    if isinstance(value, Mapping):
        text = value.get("result_text") or value.get("text") or value.get("content") or ""
        status = "ok" if value.get("ok", True) else "error"
        metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
        return CursorResult(text=str(text), status=status, raw=value, metadata=metadata)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    return CursorResult(text=str(text) if text is not None else str(value or ""), raw=value)


class CursorSDKClient:
    def __init__(
        self,
        settings: Settings | None = None,
        store: StateStore | None = None,
        sdk: Any | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.store = store or StateStore(self.settings.store_dir)
        self._sdk = sdk

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            try:
                self._sdk = importlib.import_module("cursor_sdk")
            except ImportError as exc:  # pragma: no cover - optional runtime dependency
                raise CursorSDKUnavailableError("cursor-sdk is not importable") from exc
        return self._sdk

    def list_models(self) -> list[dict[str, Any]]:
        api_key = require_api_key(self.settings)
        cursor = self.sdk.Cursor
        return [
            normalize_model(item, bridge_context_length=self.settings.bridge_context_length)
            for item in _items(_call_list(cursor.models, api_key), "models")
        ]

    def list_repositories(self) -> list[dict[str, Any]]:
        api_key = require_api_key(self.settings)
        cursor = self.sdk.Cursor
        return [
            normalize_repository(item)
            for item in _items(_call_list(cursor.repositories, api_key), "repositories")
        ]

    def run_local(
        self,
        *,
        prompt: str,
        cwd: str | Path,
        model: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> ResultDict:
        try:
            api_key = require_api_key(self.settings)
            cwd_path = self._validate_cwd(cwd)
            model_selection = self._resolve_model(model, params)
            with self._launch_bridge(cwd_path, api_key) as bridge:
                options = self._agent_options(
                    api_key=api_key, model=model_selection, runtime="local", cwd=cwd_path
                )
                run = self._agent_prompt(prompt, options, bridge)
                terminal = self._wait(run)
            return self._result_from_run(terminal, runtime="local", model=model_selection)
        except Exception as exc:
            return error_result(map_exception(exc), runtime="local", model=model)

    def start_cloud(
        self,
        *,
        prompt: str,
        repos: list[Any],
        model: Any = None,
        params: Mapping[str, Any] | None = None,
        auto_create_pr: bool = False,
        skip_reviewer_request: bool = True,
        mode: str = "agent",
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        wait: bool = False,
        env_names: list[str] | None = None,
    ) -> ResultDict:
        try:
            if idempotency_key:
                existing = self.store.get_idempotency(f"create:{idempotency_key}")
                payload = existing.get("payload") if existing else None
                if isinstance(payload, dict) and payload.get("ok") is True:
                    return cast(ResultDict, payload)
            api_key = require_api_key(self.settings)
            self._validate_env_names(env_names)
            cloud_repos = self._validate_cloud_repos(repos)
            model_selection = self._resolve_model(model, params)
            options = self._agent_options(
                api_key=api_key,
                model=model_selection,
                runtime="cloud",
                repos=cloud_repos,
                auto_create_pr=auto_create_pr or self.settings.auto_create_pr,
                skip_reviewer_request=skip_reviewer_request,
                env_names=env_names,
            )
            agent = self._agent_create(options)
            try:
                agent_id = self._agent_id(agent)
                if not agent_id:
                    raise RunFailedError("Cursor SDK did not return an agent id")
                self.store.upsert_agent(
                    agent_id,
                    runtime="cloud",
                    repos=cloud_repos,
                    model=model_selection,
                    auto_create_pr=auto_create_pr or self.settings.auto_create_pr,
                )
                run = self._agent_send(agent, prompt, mode=mode, metadata=metadata)
                run_id = self._run_id(run)
                if run_id:
                    self.store.upsert_run(
                        run_id, agent_id=agent_id, status=self._status(run) or "running"
                    )
                terminal = self._wait(run) if wait else run
                result = self._result_from_run(
                    terminal, runtime="cloud", model=model_selection, agent_id=agent_id
                )
                if idempotency_key and result.get("ok") is True:
                    self.store.put_idempotency(
                        f"create:{idempotency_key}",
                        agent_id=agent_id,
                        run_id=run_id,
                        payload=result,
                    )
                    self.store.put_idempotency(
                        f"send:{idempotency_key}", agent_id=agent_id, run_id=run_id, payload=result
                    )
                return result
            finally:
                self._close(agent)
        except Exception as exc:
            return error_result(map_exception(exc), runtime="cloud", model=model)

    def session_ensure_local(
        self,
        *,
        cwd: str | Path,
        session_key: str,
        model: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> str:
        existing = self.store.get_session(session_key)
        if existing:
            return str(existing["agent_id"])
        api_key = require_api_key(self.settings)
        cwd_path = self._validate_cwd(cwd)
        model_selection = self._resolve_model(model, params)
        with self._launch_bridge(cwd_path, api_key) as bridge:
            options = self._agent_options(
                api_key=api_key, model=model_selection, runtime="local", cwd=cwd_path
            )
            agent = self._agent_create(options, bridge)
            try:
                agent_id = self._agent_id(agent)
                if not agent_id:
                    raise RunFailedError("Cursor SDK did not return an agent id")
                self.store.upsert_agent(
                    agent_id, runtime="local", cwd=cwd_path, model=model_selection
                )
                self.store.set_session(session_key, agent_id=agent_id, cwd=cwd_path)
                return agent_id
            finally:
                self._close(agent)

    def session_send(
        self,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
        prompt: str,
        cwd: str | Path | None = None,
        model: Any = None,
        params: Mapping[str, Any] | None = None,
        wait: bool = True,
        force: bool = False,
        close: bool = False,
    ) -> ResultDict:
        resolved_agent_id = agent_id
        try:
            if not resolved_agent_id and session_key:
                session = self.store.get_session(session_key)
                if session:
                    resolved_agent_id = str(session["agent_id"])
                elif cwd is not None:
                    resolved_agent_id = self.session_ensure_local(
                        cwd=cwd, session_key=session_key, model=model, params=params
                    )
            if not resolved_agent_id:
                raise InvalidArgsError("agent_id or session_key is required")
            result = self._resume_and_send(
                agent_id=resolved_agent_id,
                prompt=prompt,
                cwd=cwd,
                force=force,
                model=model,
                params=params,
                wait=wait,
            )
            if close and session_key and result.get("ok") is True:
                self.store.delete_session(session_key)
            return result
        except Exception as exc:
            return error_result(map_exception(exc), agent_id=resolved_agent_id)

    def status(self, *, agent_id: str, run_id: str | None = None) -> ResultDict:
        try:
            runtime = self._runtime(agent_id)
            api_key = require_api_key(self.settings)
            with self._control_client(agent_id, api_key) as client:
                if run_id:
                    run = self._get_run(client, agent_id, run_id)
                    return self._result_from_run(run, runtime=runtime, agent_id=agent_id)
                agent = self._get_agent(client, agent_id)
                return ok_result(
                    agent_id=agent_id,
                    runtime=runtime,
                    status=self._status(agent),
                    metadata={"agent": self._public(agent)},
                )
        except Exception as exc:
            return error_result(map_exception(exc), agent_id=agent_id)

    def resume_and_send(
        self,
        *,
        agent_id: str,
        prompt: str,
        cwd: str | Path | None = None,
        force: bool = False,
        model: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> ResultDict:
        try:
            return self._resume_and_send(
                agent_id=agent_id,
                prompt=prompt,
                cwd=cwd,
                force=force,
                model=model,
                params=params,
                wait=True,
            )
        except Exception as exc:
            return error_result(map_exception(exc), agent_id=agent_id)

    def cancel(self, *, agent_id: str, run_id: str | None = None) -> ResultDict:
        try:
            runtime = self._runtime(agent_id)
            api_key = require_api_key(self.settings)
            run_id = run_id or self._latest_run_id(agent_id)
            if not run_id:
                raise ResourceNotFoundError("No run found for agent")
            with self._control_client(agent_id, api_key) as client:
                run = self._get_run(client, agent_id, run_id)
                cancel = getattr(run, "cancel", None)
                if not callable(cancel):
                    raise UnsupportedError("Run cancellation is not supported")
                cancel()
                self.store.upsert_run(run_id, agent_id=agent_id, status="cancelled")
                return ok_result(
                    agent_id=agent_id, run_id=run_id, runtime=runtime, status="cancelled"
                )
        except Exception as exc:
            return error_result(map_exception(exc), agent_id=agent_id, run_id=run_id)

    def manage_agent(
        self,
        *,
        action: str,
        agent_id: str | None = None,
        runtime: str | None = None,
        confirm_agent_id: str | None = None,
    ) -> ResultDict:
        try:
            api_key = require_api_key(self.settings)
            action = action.lower()
            if action == "list":
                return ok_result(
                    runtime=runtime,
                    metadata={"agents": self._list_agents(api_key, runtime=runtime)},
                )
            if not agent_id:
                raise InvalidArgsError("agent_id is required")
            if action == "get":
                return self.status(agent_id=agent_id)
            if action in {"archive", "delete"}:
                if confirm_agent_id != agent_id:
                    raise InvalidArgsError("confirm_agent_id must match agent_id")
                with self._control_client(agent_id, api_key) as client:
                    agent = self._get_agent(client, agent_id)
                    handler = getattr(agent, action, None)
                    if not callable(handler):
                        raise UnsupportedError(f"Agent {action} is not supported")
                    handler()
                return ok_result(agent_id=agent_id, runtime=self._runtime(agent_id), status=action)
            if action == "cancel":
                return self.cancel(agent_id=agent_id)
            raise UnsupportedError(f"Unsupported agent action: {action}")
        except Exception as exc:
            return error_result(map_exception(exc), agent_id=agent_id, runtime=runtime)

    def _resume_and_send(
        self,
        *,
        agent_id: str,
        prompt: str,
        cwd: str | Path | None,
        force: bool,
        model: Any,
        params: Mapping[str, Any] | None,
        wait: bool,
    ) -> ResultDict:
        stored = self.store.get_agent(agent_id) or {}
        runtime = str(stored.get("runtime") or self._runtime(agent_id))
        if not force and self._is_busy(agent_id):
            raise BusyError("Agent has an active run")
        api_key = require_api_key(self.settings)
        cwd_path = self._validate_cwd(cwd or stored.get("cwd")) if runtime == "local" else None
        model_selection = (
            self._resolve_model(model, params) if model or params else stored.get("model")
        )
        with (
            self._launch_bridge(cwd_path, api_key)
            if runtime == "local"
            else self._null_context(None) as bridge
        ):
            options = self._agent_options(
                api_key=api_key, model=model_selection, runtime=runtime, cwd=cwd_path
            )
            agent = self._agent_resume(agent_id, options, bridge)
            try:
                run = self._agent_send(agent, prompt)
                run_id = self._run_id(run)
                if run_id:
                    self.store.upsert_run(
                        run_id, agent_id=agent_id, status=self._status(run) or "running"
                    )
                terminal = self._wait(run) if wait else run
                return self._result_from_run(
                    terminal, runtime=runtime, model=model_selection, agent_id=agent_id
                )
            finally:
                self._close(agent)

    def _resolve_model(self, model: Any, params: Mapping[str, Any] | None) -> Any:
        merged_params = {**self.settings.provider_model_params, **dict(params or {})}
        return resolve_model_selection(
            model, merged_params, self.list_models(), self.settings.default_model
        )

    def _validate_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is None:
            raise InvalidArgsError("cwd is required for local Cursor runs")
        path = Path(cwd).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise InvalidArgsError("cwd must be an existing directory")
        home = Path.home().resolve()
        # Deny sensitive roots only. Do not deny /var or /tmp wholesale — macOS
        # TemporaryDirectory and many CI workspaces live under /var/folders.
        denied_exact = {
            home / ".ssh",
            home / ".hermes",
            Path("/etc").resolve(),
            Path("/private/etc").resolve(),
            Path("/root").resolve(),
        }
        if path in denied_exact or any(
            path == root or root in path.parents for root in denied_exact
        ):
            raise InvalidArgsError("cwd is not allowed")
        if self.settings.allowed_local_roots and not any(
            path == root or root in path.parents for root in self.settings.allowed_local_roots
        ):
            raise InvalidArgsError("cwd is outside allowed_local_roots")
        return path

    def _validate_env_names(self, env_names: list[str] | None) -> None:
        if not env_names:
            return
        allowed = set(self.settings.allowed_cloud_env_names)
        unknown = sorted(set(env_names) - allowed)
        if unknown:
            raise InvalidArgsError(f"Cloud env name is not allowed: {unknown[0]}")

    def _validate_cloud_repos(self, repos: list[Any]) -> list[dict[str, Any]]:
        if not repos:
            raise InvalidRepositoryError("At least one cloud repository is required")
        catalog = self.list_repositories()
        allowed = {
            value
            for repo in catalog
            for value in (repo.get("id"), repo.get("name"), repo.get("url"))
            if value
        }
        normalized: list[dict[str, Any]] = []
        for repo in repos:
            if isinstance(repo, str):
                repo_id = repo
                ref = self.settings.default_cloud_ref
                entry = {"id": repo_id, "ref": ref}
            else:
                repo_id = str(_value(repo, "id", "name", "url", "repository"))
                ref = str(
                    _value(
                        repo,
                        "starting_ref",
                        "ref",
                        "branch",
                        default=self.settings.default_cloud_ref,
                    )
                )
                entry = dict(repo) if isinstance(repo, dict) else {"id": repo_id}
                entry.setdefault("id", repo_id)
                entry.setdefault("url", _value(repo, "url", default=repo_id))
                entry["starting_ref"] = ref
                entry["ref"] = ref
            if repo_id not in allowed:
                raise InvalidRepositoryError("Repository is not available to Cursor")
            normalized.append(entry)
        return normalized

    def _agent_options(
        self,
        *,
        api_key: str,
        model: Any,
        runtime: str,
        cwd: Path | None = None,
        repos: Any = None,
        **extra: Any,
    ) -> Any:
        payload: dict[str, Any] = {"api_key": api_key, "model": model}
        if runtime == "local":
            local_payload: dict[str, Any] = {"cwd": str(cwd)}
            if self.settings.local_setting_sources is not None:
                local_payload["setting_sources"] = self.settings.local_setting_sources
            payload["local"] = self._construct("LocalAgentOptions", local_payload)
        elif runtime == "cloud":
            cloud_payload = {
                "repos": [self._cloud_repository(repo) for repo in repos or []],
                "auto_create_pr": extra.get("auto_create_pr", False),
                "skip_reviewer_request": extra.get("skip_reviewer_request", True),
            }
            if extra.get("env_names"):
                cloud_payload["env_names"] = extra["env_names"]
            payload["cloud"] = self._construct("CloudAgentOptions", cloud_payload)
        return self._construct("AgentOptions", payload)

    def _cloud_repository(self, repo: Mapping[str, Any]) -> Any:
        repo_id = repo.get("id") or repo.get("name") or repo.get("url")
        ref = repo.get("starting_ref") or repo.get("ref") or self.settings.default_cloud_ref
        payload = {
            "id": repo_id,
            "url": repo.get("url") or repo_id,
            "ref": ref,
            "starting_ref": ref,
        }
        if repo.get("pr_url"):
            payload["pr_url"] = repo["pr_url"]
        return self._construct("CloudRepository", payload)

    def _construct(self, class_name: str, payload: dict[str, Any]) -> Any:
        cls = getattr(self.sdk, class_name, None)
        if cls is None:
            return payload
        try:
            return cls(**payload)
        except TypeError:
            signature = inspect.signature(cls)
            filtered = {key: value for key, value in payload.items() if key in signature.parameters}
            try:
                return cls(**filtered)
            except TypeError:
                return payload

    @contextmanager
    def _launch_bridge(self, cwd: Path | None, api_key: str):
        cursor_client = getattr(self.sdk, "CursorClient", None)
        if cursor_client is None or not hasattr(cursor_client, "launch_bridge"):
            yield None
            return
        kwargs = {
            "workspace": str(cwd or self.settings.bridge_cwd or Path.cwd()),
            "api_key": api_key,
            "timeout": self.settings.sdk_http_timeout,
        }
        try:
            bridge = cursor_client.launch_bridge(**kwargs)
        except TypeError:
            bridge = cursor_client.launch_bridge(workspace=kwargs["workspace"])
        if hasattr(bridge, "__enter__"):
            with bridge as client:
                yield client
        else:
            try:
                yield bridge
            finally:
                self._close(bridge)

    @contextmanager
    def _control_client(self, agent_id: str, api_key: str):
        stored = self.store.get_agent(agent_id) or {}
        cwd = stored.get("cwd") or self.settings.bridge_cwd or Path.cwd()
        with self._launch_bridge(Path(cwd).expanduser().resolve(), api_key) as client:
            yield client

    @contextmanager
    def _null_context(self, value: Any):
        yield value

    def _agent_prompt(self, prompt: str, options: Any, bridge: Any) -> Any:
        agent = self.sdk.Agent
        try:
            return agent.prompt(prompt, options, client=bridge)
        except TypeError:
            return agent.prompt(prompt, options)

    def _agent_create(self, options: Any, bridge: Any = None) -> Any:
        agent = self.sdk.Agent
        try:
            created = agent.create(options, client=bridge)
        except TypeError:
            created = agent.create(options)
        return created.__enter__() if hasattr(created, "__enter__") else created

    def _agent_resume(self, agent_id: str, options: Any, bridge: Any = None) -> Any:
        agent = self.sdk.Agent
        try:
            resumed = agent.resume(agent_id, options, client=bridge)
        except TypeError:
            resumed = agent.resume(agent_id, options)
        return resumed.__enter__() if hasattr(resumed, "__enter__") else resumed

    def _agent_send(self, agent: Any, prompt: str, **kwargs: Any) -> Any:
        send = agent.send
        call_kwargs = {key: value for key, value in kwargs.items() if value is not None}
        try:
            return send(prompt, **call_kwargs)
        except TypeError:
            try:
                signature = inspect.signature(send)
            except (TypeError, ValueError):
                signature = None
            if signature is not None and not any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            ):
                accepted = {
                    key: value for key, value in call_kwargs.items() if key in signature.parameters
                }
                if accepted != call_kwargs:
                    try:
                        return send(prompt, **accepted)
                    except TypeError:
                        pass
            reduced = dict(call_kwargs)
            for key in list(call_kwargs):
                reduced.pop(key, None)
                try:
                    return send(prompt, **reduced)
                except TypeError:
                    continue
            return send(prompt)

    def _wait(self, run: Any) -> Any:
        wait = getattr(run, "wait", None)
        if not callable(wait):
            return run
        try:
            result = wait(timeout=self.settings.run_wait_timeout)
        except TypeError:
            result = wait()
        return result or run

    def _result_from_run(
        self, run: Any, *, runtime: str, model: Any = None, agent_id: str | None = None
    ) -> ResultDict:
        agent_id = agent_id or self._agent_id(run)
        run_id = self._run_id(run)
        status = self._status(run) or "finished"
        text = extract_assistant_text(run)
        usage = _value(run, "usage")
        if agent_id:
            existing = self.store.get_agent(agent_id) or {}
            self.store.upsert_agent(
                agent_id,
                runtime=runtime,
                cwd=existing.get("cwd"),
                repos=existing.get("repos"),
                model=model if model is not None else existing.get("model"),
                auto_create_pr=bool(existing.get("auto_create_pr", False)),
            )
        if run_id and agent_id:
            self.store.upsert_run(run_id, agent_id=agent_id, status=status, usage=usage)
            if text:
                self.store.save_run_text(run_id, text)
        if status.lower() in {"error", "failed"}:
            return error_result(
                map_exception(RunFailedError("Cursor run failed")),
                agent_id=agent_id,
                run_id=run_id,
                runtime=runtime,
                status=status,
                result_text=text,
                model=model,
                usage=usage,
            )
        return ok_result(
            agent_id=agent_id,
            run_id=run_id,
            runtime=runtime,
            status=status,
            result_text=text,
            model=model,
            usage=usage,
        )

    def _runtime(self, agent_id: str) -> str:
        stored = self.store.get_agent(agent_id)
        if stored and stored.get("runtime"):
            return str(stored["runtime"])
        return "cloud" if agent_id.startswith("bc-") else "local"

    def _agent_id(self, obj: Any) -> str | None:
        return _id(obj, "agent_id", "agentId", "id")

    def _run_id(self, obj: Any) -> str | None:
        return _id(obj, "run_id", "runId", "id")

    def _status(self, obj: Any) -> str | None:
        status = _value(obj, "status")
        return str(status) if status is not None else None

    def _latest_run_id(self, agent_id: str) -> str | None:
        runs = self.store.list_runs(agent_id, limit=1)
        return str(runs[0]["run_id"]) if runs else None

    def _is_busy(self, agent_id: str) -> bool:
        runs = self.store.list_runs(agent_id, limit=1)
        return bool(runs and str(runs[0].get("status", "")).lower() in BUSY_STATUSES)

    def _get_agent(self, client: Any, agent_id: str) -> Any:
        if client is not None and hasattr(client, "agents"):
            return client.agents.get(agent_id)
        agent = self.sdk.Agent
        if hasattr(agent, "get"):
            try:
                return agent.get(agent_id, api_key=require_api_key(self.settings))
            except TypeError:
                return agent.get(agent_id)
        raise UnsupportedError("Agent lookup is not supported")

    def _get_run(self, client: Any, agent_id: str, run_id: str) -> Any:
        if client is not None and hasattr(client, "agents"):
            try:
                return client.agents.get_run(run_id, agent_id=agent_id)
            except TypeError:
                return client.agents.get_run(run_id)
        agent = self.sdk.Agent
        if hasattr(agent, "get_run"):
            try:
                return agent.get_run(
                    run_id,
                    agent_id=agent_id,
                    runtime=self._runtime(agent_id),
                    api_key=require_api_key(self.settings),
                )
            except TypeError:
                return agent.get_run(run_id)
        raise UnsupportedError("Run lookup is not supported")

    def _list_agents(self, api_key: str, runtime: str | None = None) -> list[Any]:
        cursor_client = getattr(self.sdk, "CursorClient", None)
        if cursor_client and hasattr(cursor_client, "launch_bridge"):
            with self._launch_bridge(self.settings.bridge_cwd, api_key) as client:
                if client is not None and hasattr(client, "agents"):
                    try:
                        return _items(client.agents.list(runtime=runtime), "agents")
                    except TypeError:
                        return _items(client.agents.list(), "agents")
        return []

    def _public(self, obj: Any) -> Any:
        if isinstance(obj, (str, int, float, bool, type(None), list, dict)):
            return obj
        return {
            name: value
            for name in ("agent_id", "id", "status", "runtime")
            if (value := _value(obj, name)) is not None
        }

    def _close(self, obj: Any) -> None:
        close = getattr(obj, "close", None)
        if callable(close):
            close()
