"""
Optional AI assistant - OFF by default, provider-agnostic, glass-box.

The investigator may configure ONE of:
  * anthropic - the Anthropic API (Claude), POST {base}/v1/messages
  * openai    - the OpenAI API (ChatGPT), POST {base}/chat/completions
  * custom    - ANY OpenAI-compatible endpoint, including LOCAL models
                (Ollama, LM Studio) so case data never leaves the machine.

Design rules, enforced here rather than hoped for:
- Nothing is ever sent to an AI provider until the investigator configures
  one and clicks an AI action. There is no background AI activity.
- Every interaction is recorded IN FULL (prompt and response) in the
  ai_log table - the audit record a court or supervisor can inspect.
- The system prompt binds the model to the supplied trace data, requires
  citations of transaction IDs/addresses, and forbids invented
  attribution or continuity.
- AI output is assistance, never evidence: it is banner-marked in the UI
  and is never written into court reports or worksheets automatically.
- Calls go direct over httpx: never through the evidence cache and never
  into the chain-of-custody log (they are not data acquisitions).
"""

import json
import time

import httpx

from app import config, database

SYSTEM_PROMPT = (
    "You are an analytical assistant embedded in Crypto Investigator, a "
    "cryptocurrency-tracing tool operated by a law-enforcement "
    "investigator. You are given structured trace data (public blockchain "
    "records plus the tool's labelled inferences) and asked to explain or "
    "analyse it.\n"
    "STRICT RULES:\n"
    "1. Base every factual statement ONLY on the trace data provided in "
    "this conversation. If the data does not show something, say exactly "
    "that - never guess, extrapolate, or fill gaps.\n"
    "2. When you make a specific claim about a movement or wallet, cite "
    "the transaction ID and/or address from the data so the investigator "
    "can verify it in one click.\n"
    "3. The data separates on-chain FACTS from labelled INFERENCES "
    "(each label/finding carries a source and confidence). Preserve that "
    "distinction: never present an inference as a fact, and never invent "
    "attribution or continuity the tool itself refused to assert (e.g. "
    "through a mixer).\n"
    "4. Your output is investigative assistance, NOT evidence, and must "
    "not be quoted in affidavits. When asked for next steps, ground them "
    "in the findings' recommended actions.\n"
    "5. Write plainly for a non-specialist detective. Be concise; lead "
    "with what matters most."
)


class AssistantError(Exception):
    """Raised when the AI assistant cannot complete a request."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def get_config() -> dict:
    """Current assistant configuration (never includes the key)."""
    provider = database.get_setting("ai_provider", "")
    if provider not in config.AI_PROVIDERS:
        provider = ""
    key = database.get_setting("ai_api_key", "")
    model = (database.get_setting("ai_model", "")
             or config.AI_DEFAULT_MODELS.get(provider, ""))
    base = (database.get_setting("ai_base_url", "")
            or config.AI_DEFAULT_BASES.get(provider, "")).rstrip("/")
    # A local/custom endpoint may legitimately need no key (Ollama).
    configured = bool(provider and model and (key or provider == "custom"))
    return {
        "provider": provider,
        "model": model,
        "base_url": base,
        "has_key": bool(key),
        # Anthropic identity-linked keys that are not scoped to a single
        # workspace must name the workspace on every request.
        "workspace_id": database.get_setting("ai_workspace_id", ""),
        "configured": configured,
        "local": base.startswith("http://localhost")
                 or base.startswith("http://127.0.0.1"),
    }


# ---------------------------------------------------------------------------
# Provider calls (raw HTTP; provider-neutral by design)
# ---------------------------------------------------------------------------

def _call_anthropic(base: str, key: str, model: str, messages: list,
                    workspace_id: str = "") -> str:
    """One Anthropic Messages API call. Thinking is left at the model's
    default (current Claude models manage it adaptively). Identity-linked
    keys that span workspaces additionally need the workspace header."""
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if workspace_id:
        headers["anthropic-workspace-id"] = workspace_id
    response = httpx.post(
        f"{base}/v1/messages",
        headers=headers,
        json={
            "model": model,
            "max_tokens": config.AI_MAX_RESPONSE_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": messages,
        },
        timeout=config.AI_REQUEST_TIMEOUT_SECONDS,
    )
    payload = response.json()
    if response.status_code != 200:
        detail = (payload.get("error") or {}).get("message") \
            or f"HTTP {response.status_code}"
        if "anthropic-workspace-id" in detail:
            raise AssistantError(
                "Your Anthropic key is identity-linked and not scoped to "
                "a single workspace, so requests must say which workspace "
                "they act in. Fix either way: (1) open Settings and paste "
                "your Workspace ID into the new 'Anthropic workspace ID' "
                "field - find it in the Claude Console under Settings → "
                "Workspaces, ID column (looks like wrkspc_...); or "
                "(2) create a new API key in the Console and scope it to "
                "one workspace when creating it - scoped keys need no ID. "
                f"(API said: {detail})")
        raise AssistantError(f"{model}: {detail}")
    if payload.get("stop_reason") == "refusal":
        details = payload.get("stop_details") or {}
        raise AssistantError(
            "The model declined this request"
            + (f" ({details.get('explanation')})"
               if details.get("explanation") else "")
            + ". Rephrase the question or ask about a narrower part of "
            "the trace.")
    text = "".join(block.get("text", "")
                   for block in payload.get("content", [])
                   if block.get("type") == "text").strip()
    if not text:
        raise AssistantError(f"{model}: the response contained no text")
    return text


def _call_openai_style(base: str, key: str, model: str, messages: list,
                       official_openai: bool) -> str:
    """One chat-completions call (OpenAI or any compatible endpoint)."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                    + messages,
    }
    # OpenAI's current models take max_completion_tokens; most compatible
    # servers (Ollama, LM Studio, vLLM) still take max_tokens.
    if official_openai:
        body["max_completion_tokens"] = config.AI_MAX_RESPONSE_TOKENS
    else:
        body["max_tokens"] = config.AI_MAX_RESPONSE_TOKENS
    headers = {"content-type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = httpx.post(f"{base}/chat/completions", headers=headers,
                          json=body,
                          timeout=config.AI_REQUEST_TIMEOUT_SECONDS)
    try:
        payload = response.json()
    except ValueError:
        raise AssistantError(
            f"{model}: endpoint returned a non-JSON response "
            f"(HTTP {response.status_code}) - check the base URL")
    if response.status_code != 200:
        detail = (payload.get("error") or {}).get("message") \
            or f"HTTP {response.status_code}"
        raise AssistantError(f"{model}: {detail}")
    choices = payload.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") or "").strip() \
        if choices else ""
    if not text:
        raise AssistantError(f"{model}: the response contained no text")
    return text


