"""
One place for every AI call the agent makes.

  complete(prompt, max_tokens=1000, model=None, images=None) -> str

Tries Anthropic (Claude) first. If Anthropic is unavailable - out of credit, bad key, rate limited, overloaded, network -
it automatically falls back to Groq (OpenAI-compatible API; GROQ_API_KEY in .env) so listing text, discovery, screenshot
reading etc. keep working. After an out-of-credit error Anthropic is skipped for 30 min (no failing call each time) and the
owner gets ONE Telegram note per 6 h. Set LLM_PROVIDER=groq to use Groq first (saves Anthropic credit).

images: optional list of (media_type, base64_data) tuples for vision.
"""
import logging
import os
import time

import requests

log = logging.getLogger("llm")

ANTHROPIC_DEFAULT = os.getenv("ANTHROPIC_TEXT_MODEL", "claude-sonnet-4-6")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TEXT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")

_anthropic_skip_until = 0.0


def _tell_owner(msg: str):
    """At most one Telegram note per 6 hours."""
    try:
        stamp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".llm_alert")
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 6 * 3600:
            return
        open(stamp, "w").write("1")
        from telegram_bot import _send
        _send(msg, silent=True)
    except Exception:
        pass


def _anthropic(prompt, max_tokens, model, images):
    import anthropic
    from config import ANTHROPIC_API_KEY
    if images:
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}} for mt, b64 in images]
        content.append({"type": "text", "text": prompt})
    else:
        content = prompt
    msg = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=90).messages.create(
        model=model or ANTHROPIC_DEFAULT, max_tokens=max_tokens, messages=[{"role": "user", "content": content}])
    return msg.content[0].text


def _groq(prompt, max_tokens, images):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("no GROQ_API_KEY configured")
    if images:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}} for mt, b64 in images]
        model, extra = GROQ_VISION_MODEL, {}
    else:
        content, model = prompt, GROQ_TEXT_MODEL
        extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
    body = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0.3,
            "max_tokens": max(max_tokens, 400) + (600 if extra else 0), **extra}   # reasoning models spend tokens thinking
    last = None
    for attempt in range(3):
        r = requests.post(GROQ_URL, headers={"Authorization": "Bearer " + key}, json=body, timeout=90)
        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()
    raise RuntimeError(f"Groq failed: {last}")


def complete(prompt: str, max_tokens: int = 1000, model: str | None = None, images=None) -> str:
    global _anthropic_skip_until
    order = ["groq", "anthropic"] if os.getenv("LLM_PROVIDER", "").lower() == "groq" else ["anthropic", "groq"]
    errors = []
    for provider in order:
        if provider == "anthropic" and time.time() < _anthropic_skip_until:
            continue
        try:
            text = _anthropic(prompt, max_tokens, model, images) if provider == "anthropic" else _groq(prompt, max_tokens, images)
            if provider == "groq" and order[0] == "anthropic":
                log.warning("Anthropic unavailable - answered by Groq instead")
            return text
        except Exception as e:
            msg = str(e)
            errors.append(f"{provider}: {msg[:140]}")
            log.warning(f"LLM provider {provider} failed: {msg[:160]}")
            if provider == "anthropic":
                if "credit balance" in msg:
                    _anthropic_skip_until = time.time() + 1800
                    _tell_owner("Anthropic credits are exhausted - the agent is now using Groq as its AI provider. "
                                "Top up at console.anthropic.com > Billing to switch back to Claude.")
                elif "authentication" in msg.lower() or "invalid x-api-key" in msg.lower():
                    _anthropic_skip_until = time.time() + 1800
                    _tell_owner("The Anthropic API key was rejected - using Groq instead.")
    raise RuntimeError("all LLM providers failed: " + " | ".join(errors))
