"""Perplexity AI provider profile.

Perplexity exposes an OpenAI-compatible Chat Completions endpoint.
Sonar models include real-time web search; r1-1776 is offline only.
"""

from providers import register_provider
from providers.base import ProviderProfile


perplexity = ProviderProfile(
    name="perplexity",
    aliases=("perplexity-ai", "pplx"),
    display_name="Perplexity AI",
    description="Perplexity AI — sonar models with real-time web search",
    signup_url="https://www.perplexity.ai/settings/api",
    env_vars=("PERPLEXITY_API_KEY",),
    base_url="https://api.perplexity.ai",
    auth_type="api_key",
    default_aux_model="sonar",
    fallback_models=(
        "sonar-pro",
        "sonar-reasoning-pro",
        "sonar-reasoning",
        "sonar",
        "r1-1776",
    ),
)

register_provider(perplexity)
