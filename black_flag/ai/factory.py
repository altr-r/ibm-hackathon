"""
ai/factory.py — Return the appropriate AIProvider based on environment variables.

Decision logic:
  1. If --no-ai flag is set (no_ai=True): return DeterministicProvider
  2. If WATSONX_API_KEY is set: return WatsonxProvider
  3. Otherwise: return DeterministicProvider

The WatsonxProvider itself falls back to DeterministicProvider on any API
failure, so there is always a working provider regardless of network state.
"""
from __future__ import annotations

import os

from .provider import AIProvider


def get_provider(no_ai: bool = False) -> AIProvider:
    """
    Return the best available AI provider.

    Parameters
    ----------
    no_ai : bool
        If True, always return DeterministicProvider regardless of env vars.
    """
    from .deterministic import DeterministicProvider

    if no_ai:
        return DeterministicProvider()

    if os.environ.get("WATSONX_API_KEY"):
        from .watsonx import WatsonxProvider
        return WatsonxProvider()

    return DeterministicProvider()
