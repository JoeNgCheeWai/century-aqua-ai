"""
Live diagnostic for the internal-only AI_CAO endpoint (POST /api/cao).

Prints the HTTP status code and the RAW response body no matter what the
server returns (200, 401, 422, 502, 503 ...), which PowerShell's
Invoke-RestMethod hides behind a generic WebException.

The key is read from your local .env (backend/.env first, then ./.env) and is
never printed. HTTP uses only the standard library, so this runs with the
backend venv (which has python-dotenv but not requests/httpx) as well as with
a system Python.

Usage (from the project root):
    backend\\venv\\Scripts\\python.exe test_cao_live.py
    backend\\venv\\Scripts\\python.exe test_cao_live.py "Your own test message"

Optional environment overrides:
    CAO_TEST_URL      base URL (default: the Render backend)
    INTERNAL_CAO_KEY  used instead of the .env files if set in the shell
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.getenv("CAO_TEST_URL", "https://caip-backend-wr3i.onrender.com").rstrip("/")
ROOT = Path(__file__).resolve().parent
ENV_FILES = [ROOT / "backend" / ".env", ROOT / ".env"]
KEY_VAR = "INTERNAL_CAO_KEY"
HEADER_NAME = "X-Internal-CAO-Key"
DEFAULT_MESSAGE = (
    "In two sentences, what is the strategic role of a Chief Aquaculture Officer?"
)
TIMEOUT_SECONDS = 180  # Render's free tier can take 30-60s to wake up

# Cloudflare sits in front of Render and may reject the default Python user agent.
USER_AGENT = "test_cao_live/1.0 (diagnostic script)"

# Windows consoles often use a legacy code page; never crash while printing a reply.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def read_env_file(path: Path) -> dict:
    """Read KEY=VALUE pairs from a .env file (python-dotenv if available)."""
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except ImportError:
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
        return values


def find_key():
    """Return (key, source description). The shell variable wins over files."""
    shell_value = os.getenv(KEY_VAR, "").strip()
    if shell_value:
        return shell_value, "shell environment variable"
    for path in ENV_FILES:
        value = read_env_file(path).get(KEY_VAR, "").strip()
        if value:
            return value, str(path)
    return "", ""


def call(method, url, headers=None, body=None):
    """Return (status, headers, raw_text, seconds). Never raises for HTTP errors."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, dict(response.headers), raw, time.time() - started
    except urllib.error.HTTPError as error:
        # 4xx / 5xx: urllib raises, but the body is exactly what we want to see.
        raw = error.read().decode("utf-8", errors="replace")
        return error.code, dict(error.headers), raw, time.time() - started


def explain(status):
    hints = {
        200: "Success: the endpoint, the key, the model, and the Anthropic call all work.",
        401: f"The server has {KEY_VAR} set, but it does not match the key sent. "
        "Check for typos, extra spaces, or quotes in the Render value.",
        403: "Blocked before reaching the app (possibly Cloudflare or a proxy rule).",
        404: "Route not found: the deployed backend does not have /api/cao yet. "
        "Check that the latest commit deployed on Render.",
        422: "The server rejected the request body. Check the JSON fields.",
        502: "Key accepted, but the AI_CAO call failed. Likely a wrong CAO_MODEL name, "
        "an Anthropic API error, or a quota problem. See the Render logs for 'ERROR IN AI_CAO'.",
        503: f"The endpoint is disabled because {KEY_VAR} is NOT set on the Render service. "
        "Add it in Render > Environment, save, and let it redeploy.",
    }
    return hints.get(status, "Unexpected status. See the raw response above and the Render logs.")


def main():
    message = " ".join(sys.argv[1:]).strip() or DEFAULT_MESSAGE

    print("=" * 64)
    print("AI_CAO LIVE DIAGNOSTIC")
    print("=" * 64)
    print(f"Target URL : {BASE_URL}/api/cao")

    key, source = find_key()
    if key:
        print(f"{KEY_VAR}: found in {source} (length {len(key)}, value hidden)")
    else:
        print(f"{KEY_VAR}: NOT FOUND in the shell, {ENV_FILES[0]}, or {ENV_FILES[1]}")
        print("          The request below will be sent WITHOUT the key header,")
        print("          so a 401 or 503 is expected. Add the key to backend/.env to test fully.")

    # Step 1: wake the free-tier server and confirm it is reachable.
    print("\n--- Step 1: GET /api/health (wakes the server) ---")
    try:
        status, _, raw, seconds = call("GET", f"{BASE_URL}/api/health")
    except Exception as error:
        print(f"Could not reach the server: {type(error).__name__}: {error}")
        return 2
    print(f"HTTP status : {status}  ({seconds:.1f}s)")
    print(f"Raw response: {raw}")

    # Step 2: the actual /api/cao request.
    print("\n--- Step 2: POST /api/cao ---")
    print(f"Message     : {message}")
    headers = {HEADER_NAME: key} if key else {}
    try:
        status, response_headers, raw, seconds = call(
            "POST",
            f"{BASE_URL}/api/cao",
            headers=headers,
            body={"message": message, "history": []},
        )
    except Exception as error:
        print(f"Request failed before a response arrived: {type(error).__name__}: {error}")
        print("A timeout here usually means a long cold start or a long model reply.")
        return 2

    print(f"HTTP status : {status}  ({seconds:.1f}s)")
    interesting = ("content-type", "server", "cf-ray", "rndr-id")
    for name, value in response_headers.items():
        if name.lower() in interesting:
            print(f"Header      : {name}: {value}")
    print("Raw response text:")
    print(raw if raw else "(empty body)")

    print("\n--- Diagnosis ---")
    print(explain(status))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
