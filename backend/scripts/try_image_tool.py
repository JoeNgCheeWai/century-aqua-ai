"""
Try the real image tool once, for real (costs a few cents at most: one low-quality image).

    python scripts/try_image_tool.py "a calm blue hero banner for a shrimp farm website"   (from backend/)

Reads OPENAI_API_KEY (and optionally OPENAI_IMAGE_MODEL) from backend/.env. Prints whether the picture is real or the
placeholder and why, and saves a real picture to image_tool_sample.png in the system temp folder. It never prints the key.
"""

import base64
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import logging  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from app.agents import image_tool  # noqa: E402


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip() or "a calm blue hero banner for an aquaculture technology website, flat illustration"
    result = image_tool.generate_image_detailed(prompt)
    print(f"model requested : {image_tool.image_model()}")
    print(f"real image      : {result.generated}")
    if not result.generated:
        print(f"fell back because: {result.reason}")
        print(f"placeholder url : {result.url}")
        return 1
    header, _, encoded = result.url.partition(",")
    print(f"url kind        : {header if result.url.startswith('data:') else 'https link'}")
    print(f"url length      : {len(result.url):,} characters")
    if result.url.startswith("data:"):
        out = Path(tempfile.gettempdir()) / "image_tool_sample.png"
        out.write_bytes(base64.b64decode(encoded))
        print(f"saved picture   : {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