def ask(purpose: str, messages: list, trace_id=None, case_id=None) -> dict:
    """Send one request to the configured provider; log it IN FULL."""
    settings = get_config()
    if not settings["configured"]:
        raise AssistantError(
            "The AI assistant is not configured. Open Settings and choose "
            "a provider (Claude, ChatGPT, or a local/custom endpoint).")
    key = database.get_setting("ai_api_key", "")
    request_json = json.dumps({"system": SYSTEM_PROMPT,
                               "messages": messages}, indent=1)
    started = time.monotonic()
    error = None
    text = ""
    try:
        if settings["provider"] == "anthropic":
            text = _call_anthropic(settings["base_url"], key,
                                   settings["model"], messages,
                                   workspace_id=settings["workspace_id"])
        else:
            text = _call_openai_style(
                settings["base_url"], key, settings["model"], messages,
                official_openai=(settings["provider"] == "openai"))
    except AssistantError as exc:
        error = str(exc)
        raise
    except httpx.HTTPError as exc:
        error = f"network error: {exc}"
        raise AssistantError(
            f"Could not reach the AI endpoint ({exc}). For a local model, "
            f"check that the server (e.g. Ollama) is running.")
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        database.ai_log_add(settings["provider"], settings["model"],
                            purpose, trace_id, case_id, request_json,
                            text, duration_ms, error)
    return {"text": text, "provider": settings["provider"],
            "model": settings["model"]}


# ---------------------------------------------------------------------------
# Trace context (compact, capped, honest about truncation)
# ---------------------------------------------------------------------------

