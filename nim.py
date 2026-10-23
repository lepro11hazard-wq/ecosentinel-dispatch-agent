"""Shared NVIDIA NIM chat-completions call used by Verification and the Evaluator."""
import json
import urllib.error
import urllib.request

import config


class NIMError(Exception):
    pass


def chat(content, max_tokens=200, temperature=0.1, timeout=config.NIM_TIMEOUT_S):
    """Send one user message and return the model's reply text. Raises NIMError on any failure."""
    if not config.NVIDIA_API_KEY:
        raise NIMError("NVIDIA_API_KEY not set in .env")
    payload = {
        "model": config.NIM_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        config.NIM_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {config.NVIDIA_API_KEY}",
                 "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        raise NIMError(f"NIM HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}") from exc
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        raise NIMError(f"NIM call failed: {exc}") from exc
