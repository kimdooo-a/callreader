"""Thin client for Meta Model API (OpenAI-compatible)."""
from __future__ import annotations

import base64
import json
import os
import re
import time

from openai import OpenAI

MODEL = os.environ.get("CALLREADER_MODEL", "muse-spark-1.3")

_client: OpenAI | None = None
usage_log: list[dict] = []
sam_images = 0  # SAM 3.1 is billed per image, not per token


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set")
    return key


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url="https://api.meta.ai/v1", api_key=api_key())
    return _client


def obj(props: dict, required: list[str] | None = None) -> dict:
    """Strict JSON-schema object: every property required unless listed otherwise."""
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


def chat(messages: list[dict], max_tokens: int = 8000, retries: int = 4, schema: dict | None = None) -> str:
    """One chat call with backoff. Records token usage for cost accounting.
    With `schema`, structured output guarantees the reply parses as that JSON shape."""
    extra = {}
    if schema is not None:
        extra["response_format"] = {"type": "json_schema", "json_schema": {"name": "reply", "strict": True, "schema": schema}}
    delay = 1.0
    for attempt in range(retries):
        try:
            r = client().chat.completions.create(model=MODEL, messages=messages, max_tokens=max_tokens, **extra)
            u = r.usage
            usage_log.append({"in": getattr(u, "prompt_tokens", 0), "out": getattr(u, "completion_tokens", 0),
                              "finish": r.choices[0].finish_reason})
            content = r.choices[0].message.content or ""
            if os.environ.get("CALLREADER_DEBUG"):
                with open(os.environ["CALLREADER_DEBUG"], "a", encoding="utf-8") as fh:
                    fh.write(f"\n===== finish={r.choices[0].finish_reason} in={u.prompt_tokens} "
                             f"out={u.completion_tokens}\n{content}\n")
            return content
        except Exception:  # rate limits and transient 5xx
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return ""


def image_part(data: bytes, mime: str) -> dict:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def parse_json(text: str):
    """Models wrap JSON in prose or fences; take the first JSON value."""
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in model output")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def cost_usd() -> float:
    tin = sum(u["in"] for u in usage_log)
    tout = sum(u["out"] for u in usage_log)
    return tin / 1e6 * 1.25 + tout / 1e6 * 4.25 + sam_images * 0.0025
