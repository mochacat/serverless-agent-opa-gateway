"""Thin client for the agent gateway — what a framework adapter calls.

A framework adapter (LangChain today, Pydantic AI next) does exactly one thing
before a tool runs: ask the gateway "may this principal perform this tool with
these arguments?". This module is that ask. It sends the **normalized
``tools/call`` payload** (see DESIGN §4.11) over JSON-RPC and returns a typed
:class:`Verdict` — ``allowed`` / ``denied`` with the policy ``reason`` and any
output the gateway redacted (feature G).

Transport uses the standard library only (``urllib``), matching the zero-heavy-
dependency posture. The gateway itself is transport-agnostic; this client just
speaks its JSON-RPC-over-HTTP surface.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# JSON-RPC application code the gateway uses for a policy denial.
CODE_TOOL_DENIED = -32000


@dataclass
class Verdict:
    """The gateway's decision for one tool dispatch.

    ``allowed`` is the ONLY field a caller must branch on. ``reason`` is the
    structured policy explanation (shown to an LLM on deny). ``redacted_output``
    carries G-redacted fields when the gateway was handed an output to inspect;
    ``raw`` is the full JSON-RPC response envelope for advanced callers.
    """

    allowed: bool
    reason: str = ""
    redacted_output: Any = None
    raw: dict[str, Any] = field(default_factory=dict)


class GatewayClient:
    """Minimal JSON-RPC client for the agent gateway (stdlib only)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        *,
        timeout: float = 5.0,
        traceparent: str | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/tools/call"
        self._timeout = timeout
        self._traceparent = traceparent

    def check(
        self,
        *,
        tool: str,
        args: Mapping[str, Any] | None = None,
        industry: str = "",
        context: Mapping[str, Any] | None = None,
        principal_id: str | None = None,
        output: Any = None,
        session_id: str | None = None,
    ) -> Verdict:
        """Send one normalized ``tools/call`` and return the :class:`Verdict`.

        ``tool`` + ``args`` are the normalized schema the frameworks map onto;
        everything else is optional gateway enrichment (identity, cross-call
        context, G-redaction target). Never raises on a denial — check
        ``.allowed``.
        """
        params: dict[str, Any] = {
            "tool": tool,
            "industry": industry,
            "args": dict(args or {}),
        }
        if context is not None:
            params["context"] = dict(context)
        if principal_id is not None:
            params["principal_id"] = principal_id
        if output is not None:
            params["output"] = output
        if session_id is not None:
            params["session_id"] = session_id

        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": params,
        }
        response = self._post(payload)
        return self._to_verdict(response)

    # -- internals ----------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._traceparent:
            headers["traceparent"] = self._traceparent
        request = Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self._timeout) as resp:  # noqa: S310 (configurable gateway URL)
                return json.loads(resp.read())
        except (URLError, HTTPError, TimeoutError) as exc:
            # Fail closed: a gateway we cannot reach must not let a tool run.
            return {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {
                    "code": -32001,
                    "message": "Gateway unreachable",
                    "data": {"reason": f"gateway unreachable: {exc}"},
                },
            }

    @staticmethod
    def _to_verdict(response: Mapping[str, Any]) -> Verdict:
        if "result" in response:
            result = response["result"] or {}
            return Verdict(
                allowed=bool(result.get("allowed", False)),
                redacted_output=result.get("redacted_output"),
                raw=dict(response),
            )
        error = response.get("error", {}) or {}
        data = error.get("data", {}) or {}
        reason = data.get("reason") or error.get("message") or "denied"
        return Verdict(allowed=False, reason=str(reason), raw=dict(response))
