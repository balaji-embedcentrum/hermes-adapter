"""Provider registry — upstream URL + key env var per supported LLM.

Add a new provider by appending an entry to ``PROVIDERS``. The proxy
route reads from this table; nothing else needs to change. Keep this
list small — every entry is a trust decision (we proxy traffic to that
upstream and inject a real API key into it).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """One LLM upstream the adapter is willing to proxy to.

    * ``key_env``       — env var name in the agent's ``.env`` that
                          holds the real API key (read by the adapter,
                          never seen by the agent container).
    * ``base_url_env``  — optional env var the agent's ``.env`` may
                          override the default upstream with (e.g.
                          regional endpoints). Falls back to
                          ``default_base_url`` when unset.
    * ``default_base_url`` — the public upstream the agent's request
                              forwards to.
    """

    name: str
    key_env: str
    base_url_env: str
    default_base_url: str


PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in [
        Provider("minimax", "MINIMAX_API_KEY", "MINIMAX_API_BASE", "https://api.minimax.io/v1"),
        Provider("openai", "OPENAI_API_KEY", "OPENAI_API_BASE", "https://api.openai.com/v1"),
        Provider("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE", "https://api.anthropic.com/v1"),
        Provider("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
        Provider("together", "TOGETHER_API_KEY", "TOGETHER_API_BASE", "https://api.together.xyz/v1"),
        Provider("groq", "GROQ_API_KEY", "GROQ_API_BASE", "https://api.groq.com/openai/v1"),
        # Google Gemini exposes an OpenAI-compatible endpoint at
        # ``/v1beta/openai`` that accepts ``Authorization: Bearer <key>``,
        # so it slots into the same proxy pattern as the others. The
        # native /v1/models?key=... API would need a different injection
        # strategy and is intentionally not used here.
        Provider("google", "GEMINI_API_KEY", "GEMINI_API_BASE",
                 "https://generativelanguage.googleapis.com/v1beta/openai"),
    ]
}
