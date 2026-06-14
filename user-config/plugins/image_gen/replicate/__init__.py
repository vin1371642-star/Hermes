"""
Replicate image generation backend for Hermes.
Default: black-forest-labs/flux-1.1-pro-ultra (8MP, best photorealistic quality).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    success_response,
)

logger = logging.getLogger(__name__)

# Replicate model slug → (owner/name, supported aspect_ratios)
REPLICATE_IMAGE_MODELS: Dict[str, Dict[str, Any]] = {
    "flux-1.1-pro-ultra": {
        "slug": "black-forest-labs/flux-1.1-pro-ultra",
        "display": "FLUX 1.1 Pro Ultra",
        "speed": "~15s",
        "strengths": "Photorealistic, 8MP, best quality on Replicate",
        "price": "$0.006/img",
        "aspect_ratios": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4"],
    },
    "flux-1.1-pro": {
        "slug": "black-forest-labs/flux-1.1-pro",
        "display": "FLUX 1.1 Pro",
        "speed": "~10s",
        "strengths": "High quality, faster than Ultra",
        "price": "$0.004/img",
        "aspect_ratios": ["1:1", "16:9", "9:16", "3:2", "2:3"],
    },
    "flux-dev": {
        "slug": "black-forest-labs/flux-dev",
        "display": "FLUX Dev",
        "speed": "~5s",
        "strengths": "Fast, good for iteration",
        "price": "$0.003/img",
        "aspect_ratios": ["1:1", "16:9", "9:16"],
    },
}

DEFAULT_IMAGE_MODEL = "flux-1.1-pro-ultra"

# Hermes aspect_ratio → Replicate format
_AR_MAP = {
    "landscape": "16:9",
    "portrait":  "9:16",
    "square":    "1:1",
}


class ReplicateImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "replicate"

    @property
    def display_name(self) -> str:
        return "Replicate"

    def is_available(self) -> bool:
        return bool(os.environ.get("REPLICATE_API_TOKEN", ""))

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": mid,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
            }
            for mid, meta in REPLICATE_IMAGE_MODELS.items()
        ]

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Replicate",
            "badge": "paid",
            "tag": "FLUX 1.1 Pro Ultra — best photorealistic image gen",
            "env_vars": [
                {
                    "key": "REPLICATE_API_TOKEN",
                    "prompt": "Replicate API token",
                    "url": "https://replicate.com/account/api-tokens",
                }
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        api_key = os.environ.get("REPLICATE_API_TOKEN", "")
        if not api_key:
            return error_response(error="REPLICATE_API_TOKEN not set", provider=self.name, prompt=prompt)

        model_id = kwargs.get("model") or DEFAULT_IMAGE_MODEL
        model_meta = REPLICATE_IMAGE_MODELS.get(model_id, REPLICATE_IMAGE_MODELS[DEFAULT_IMAGE_MODEL])
        slug = model_meta["slug"]
        owner, name_ = slug.split("/", 1)

        ar = _AR_MAP.get(resolve_aspect_ratio(aspect_ratio), "16:9")

        try:
            import urllib.request
            import json as _json

            # Submit prediction
            payload = _json.dumps({
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": ar,
                    "output_format": "webp",
                    "safety_tolerance": 2,
                }
            }).encode("utf-8")

            req = urllib.request.Request(
                f"https://api.replicate.com/v1/models/{owner}/{name_}/predictions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Prefer": "wait",  # wait up to 60s for result
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                pred = _json.loads(resp.read())

            # If status is already succeeded (sync wait mode)
            status = pred.get("status")
            output = pred.get("output")

            if status == "succeeded" and output:
                img_url = output[0] if isinstance(output, list) else output
                return success_response(
                    image=img_url,
                    model=slug,
                    prompt=prompt,
                    aspect_ratio=resolve_aspect_ratio(aspect_ratio),
                    provider=self.name,
                )

            # Otherwise poll
            pred_id = pred.get("id")
            if not pred_id:
                return error_response(error=f"No prediction id: {pred}", provider=self.name, prompt=prompt)

            poll_req = urllib.request.Request(
                f"https://api.replicate.com/v1/predictions/{pred_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            for _ in range(90):  # up to 3 min
                time.sleep(2)
                with urllib.request.urlopen(poll_req, timeout=15) as pr:
                    data = _json.loads(pr.read())
                status = data.get("status")
                if status == "succeeded":
                    output = data.get("output")
                    img_url = output[0] if isinstance(output, list) else output
                    return success_response(
                        image=img_url,
                        model=slug,
                        prompt=prompt,
                        aspect_ratio=resolve_aspect_ratio(aspect_ratio),
                        provider=self.name,
                    )
                elif status in ("failed", "canceled"):
                    return error_response(
                        error=data.get("error") or f"Prediction {status}",
                        provider=self.name,
                        prompt=prompt,
                        model=slug,
                    )

            return error_response(error="Replicate prediction timed out (3 min)", provider=self.name, prompt=prompt)

        except Exception as exc:
            logger.exception("replicate image_gen error")
            return error_response(error=str(exc), provider=self.name, prompt=prompt)


def register(ctx) -> None:
    ctx.register_image_gen_provider(ReplicateImageGenProvider())
