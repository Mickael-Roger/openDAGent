from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from . import Tool
from ..ids import new_id


class GenerateImage(Tool):
    name = "generate_image"
    description = (
        "Generate an image from a text prompt using the configured image generation "
        "model (any diffusion model reachable via an OpenAI-compatible /images/generations "
        "endpoint). The image is saved to the task workspace and the file path is returned."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Detailed description of the image to generate.",
            },
            "size": {
                "type": "string",
                "description": "Image dimensions (e.g. '1024x1024'). Default: 1024x1024.",
            },
            "quality": {
                "type": "string",
                "enum": ["standard", "hd"],
                "description": "Image quality hint if supported by the provider. Default: standard.",
            },
            "filename": {
                "type": "string",
                "description": "Optional filename (without extension) for the saved image.",
            },
        },
        "required": ["prompt"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        filename: str | None = None,
        **_: Any,
    ) -> str:
        # Provider configuration via environment variables.
        # Defaults to the OpenAI images endpoint but works with any
        # OpenAI-compatible image generation API (ComfyUI, Automatic1111 API,
        # Together AI, Replicate, etc.).
        api_key   = os.environ.get("IMAGE_GEN_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        endpoint  = os.environ.get(
            "IMAGE_GEN_ENDPOINT",
            "https://api.openai.com/v1/images/generations",
        )
        model     = os.environ.get("IMAGE_GEN_MODEL", "dall-e-3")

        if not api_key:
            return (
                "Error: no API key found. Set IMAGE_GEN_API_KEY (or OPENAI_API_KEY) "
                "and optionally IMAGE_GEN_ENDPOINT / IMAGE_GEN_MODEL."
            )

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
        }

        try:
            resp = httpx.post(endpoint, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:400]
            return f"Error: image generation API HTTP {e.response.status_code}: {body}"
        except Exception as e:
            return f"Error calling image generation API: {e}"

        data = resp.json().get("data", [{}])[0]
        image_url      = data.get("url", "")
        revised_prompt = data.get("revised_prompt", prompt)

        if not image_url:
            return "Error: API returned no image URL."

        # Download the image
        try:
            img_resp = httpx.get(image_url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
        except Exception as e:
            return f"Image generated but download failed: {e}\nURL: {image_url}"

        # Detect format from content-type
        ct  = img_resp.headers.get("content-type", "image/png")
        ext = "webp" if "webp" in ct else ("jpeg" if "jpeg" in ct else "png")

        # Save to workspace
        workspace = task.get("workspace_path")
        if workspace:
            save_dir = Path(workspace) / "generated_images"
        else:
            save_dir = Path("/tmp") / "opendagent_images"
        save_dir.mkdir(parents=True, exist_ok=True)

        stem     = filename or new_id("img")
        out_path = save_dir / f"{stem}.{ext}"
        out_path.write_bytes(image_bytes)

        return (
            f"Image saved to: {out_path}\n"
            f"Model: {model} · Size: {size} · Quality: {quality}\n"
            f"Prompt used: {revised_prompt}"
        )
