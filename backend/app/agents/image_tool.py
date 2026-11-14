"""
The CMO's image tool: generate a mockup with the OpenAI Images API, store it in Vercel Blob, return its public https URL.

    generate_image(prompt)           -> str          the Vercel Blob URL of the picture, or the placeholder URL
    generate_image_detailed(prompt)  -> ImageResult  the same, plus whether it is real and why it was not
    IMAGE_TOOL_SPEC / tool_result_text(...)          how the CMO is offered the tool and told what it returned

THE FLOW, in order, and it stops at the first thing that is missing or fails:
  1. a prompt, OPENAI_API_KEY and BLOB_READ_WRITE_TOKEN must all be present. The Blob token is checked BEFORE the image is
     generated, so the app never pays for a picture it has nowhere to store;
  2. POST /v1/images/generations (default model gpt-image-1-mini, OPENAI_IMAGE_MODEL changes it). DALL-E 3 was shut down on
     May 12, 2026, and the GPT Image models answer with base64, never a hosted link, so the bytes come back to us;
  3. the bytes are checked to really be a PNG, JPEG or WebP, then uploaded to a PUBLIC Vercel Blob store (vercel-blob);
  4. the URL Blob returns is checked (https, on blob.vercel-storage.com) and returned.

NEVER RAISES, NEVER CRASHES THE APP. Any failure logs ONE warning (the reason only) and returns the labelled placeholder
(``tools.mock_generate_image``). Nothing is retried: an image costs money, so a failure is reported, not repeated. Neither the
OpenAI key nor the Blob token is ever logged or returned, and neither are the prompt or the vendors' error text.

BLOCKING. Both vendor calls are synchronous. Async callers (the CMO) run this in a worker thread (asyncio.to_thread).
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from app.agents.tools import mock_generate_image

logger = logging.getLogger(__name__)

IMAGES_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_IMAGE_MODEL = "gpt-image-1-mini"
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "low"  # the cheapest tier: a mockup for a human to look at, not a final asset
TIMEOUT_SECONDS = 60  # a live call took about 8 seconds; the CMO may make 2 calls inside the console's 170-second limit
MAX_PROMPT_CHARS = 1000
USER_AGENT = "century-aqua-ai-image-tool"

BLOB_TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"
BLOB_HOST_SUFFIX = ".blob.vercel-storage.com"
BLOB_FOLDER = "cmo-mockups"
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@dataclass(frozen=True)
class ImageResult:
    url: str  # the Vercel Blob https URL (generated) or a placehold.co URL (fallback)
    generated: bool  # True only when OpenAI made the picture AND it is stored in Vercel Blob
    reason: str = ""  # why it fell back, in a few safe words (empty when generated)
    model: str = ""  # the model that made it (empty for the placeholder)


def image_model() -> str:
    """The model to use, read at call time so a deploy-time change takes effect."""
    return os.getenv("OPENAI_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL


def readiness() -> dict[str, bool]:
    """Whether the two keys the tool needs are set. Booleans only: a value is never returned, so this is safe to log."""
    return {"openai_key": bool(os.getenv("OPENAI_API_KEY", "").strip()), "blob_token": bool(os.getenv(BLOB_TOKEN_ENV, "").strip())}


def is_blob_url(value: Any) -> bool:
    """True for a plain https address on Vercel Blob's host (no credentials, no odd port, no spaces)."""
    if not isinstance(value, str) or " " in value or len(value) > 300:
        return False
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        return (parsed.scheme == "https" and host.endswith(BLOB_HOST_SUFFIX) and len(host) > len(BLOB_HOST_SUFFIX)
                and parsed.username is None and parsed.password is None and parsed.port in (None, 443))
    except ValueError:
        return False


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


def _decode_picture(item: Any) -> tuple[bytes, str] | str:
    """(image bytes, mime type) from one ``data`` entry of the API reply, or a short reason it is unusable."""
    encoded = item.get("b64_json") if isinstance(item, dict) else None
    if not isinstance(encoded, str) or not encoded.strip():
        return "the reply held no image data"
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError):
        return "the image data was not valid base64"
    mime = _sniff_mime(raw)
    return (raw, mime) if mime else "the image data was not a PNG, JPEG or WebP picture"


