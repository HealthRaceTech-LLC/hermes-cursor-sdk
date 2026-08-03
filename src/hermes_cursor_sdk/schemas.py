"""Hermes tool schemas for the Cursor SDK plugin."""

from __future__ import annotations

from typing import Any

JsonDict = dict[str, Any]

PARAMS_SCHEMA: JsonDict = {
    "type": "object",
    "description": "Cursor SDK parameters to pass through unchanged.",
    "additionalProperties": True,
}

CURSOR_MODELS: JsonDict = {
    "name": "cursor_models",
    "description": "List available Cursor models; use when selecting or validating a model.",
    "parameters": {
        "type": "object",
        "properties": {
            "refresh": {
                "type": "boolean",
                "description": "Refresh the cached model list before returning results.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

CURSOR_REPOSITORIES: JsonDict = {
    "name": "cursor_repositories",
    "description": "List Cursor-accessible repositories; use before starting a cloud agent.",
    "parameters": {
        "type": "object",
        "properties": {
            "refresh": {
                "type": "boolean",
                "description": "Refresh the cached repository list before returning results.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

CURSOR_RUN: JsonDict = {
    "name": "cursor_run",
    "description": "Run a one-shot local Cursor task in an existing working directory.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "cwd": {
                "type": "string",
                "minLength": 1,
                "description": "Local working directory for the Cursor SDK run.",
            },
            "model": {"type": "string", "minLength": 1},
            "params": PARAMS_SCHEMA,
        },
        "required": ["prompt", "cwd"],
        "additionalProperties": False,
    },
}

CURSOR_START: JsonDict = {
    "name": "cursor_start",
    "description": "Start a cloud Cursor agent against one or more repositories.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "repos": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "minLength": 1},
                        "starting_ref": {"type": "string", "minLength": 1},
                        "pr_url": {"type": "string", "minLength": 1},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
            "model": {"type": "string", "minLength": 1},
            "params": PARAMS_SCHEMA,
            "auto_create_pr": {
                "type": "boolean",
                "description": "Ask Cursor to create a pull request when the agent finishes.",
            },
            "skip_reviewer_request": {
                "type": "boolean",
                "description": "Skip automatic reviewer requests for created pull requests.",
            },
            "mode": {
                "type": "string",
                "enum": ["agent", "plan"],
                "description": "Cursor cloud mode to use for the agent.",
            },
            "correlation_id": {"type": "string", "minLength": 1},
            "env_names": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": "Named Cursor cloud environments to attach to the agent.",
            },
            "idempotency_key": {"type": "string", "minLength": 1},
            "wait": {
                "type": "boolean",
                "description": (
                    "Wait for the cloud start response to reach a terminal startup state."
                ),
            },
        },
        "required": ["prompt", "repos"],
        "additionalProperties": False,
    },
}

CURSOR_STATUS: JsonDict = {
    "name": "cursor_status",
    "description": "Check status for a Cursor agent or one of its runs.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1},
        },
        "required": ["agent_id"],
        "additionalProperties": False,
    },
}

CURSOR_RESUME: JsonDict = {
    "name": "cursor_resume",
    "description": "Resume an existing Cursor agent with another prompt.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string", "minLength": 1},
            "prompt": {"type": "string", "minLength": 1},
            "cwd": {
                "type": "string",
                "minLength": 1,
                "description": "Local working directory when resuming a local agent.",
            },
            "force": {
                "type": "boolean",
                "description": "Force sending even when normal session checks would block it.",
            },
        },
        "required": ["agent_id", "prompt"],
        "additionalProperties": False,
    },
}

CURSOR_CANCEL: JsonDict = {
    "name": "cursor_cancel",
    "description": "Cancel a Cursor agent or a specific run for that agent.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string", "minLength": 1},
            "run_id": {"type": "string", "minLength": 1},
        },
        "required": ["agent_id"],
        "additionalProperties": False,
    },
}

CURSOR_SESSION_SEND: JsonDict = {
    "name": "cursor_session_send",
    "description": "Send a prompt into a Hermes-managed Cursor session.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "cwd": {
                "type": "string",
                "minLength": 1,
                "description": "Required on the first turn of a session.",
            },
            "session_tag": {
                "type": "string",
                "minLength": 1,
                "description": "Stable tag when Hermes session ids are unavailable.",
            },
            "agent_id": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "params": PARAMS_SCHEMA,
            "force": {
                "type": "boolean",
                "description": "Force sending even when normal session checks would block it.",
            },
            "close": {
                "type": "boolean",
                "description": "Close the Cursor session after this turn.",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}

CURSOR_AGENT: JsonDict = {
    "name": "cursor_agent",
    "description": "Manage Cursor agents and archive state.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "get",
                    "archive",
                    "delete",
                    "cancel",
                ],
            },
            "agent_id": {"type": "string", "minLength": 1},
            "runtime": {
                "type": "string",
                "enum": ["local", "cloud"],
                "description": "Restrict the operation to local or cloud agents.",
            },
            "confirm_agent_id": {
                "type": "string",
                "minLength": 1,
                "description": "Must match agent_id before archiving or deleting an agent.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

TOOL_SCHEMAS: list[JsonDict] = [
    CURSOR_MODELS,
    CURSOR_REPOSITORIES,
    CURSOR_RUN,
    CURSOR_START,
    CURSOR_STATUS,
    CURSOR_RESUME,
    CURSOR_CANCEL,
    CURSOR_SESSION_SEND,
    CURSOR_AGENT,
]

__all__ = [
    "CURSOR_AGENT",
    "CURSOR_CANCEL",
    "CURSOR_MODELS",
    "CURSOR_REPOSITORIES",
    "CURSOR_RESUME",
    "CURSOR_RUN",
    "CURSOR_SESSION_SEND",
    "CURSOR_START",
    "CURSOR_STATUS",
    "TOOL_SCHEMAS",
]
