"""MiniMax ModelClient integration.

MiniMax provides an OpenAI-compatible API, so this client
extends OpenAIClient with MiniMax-specific base URL and env var.
"""

import os
import logging
from api.openai_client import OpenAIClient

log = logging.getLogger(__name__)


class MinimaxClient(OpenAIClient):
    """A ModelClient wrapper for the MiniMax API.

    MiniMax exposes an OpenAI-compatible chat completions endpoint,
    so we simply reuse OpenAIClient with a different base URL and
    API-key environment variable.

    Environment variables:
        MINIMAX_API_KEY  – your MiniMax API key (required)
        MINIMAX_BASE_URL – override the base URL (optional,
                           defaults to https://api.minimax.chat/v1)
    """

    def __init__(self, api_key: str | None = None, **kwargs):
        base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            env_api_key_name="MINIMAX_API_KEY",
            env_base_url_name="MINIMAX_BASE_URL",
            **kwargs,
        )
        log.info(f"MinimaxClient initialized with base_url={base_url}")
