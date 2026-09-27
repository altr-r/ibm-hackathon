"""
ai/factory.py — Return the appropriate AIProvider based on environment variables.

Selection order:

  1. If ``no_ai`` is True (the CLI --no-ai flag): always DeterministicProvider.
  2. If an explicit ``provider`` argument is passed (e.g. by the web service):
     use it directly. This lets callers force "deterministic" / "watsonx" /
     "ollama" without mutating the process environment.
  3. Otherwise consult the ``AI_PROVIDER`` environment variable:
       AI_PROVIDER=deterministic -> DeterministicProvider
       AI_PROVIDER=watsonx       -> WatsonxProvider
       AI_PROVIDER=ollama        -> OllamaProvider
  4. If ``AI_PROVIDER`` is unset or unrecognised, preserve the ORIGINAL
     default behaviour so nothing regresses:
       WATSONX_API_KEY present   -> WatsonxProvider
       otherwise                 -> DeterministicProvider

Every provider falls back to DeterministicProvider on any API failure, so
there is always a working provider regardless of network/credential state.
An unknown ``AI_PROVIDER`` value never raises — it is reported as deterministic.
"""
from __future__ import annotations

import os

from .provider import AIProvider

# Canonical provider identifiers accepted by AI_PROVIDER / the `provider` arg.
_PROVIDER_NAMES = ("deterministic", "watsonx", "ollama")


def _sanitize(raw: str | None) -> str:
    """Lower-case and strip an env value (tolerate 'AI_PROVIDER=ollama')."""
    if not raw:
        return ""
    val = raw.strip()
    if "=" in val:
        val = val.split("=", 1)[1]
    return val.strip().strip('"').strip("'").lower()


def get_provider(
    no_ai: bool = False,
    provider: str | None = None,
) -> AIProvider:
    """
    Return the best available AI provider.

    Parameters
    ----------
    no_ai : bool
        If True, always return DeterministicProvider regardless of env vars.
    provider : str | None
        Optional explicit provider name ("deterministic" / "watsonx" /
        "ollama"). When given, it takes precedence over the environment.
        Unknown values fall through to environment/default resolution.
    """
    from .deterministic import DeterministicProvider

    if no_ai:
        return DeterministicProvider()

    # Explicit override (used by the web service / tests).
    requested = _sanitize(provider) or _sanitize(os.environ.get("AI_PROVIDER"))

    if requested == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider()

    if requested == "watsonx":
        from .watsonx import WatsonxProvider
        return WatsonxProvider()

    if requested == "deterministic":
        return DeterministicProvider()

    # --- Original default behaviour (AI_PROVIDER unset / unrecognised) ---
    if os.environ.get("WATSONX_API_KEY"):
        from .watsonx import WatsonxProvider
        return WatsonxProvider()

    return DeterministicProvider()