def _upload(raw: bytes, mime: str) -> str:
    """Store the picture in the public Vercel Blob store. Returns its https URL, or a reason that starts with 'FAIL:'."""
    try:
        from vercel.blob import sync as vercel_blob  # imported here so the backend starts even without the package
    except ImportError:
        return "FAIL:the vercel-blob package is not installed"
    pathname = f"{BLOB_FOLDER}/{uuid.uuid4().hex}.{_EXTENSIONS[mime]}"
    try:
        # The SDK reads BLOB_READ_WRITE_TOKEN itself. The store must be PUBLIC, or the upload is refused and we fall back.
        stored = vercel_blob.put_blob(pathname, raw, access="public", content_type=mime, add_random_suffix=True)
    except Exception as exc:  # BlobError subclasses, network errors, a bad token: log the type only
        return f"FAIL:the upload to Vercel Blob failed ({type(exc).__name__})"
    url = getattr(stored, "url", None)
    if not is_blob_url(url):
        return "FAIL:Vercel Blob did not return a usable https address"
    return url


def generate_image_detailed(prompt: str) -> ImageResult:
    """Generate one image for ``prompt``, store it in Vercel Blob, return its URL. Falls back to the placeholder on ANY problem."""
    try:
        if not isinstance(prompt, str) or not prompt.strip():
            return _fallback(prompt, "the prompt was empty")
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            return _fallback(prompt, "OPENAI_API_KEY is not set")
        if not os.getenv(BLOB_TOKEN_ENV, "").strip():
            # Checked BEFORE generating: an image with nowhere to be stored would be paid for and thrown away.
            return _fallback(prompt, f"{BLOB_TOKEN_ENV} is not set")

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
            # Only the status code: the error body can echo the prompt back.
            return _fallback(prompt, f"OpenAI answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return _fallback(prompt, "OpenAI's reply was not JSON")
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return _fallback(prompt, "OpenAI's reply held no image")
        decoded = _decode_picture(items[0])
        if isinstance(decoded, str):
            return _fallback(prompt, decoded)

        stored = _upload(*decoded)
        if stored.startswith("FAIL:"):
            return _fallback(prompt, stored[len("FAIL:"):])
        return ImageResult(stored, True, "", model)
    except Exception as exc:  # a tool must never take the app down
        return _fallback(prompt, f"unexpected error ({type(exc).__name__})")


def generate_image(prompt: str) -> str:
    """The Vercel Blob https URL of a generated image for ``prompt``, or the placeholder URL if it cannot be made and stored."""
    return generate_image_detailed(prompt).url


# ------------------------------------------------------------------------------------------------ offered to the CMO
# The shape Anthropic's Messages API takes for a tool. The CMO is offered ONLY this tool.
IMAGE_TOOL_NAME = "generate_image"
IMAGE_TOOL_SPEC: dict[str, Any] = {
    "name": IMAGE_TOOL_NAME,
    "description": (
        "Generate one AI mockup image from a short visual description and get back its web address. Use it when asked for a "
        "mockup of a page, banner or visual. It costs money, so call it only when a picture is wanted, once per distinct "
        "image. The result is an AI-generated draft for a human to judge, not a finished design."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What the image shows: the subject, setting, composition, colours and style. Do not include text to be drawn in the image, a real person's name, or any secret.",
            }
        },
        "required": ["prompt"],
    },
}


def tool_result_text(result: ImageResult) -> str:
    """What the CMO is told the tool returned. It carries the address to use as a mockup and says honestly if it is a placeholder."""
    if result.generated:
        return f"Image generated. Use exactly this address in mockup_urls: {result.url}"
    return (
        f"No image could be generated ({result.reason}). This is a labelled placeholder, NOT the requested image: "
        f"{result.url}. You may list it in mockup_urls but you must say it is a placeholder."
    )
