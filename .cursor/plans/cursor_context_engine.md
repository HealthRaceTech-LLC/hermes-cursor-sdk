# Cursor bridge context integration

## Context

The user reported a status-bar symptom: **5.6M / 200K** on a Grok 4.5 Max session. Two distinct problems contribute to that reading:

1. **Numerator is wrong.** The Hermes 0.20.0 desktop binary (engine 0.17.0) shows `session_total_tokens` (cumulative lifetime) when `last_prompt_tokens` is 0. The upstream fix (commit #50421) reads `last_prompt_tokens` and only falls back to `session_total_tokens` if `last_prompt_tokens == 0`. The fix is in source on disk at `~/.hermes/hermes-agent/tui_gateway/server.py:4866-4873` but not in the shipped binary.

2. **Denominator is wrong.** The user has `model.context_length: 200000` pinned in their Hermes profile. This forces the meter to use 200K (Composer's window) regardless of the actual model. Grok 4.5 Max is 256K.

A previous draft of this plan proposed adding a context-engine plugin to the SDK. That plugin would:
- Populate `last_prompt_tokens` correctly on every turn
- Surface a `compression_state = "unknown"` field
- Surface "Cursor-managed context" disclosure

**Honest re-assessment:** the plugin is unnecessary for the immediate symptom. The SDK already emits `prompt_tokens` correctly via `to_openai_usage()`. Hermes's `ContextCompressor.update_from_response()` already stores `last_prompt_tokens` correctly. The user's symptom is purely a stale-binary issue and a config gotcha — neither requires SDK code.

The plugin is still **valid as a future enhancement** (clean upstream-PR target, foundation for compression-aware feature work), but it doesn't earn its keep today.

## What we actually need

### A. Confirm the SDK is correct (read-only analysis)

Verify there is no SDK bug that needs fixing. The relevant code:

- `src/hermes_cursor_sdk/results.py:101-125` — `to_openai_usage()` produces `prompt_tokens = input + cache_read + cache_write`. Correct.
- `src/hermes_cursor_sdk/bridge/server.py:423-433` — emits trailing SSE chunk with `usage.prompt_tokens` per request. Correct.
- `src/hermes_cursor_sdk/bridge/server.py:726-728` — non-stream completions include `usage`. Correct.
- `src/hermes_cursor_sdk/models.py:169-198` — `infer_model_context_length()` returns the right value per model from the Cursor catalog. Correct.

**No code change in the SDK is required for the 5.6M / 200K symptom.**

### B. Document the two fixes the user can apply

Two immediate fixes for the symptom, no SDK changes required:

**B1. Drop the `model.context_length` pin.**

The user's `~/.hermes/config.yaml` has `model.context_length: 200000` pinned. This was added per the SDK's docs as a workaround for stale caches. Removing the pin lets the SDK's `/v1/models` endpoint advertise the correct window per model:

- Composer-2.5 → 200K (from the SDK's `_COMPOSER_CONTEXT_LENGTH` constant at `models.py:106`)
- Grok 4.5 Max → 256K (from the Cursor catalog's `context_options`)
- Other models with a Max Mode toggle → their max option

The pin is documented in `docs/chat-provider-mode.md:64` as a workaround for stale caches. If the user's cache is fresh, the pin is unnecessary and produces wrong values for non-Composer models.

If the cache is stale, the user can still override per-model via the SDK's config or the bridge's `--context-length` flag, but the default should be no pin.

**B2. Wait for or patch the upstream Hermes fix.**

The desktop binary is older than the source fix. Either:
- Wait for the next Hermes Desktop release that ships the #50421 fix.
- Patch `~/.hermes/hermes-agent/tui_gateway/server.py:4849-4873` directly with the post-fix code (the on-disk source already has it). The binary is the issue, not the source.

For the user on 0.20.0 (binary 0.17.0), patching the source is the only way to fix the numerator without waiting. The user can do this by:
1. Confirming the on-disk source already has the fix (it does, as of Aug 3).
2. Restarting Hermes Desktop to pick up the patched source.

*Wait — does the desktop binary run from the on-disk source, or from a bundled copy?* This is the actual question. Let me investigate.

### C. (Optional, future) Context-engine plugin

The plugin from the previous draft is parked here. It is **not** part of the immediate fix. It becomes relevant if:

- The user wants to upstream a clean SDK integration to Hermes
- We want to surface honest "Cursor-managed context" labels in the status bar
- Cursor exposes a `compressed` flag on `RunResult` that we want to surface

If/when we proceed, the plugin lives in the SDK repo at `src/hermes_cursor_sdk/hermes/`, shipped via the SDK's install helper, and references the existing `ContextEngine` ABC. See the prior draft for the full file layout.

## Investigation needed

Before writing any code, verify the actual runtime architecture. The big question: **does the Hermes Desktop binary run from source on disk, or from a bundled copy?**

- If from source: patching the source fixes the symptom immediately.
- If from a bundled copy: the user has to wait for the upstream release, or the plugin path becomes more attractive.

This is the investigation step that should drive what we actually build.

## Files to change

**None, until the architecture question is resolved.**

Once the answer is in hand:
- If the desktop binary reads from source: document the patch path in `docs/chat-provider-mode.md` and ship it.
- If the desktop binary is self-contained: the SDK plugin becomes the recommended path, and we should implement it. Re-open the prior draft.

## What we have to gain

Polishing the SDK purely for the symptom is wasted work. The plugin is a real piece of work, but it doesn't fix the user's problem today. The user's problem is a stale binary + a config pin. Until those are resolved, no SDK change helps.

The right next move is **investigate the runtime architecture of the Hermes Desktop binary**, not write more SDK code.

## Plan

1. **Investigate** whether Hermes Desktop 0.20.0 binary runs from source on disk or a bundled copy. Look at the app bundle at `/Applications/Hermes.app/Contents/Resources/` or `Contents/MacOS/` for clues.
2. If from source: document the patch path. No SDK code change.
3. If bundled: revisit the plugin plan. The plugin becomes the recommended workaround.
4. Either way: update `docs/chat-provider-mode.md` to document the `model.context_length` pin gotcha more clearly, so users don't trip over it.
