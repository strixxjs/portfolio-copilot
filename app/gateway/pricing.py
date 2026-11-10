"""List prices per 1M tokens (USD). Costs reported by this project are computed at these list prices,
even when the provider's free tier bills $0, and are labelled "list price" wherever they appear.

Checked 2026-11-10 against published Groq price listings (groq.com/pricing as quoted by price trackers).
Note: some trackers report Llama 3.x moving to enterprise-only pricing in Aug 2026, which is why the
defaults are the GPT-OSS models. Re-check before quoting numbers; the date above must move with any change.
"""

from __future__ import annotations

from app.gateway.types import ModelPrice

PRICES: dict[str, ModelPrice] = {
    # Groq
    "openai/gpt-oss-20b": ModelPrice(input_per_mtok=0.075, output_per_mtok=0.30),
    "openai/gpt-oss-120b": ModelPrice(input_per_mtok=0.15, output_per_mtok=0.60),
    "llama-3.1-8b-instant": ModelPrice(input_per_mtok=0.05, output_per_mtok=0.08),
    "llama-3.3-70b-versatile": ModelPrice(input_per_mtok=0.59, output_per_mtok=0.79),
    # OpenAI
    "gpt-4o-mini": ModelPrice(input_per_mtok=0.15, output_per_mtok=0.60),
    "gpt-4.1-mini": ModelPrice(input_per_mtok=0.40, output_per_mtok=1.60),
    # Fake providers (tests, offline eval)
    "fake-fast": ModelPrice(input_per_mtok=0.0, output_per_mtok=0.0),
    "fake-strong": ModelPrice(input_per_mtok=0.0, output_per_mtok=0.0),
}
