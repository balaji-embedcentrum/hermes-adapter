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
    * ``default_base_url`` — the upstream HOST ROOT (no version path).
                              The agent's ``*_API_BASE`` env in compose
                              already includes the version, so the
                              captured request path looks like
                              ``v1/chat/completions``. We append that
                              to the host root to get the final URL.

    Wrong: ``https://api.minimax.io/v1`` + ``v1/chat/completions``
           = ``https://api.minimax.io/v1/v1/chat/completions`` (404).
    Right: ``https://api.minimax.io`` + ``v1/chat/completions``
           = ``https://api.minimax.io/v1/chat/completions``.
    """

    name: str
    key_env: str
    base_url_env: str
    default_base_url: str


PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in [
        Provider("minimax",    "MINIMAX_API_KEY",    "MINIMAX_API_BASE",    "https://api.minimax.io"),
        Provider("openai",     "OPENAI_API_KEY",     "OPENAI_API_BASE",     "https://api.openai.com"),
        Provider("anthropic",  "ANTHROPIC_API_KEY",  "ANTHROPIC_API_BASE",  "https://api.anthropic.com"),
        Provider("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_API_BASE", "https://openrouter.ai/api"),
        Provider("together",   "TOGETHER_API_KEY",   "TOGETHER_API_BASE",   "https://api.together.xyz"),
        Provider("groq",       "GROQ_API_KEY",       "GROQ_API_BASE",       "https://api.groq.com/openai"),
        # Google Gemini exposes an OpenAI-compatible endpoint at
        # ``/v1beta/openai`` that accepts ``Authorization: Bearer <key>``.
        # The agent's GEMINI_API_BASE includes ``/v1beta/openai`` so the
        # captured path is ``v1beta/openai/chat/completions``. Default
        # base must NOT include that prefix — same double-prefix rule
        # as every other provider.
        Provider("google",     "GEMINI_API_KEY",     "GEMINI_API_BASE",
                 "https://generativelanguage.googleapis.com"),
    ]
}