def trace_context(result: dict) -> str:
    """Serialize a trace result for the model: everything analytical
    (findings, disposition, exits, warnings) in full, nodes and edges
    capped largest-first with an explicit truncation note."""
    edges = sorted(result.get("edges") or [],
                   key=lambda e: -(e.get("value_usd") or e.get("value")
                                   or 0))
    nodes = result.get("nodes") or []
    kept_edges = edges[:config.AI_MAX_CONTEXT_EDGES]
    kept_nodes = nodes[:config.AI_MAX_CONTEXT_NODES]

    def node_view(node):
        return {
            "address": node.get("address"),
            "role": node.get("role"),
            "depth": node.get("depth"),
            "labels": [f"{l.get('entity_name')} ({l.get('category')}, "
                       f"{l.get('confidence')}, {l.get('source')})"
                       for l in (node.get("labels") or [])[:3]],
            "flags": node.get("flags") or [],
            "basis": node.get("basis"),
        }

    def edge_view(edge):
        return {
            "txid": edge.get("txid"),
            "from": edge.get("from_address"),
            "to": edge.get("to_address"),
            "asset": edge.get("asset"),
            "value": edge.get("value"),
            "value_usd": edge.get("value_usd"),
            "timestamp": edge.get("timestamp"),
        }

    document = {
        "note": ("Trace data from Crypto Investigator. 'findings' and "
                 "node labels are the tool's inferences with stated "
                 "confidence; edges are on-chain facts."),
        "chain": result.get("chain"),
        "direction": result.get("direction", "forward"),
        "start_wallet": result.get("victim_address"),
        "focus_txid": result.get("focus_txid") or None,
        "accounting_method": result.get("accounting_method"),
        "stats": result.get("stats"),
        "findings": result.get("findings"),
        "disposition": result.get("disposition"),
        "exits": result.get("exits"),
        "warnings": result.get("warnings"),
        "nodes": [node_view(n) for n in kept_nodes],
        "edges": [edge_view(e) for e in kept_edges],
    }
    if len(edges) > len(kept_edges) or len(nodes) > len(kept_nodes):
        document["truncation"] = (
            f"Context includes the {len(kept_edges)} largest of "
            f"{len(edges)} movements and {len(kept_nodes)} of "
            f"{len(nodes)} addresses; totals in 'disposition' cover "
            f"everything.")
    text = json.dumps(document, default=str)
    if len(text) > config.AI_MAX_CONTEXT_CHARS:
        # Progressive shrink: halve the movement list until it fits.
        while len(text) > config.AI_MAX_CONTEXT_CHARS and \
                len(document["edges"]) > 20:
            document["edges"] = document["edges"][
                :len(document["edges"]) // 2]
            document["truncation"] = (
                f"Context truncated to the {len(document['edges'])} "
                f"largest movements (of {len(edges)}); totals in "
                f"'disposition' cover everything.")
            text = json.dumps(document, default=str)
    return text


SUMMARY_INSTRUCTION = (
    "Write a plain-language investigative summary of this trace for a "
    "detective's case notes: (1) what happened to the money, in "
    "chronological flow; (2) the strongest findings and why they matter, "
    "citing the key transactions/addresses; (3) what is still unknown or "
    "unresolved; (4) recommended next steps drawn from the findings. "
    "Keep it under 400 words.")


def summarize_trace(result: dict, trace_id, case_id) -> dict:
    messages = [{"role": "user",
                 "content": f"TRACE DATA:\n{trace_context(result)}\n\n"
                            f"{SUMMARY_INSTRUCTION}"}]
    return ask("trace_summary", messages, trace_id=trace_id,
               case_id=case_id)


def answer_question(result: dict, trace_id, case_id, question: str,
                    history: list) -> dict:
    """Q&A over one trace. `history` is prior [{role, content}] turns."""
    clean_history = [
        {"role": turn.get("role"), "content": str(turn.get("content", ""))}
        for turn in (history or [])[-10:]
        if turn.get("role") in ("user", "assistant")
        and turn.get("content")
    ]
    messages = ([{"role": "user",
                  "content": f"TRACE DATA:\n{trace_context(result)}"},
                 {"role": "assistant",
                  "content": "Understood. I will answer questions using "
                             "only this trace data, citing transactions "
                             "and addresses."}]
                + clean_history
                + [{"role": "user", "content": question}])
    return ask("trace_question", messages, trace_id=trace_id,
               case_id=case_id)


IC3_INSTRUCTION = (
    "Draft the 'Description of Incident' narrative for an FBI IC3 "
    "complaint based on the trace data and any partial notes provided. "
    "Requirements: first person from the victim's perspective is NOT "
    "required - neutral factual narrative is fine; include how the "
    "payments flowed (amounts, dates, wallet addresses) from the trace; "
    "do not invent contact methods, promises, or personal details that "
    "are not in the notes - leave [BRACKETED PLACEHOLDERS] for facts "
    "only the victim can supply; stay under 3,400 characters (the form "
    "cuts off at 3,500).")


def draft_ic3_narrative(result: dict, existing_description: str,
                        case_id) -> dict:
    notes = (f"PARTIAL NOTES ALREADY WRITTEN:\n{existing_description}\n\n"
             if existing_description.strip() else "")
    messages = [{"role": "user",
                 "content": f"TRACE DATA:\n{trace_context(result)}\n\n"
                            f"{notes}{IC3_INSTRUCTION}"}]
    return ask("ic3_narrative", messages, case_id=case_id)
