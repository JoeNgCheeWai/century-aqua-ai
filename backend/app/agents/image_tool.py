"""
A real image generator for the CMO's mockups (OpenAI Images API), with a safe fall-back to the placeholder.

NOT WIRED INTO ANY AGENT YET. Nothing imports this module except its tests and scripts/try_image_tool.py. It is deliberately
NOT in ``tools.TOOLS`` and not in tools.py: that file is the audited, protected tool router and its tests pin its exact tool
list. Wiring this into the CMO (the prompt, the ``mockup_urls`` validator in cmo_authorization.py, the approval screen) is a
separate step. See "WHAT WIRING IT IN WILL NEED" below.

    generate_image(prompt)           -> str          a ``data:image/...;base64,...`` URI, or the placeholder URL
    generate_image_detailed(prompt)  -> ImageResult  the same, plus whether it is real and why it was not

WHICH MODEL. DALL-E 3 was shut down on May 12, 2026, so this uses a GPT Image model. The default is ``gpt-image-1-mini`` and
OPENAI_IMAGE_MODEL changes it. These models return the picture as base64, never as a hosted URL (``response_format`` is not
accepted for them, so it is not sent). That is why the result is a data: URI: a browser ``<img src>`` renders it with no
storage and no second request.

NEVER RAISES, NEVER CRASHES THE APP. A missing key, an empty prompt, a network error, a timeout, an API error or a reply that
is not an image all log ONE warning (the reason only) and return ``tools.mock_generate_image(prompt)``. Nothing is retried: an
image costs money, so a failure is reported, not repeated. The key is read at call time, sent only in the Authorization header,
and never logged or returned. Neither is the prompt, nor the API's error text (which can echo the prompt back).

WHAT WIRING IT IN WILL NEED (not done here, on purpose):
  * A data: URI is large (a 1024x1024 PNG is roughly 1-2 MB of text). cmo_authorization.clean_mockup_urls accepts only
    https://placehold.co/ addresses up to 300 characters, so it would drop it. The authorization JSON is stored in Postgres,
    and the frontend renders ``mockup_urls`` directly, so a data: URI there bloats every audit row. The plan is to upload the
    image to S3 or Cloudinary and keep only that short https URL in the request.
  * Anything that is not a placeholder must stop being labelled "placeholder, not a real design" (chat_style.CMO_UI_PROMPT,
    cmo_authorization.audit_summary, AuthorizationDetail.tsx). A generated image is a draft for a human to judge, not a
    finished design, and not approved brand material.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from app.agents.tools import mock_generate_image

logger = logging.getLogger(__name__)

IMAGES_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_IMAGE_MODEL = "gpt-image-1-mini"
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "low"  # the cheapest tier: this is a mockup for a human to look at, not a final asset
TIMEOUT_SECONDS = 120  # image generation is slow (tens of seconds is normal)
MAX_PROMPT_CHARS = 1000
USER_AGENT = "century-aqua-ai-image-tool"


@dataclass(frozen=True)
class ImageResult:
    url: str  # a data: URI (generated) or a placehold.co URL (fallback)
    generated: bool  # True only when the OpenAI API produced the picture
    reason: str = ""  # why it fell back, in a few safe words (empty when generated)
    model: str = ""  # the model that made it (empty for the placeholder)


def image_model() -> str:
    """The model to use, read at call time so a deploy-time change takes effect."""
    return os.getenv("OPENAI_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL


def _sniff_mime(data: bytes) -> str | None:
    """The image type from the file's first bytes, or None if it is not a PNG, JPEG or WebP."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _fallback(prompt: Any, reason: str) -> ImageResult:
    logger.warning("image_tool: %s; using the placeholder image", reason)
    return ImageResult(mock_generate_image(prompt if isinstance(prompt, str) else ""), False, reason)


def _picture_from(item: Any, model: str) -> ImageResult | str:
    """The ImageResult for one ``data`` entry of the API reply, or a short reason it is unusable."""
    if not isinstance(item, dict):
        return "the reply had no image entry"
    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded.strip():
        try:
            raw = base64.b64decode(encoded.strip(), validate=True)
        except (binascii.Error, ValueError):
            return "the image data was not valid base64"
        mime = _sniff_mime(raw)
        if mime is None:
            return "the image data was not a PNG, JPEG or WebP picture"
        return ImageResult(f"data:{mime};base64,{encoded.strip()}", True, "", model)
    link = item.get("url")
    # Older image models answered with a hosted link. Only a plain https address is passed on.
    if isinstance(link, str) and link.startswith("https://") and " " not in link:
        return ImageResult(link, True, "", model)
    return "the reply held neither image data nor an https link"


def generate_image_detailed(prompt: str) -> ImageResult:
    """Generate one image for ``prompt``. Falls back to the placeholder on ANY problem. Never raises."""
    try:
        if not isinstance(prompt, str) or not prompt.strip():
            return _fallback(prompt, "the prompt was empty")
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            return _fallback(prompt, "OPENAI_API_KEY is not set")

        model = image_model()
        body = {"model": model, "prompt": prompt.strip()[:MAX_PROMPT_CHARS], "n": 1, "size": IMAGE_SIZE, "quality": IMAGE_QUALITY}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            response = requests.post(IMAGES_URL, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
        except requests.Timeout:
            return _fallback(prompt, f"the image request timed out after {TIMEOUT_SECONDS}s")
        except requests.RequestException as exc:
            return _fallback(prompt, f"could not reach OpenAI ({type(exc).__name__})")

        if response.status_code != 200:
            # Only the status code: the error body can echo the prompt back, and must never carry anything secret into a log.
            return _fallback(prompt, f"OpenAI answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return _fallback(prompt, "OpenAI's reply was not JSON")
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return _fallback(prompt, "OpenAI's reply held no image")
        picture = _picture_from(items[0], model)
        if isinstance(picture, str):
            return _fallback(prompt, picture)
        return picture
    except Exception as exc:  # a tool must never take the app down
        return _fallback(prompt, f"unexpected error ({type(exc).__name__})")


def generate_image(prompt: str) -> str:
    """The image for ``prompt`` as a ``data:image/...;base64,...`` URI, or the placeholder URL if it cannot be generated."""
    return generate_image_detailed(prompt).url
