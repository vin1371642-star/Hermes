"""
Replicate video generation backend for Hermes.
Default: wan-video/wan2.1-t2v-720p (best open-weight T2V on Replicate).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from agent.video_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    VideoGenProvider,
    error_response,
    success_response,
)

logger = logging.getLogger(__name__)

REPLICATE_VIDEO_MODELS: Dict[str, Dict[str, Any]] = {
    "wan-2.1-720p": {
        "slug": "wan-video/wan2.1-t2v-720p",
        "slug_i2v": "wan-video/wan2.1-i2v-480p",
        "display": "Wan 2.1 720p",
        "speed": "~60-120s",
        "strengths": "Best open-weight T2V, realistic motion, 720p",
        "price": "~$0.10/video",
        "modalities": ["text", "image"],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
    "hunyuan-video": {
        "slug": "tencent/hunyuan-video",
        "slug_i2v": None,
        "display": "HunyuanVideo",
        "speed": "~90-180s",
        "strengths": "High fidelity, cinematic quality, text-to-video",
        "price": "~$0.15/video",
        "modalities": ["text"],
        "aspect_ratios": ["16:9", "9:16"],
    },
    "minimax-video": {
        "slug": "minimax/video-01",
        "slug_i2v": "minimax/video-01",
        "display": "Minimax Video-01",
        "speed": "~45s",
        "strengths": "Fast, good motion, supports image-to-video",
        "price": "~$0.08/video",
        "modalities": ["text", "image"],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
}

DEFAULT_VIDEO_MODEL = "wan-2.1-720p"

_AR_MAP = {
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1":  "1:1",
    "4:3":  "16:9",  # clamp to closest
    "3:4":  "9:16",
    "3:2":  "16:9",
    "2:3":  "9:16",
}


class ReplicateVideoGenProvider(VideoGenProvider):
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
                "modalities": meta["modalities"],
            }
            for mid, meta in REPLICATE_VIDEO_MODELS.items()
        ]

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "resolutions": ["480p", "720p"],
            "max_duration": 10,
            "min_duration": 3,
            "supports_audio": False,
            "supports_negative_prompt": True,
            "max_reference_images": 1,
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Replicate",
            "badge": "paid",
            "tag": "Wan 2.1 720p — best open-weight T2V",
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
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        resolution: str = DEFAULT_RESOLUTION,
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        api_key = os.environ.get("REPLICATE_API_TOKEN", "")
        if not api_key:
            return error_response(error="REPLICATE_API_TOKEN not set", provider=self.name, prompt=prompt)

        model_id = model or os.environ.get("REPLICATE_VIDEO_MODEL", DEFAULT_VIDEO_MODEL)
        model_meta = REPLICATE_VIDEO_MODELS.get(model_id, REPLICATE_VIDEO_MODELS[DEFAULT_VIDEO_MODEL])

        is_i2v = bool(image_url)
        slug = model_meta["slug_i2v"] if (is_i2v and model_meta.get("slug_i2v")) else model_meta["slug"]
        owner, name_ = slug.split("/", 1)

        ar = _AR_MAP.get(aspect_ratio, "16:9")

        # Build input payload per model
        input_payload: Dict[str, Any] = {"prompt": prompt}
        if model_id == "wan-2.1-720p":
            input_payload["num_frames"] = min(duration * 16 if duration else 81, 161)
            input_payload["sampling_steps"] = 30
            if negative_prompt:
                input_payload["negative_prompt"] = negative_prompt
            if image_url and is_i2v:
                input_payload["image"] = image_url
        elif model_id == "hunyuan-video":
            input_payload["video_length"] = duration or 5
            input_payload["resolution"] = "720p" if "720" in resolution else "480p"
        elif model_id == "minimax-video":
            if image_url and is_i2v:
                input_payload["first_frame_image"] = image_url
            input_payload["prompt_optimizer"] = True

        if seed is not None:
            input_payload["seed"] = seed

        try:
            import json as _json
            import urllib.request

            payload = _json.dumps({"input": input_payload}).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.replicate.com/v1/models/{owner}/{name_}/predictions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                pred = _json.loads(resp.read())

            pred_id = pred.get("id")
            if not pred_id:
                return error_response(error=f"No prediction id: {pred}", provider=self.name, prompt=prompt)

            status = pred.get("status")
            output = pred.get("output")
            if status == "succeeded" and output:
                video_url = output[0] if isinstance(output, list) else output
                return success_response(
                    video=video_url,
                    model=slug,
                    prompt=prompt,
                    modality="image" if is_i2v else "text",
                    aspect_ratio=ar,
                    duration=duration or 5,
                    provider=self.name,
                )

            # Poll for completion
            poll_req = urllib.request.Request(
                f"https://api.replicate.com/v1/predictions/{pred_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            for _ in range(150):  # up to 5 min
                time.sleep(2)
                with urllib.request.urlopen(poll_req, timeout=15) as pr:
                    data = _json.loads(pr.read())
                status = data.get("status")
                if status == "succeeded":
                    output = data.get("output")
                    video_url = output[0] if isinstance(output, list) else output
                    return success_response(
                        video=video_url,
                        model=slug,
                        prompt=prompt,
                        modality="image" if is_i2v else "text",
                        aspect_ratio=ar,
                        duration=duration or 5,
                        provider=self.name,
                    )
                elif status in ("failed", "canceled"):
                    return error_response(
                        error=data.get("error") or f"Prediction {status}",
                        provider=self.name,
                        prompt=prompt,
                        model=slug,
                    )

            return error_response(error="Replicate video prediction timed out (5 min)", provider=self.name, prompt=prompt)

        except Exception as exc:
            logger.exception("replicate video_gen error")
            return error_response(error=str(exc), provider=self.name, prompt=prompt)


def register(ctx) -> None:
    ctx.register_video_gen_provider(ReplicateVideoGenProvider())
