"""
One place for every AI call the agent makes.

  complete(prompt, max_tokens=1000, model=None, images=None) -> str

Three providers, tried in turn until one answers:
  anthropic  Claude (ANTHROPIC_API_KEY)        - best text, needs paid credit
  groq       open models on Groq (GROQ_API_KEY) - free tier with a small per-minute / per-day token allowance
  gemini     Google Gemini (GEMINI_API_KEY)     - free tier, reads images well

Order: LLM_PROVIDER (one name or a comma list, e.g. "gemini,groq") first, the rest after it. With images, Gemini goes
ahead of Groq. Groq and Gemini each have a list of models (GROQ_MODEL / GEMINI_MODEL first, built-in spares after it):
when one model is rate limited the next is tried, and a limited model is left alone until the time the provider gave
(Retry-After) instead of being hit again on every call.
Anthropic out of credit / key rejected: skipped for 30 min, ONE Telegram note per 6 h.

When nothing answers, complete() raises RuntimeError("all LLM providers failed: ...") and llm.last_failure holds the
time, so a caller can tell "no AI right now - try again later" from "the listing was refused".

images: optional list of (media_type, base64_data) tuples for vision.     python llm.py  -> tests every provider
"""
import logging
import os
import re
import time

import requests

log = logging.getLogger("llm")

PROVIDERS = ("anthropic", "groq", "gemini")
ANTHROPIC_DEFAULT = os.getenv("ANTHROPIC_TEXT_MODEL", "claude-sonnet-4-6")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
_SPARES = {"GROQ_MODEL": "openai/gpt-oss-120b,openai/gpt-oss-20b",
           "GROQ_VISION_MODEL": "qwen/qwen3.8-27b",
           "GEMINI_MODEL": "gemini-3.5-flash,gemini-flash-lite-latest,gemini-3.5-flash-lite"}   # all three answered 11 Nov 2026

last_failure = 0.0                       # when complete() last found NO provider able to answer
_rest_until: dict[str, float] = {}       # "groq:<model>" / "gemini:<model>" / provider -> rate limited until then
_down_until: dict[str, float] = {}       # provider -> out of credit / key rejected until then


class _Busy(Exception):
    """Rate limited or overloaded: the same call can work after `wait` seconds."""

    def __init__(self, msg: str, wait: float):
        super().__init__(msg)
        self.wait = wait


def _models(env: str) -> list[str]:
    """The model named in .env first, then the built-in spares."""
    import config  # noqa: F401  (loads .env)
    out = []
    for m in (os.getenv(env, "") + "," + _SPARES[env]).split(","):
        if m.strip() and m.strip() not in out:
            out.append(m.strip())
    return out


def _key(name: str) -> str:
    import config  # noqa: F401  (loads .env)
    key = os.getenv(name, "")
    if not key:
        raise RuntimeError(f"no {name} configured")
    return key


def _retry_after(r, default: float = 30.0) -> float:
    """How long the provider asks us to stay away, in seconds (2 s .. 15 min)."""
    wait = None
    try:
        wait = float(r.headers.get("retry-after") or "")
    except ValueError:
        m = re.search(r'"retryDelay":\s*"([\d.]+)s"', r.text or "")        # Gemini puts it in the error body
        if m:
            wait = float(m.group(1))
    return min(max(wait if wait is not None else default, 2.0), 900.0)


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


def _each_model(provider: str, models: list[str], call):
    """Try `call(model)` on each model that is not resting. call returns the text, or raises _Busy / anything else."""
    waits, last = [], "no model configured"
    for model in models:
        tag = f"{provider}:{model}"
        left = _rest_until.get(tag, 0) - time.time()
        if left > 0:
            waits.append(left)
            last = f"{model} rate limited for {left:.0f}s more"
            continue
        try:
            try:
                text = call(model)
            except (requests.Timeout, requests.ConnectionError) as e:      # a model that hangs must not be waited on every call
                raise _Busy(f"{model} did not answer ({type(e).__name__})", 600.0)
        except _Busy as e:
            _rest_until[tag] = time.time() + e.wait
            waits.append(e.wait)
            last = str(e)
            log.info(f"{provider} {last} - trying the next model")
            continue
        if text:
            return text
        last = f"{model} gave an empty answer"
    if waits:
        raise _Busy(f"{provider}: {last}", min(waits))
    raise RuntimeError(f"{provider}: {last}")


def _groq(prompt, max_tokens, model, images):
    key = _key("GROQ_API_KEY")
    if images:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}} for mt, b64 in images]
    else:
        content = prompt

    def call(m):
        extra = {"reasoning_effort": "low"} if "gpt-oss" in m else {}
        body = {"model": m, "messages": [{"role": "user", "content": content}], "temperature": 0.3,
                "max_tokens": max(max_tokens, 400) + (600 if extra else 0), **extra}   # reasoning models spend tokens thinking
        for attempt in (1, 2):
            r = requests.post(GROQ_URL, headers={"Authorization": "Bearer " + key}, json=body, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                wait = _retry_after(r)
                if attempt == 1 and wait <= 8:               # a per-minute limit about to clear: wait it out once
                    time.sleep(wait + 0.5)
                    continue
                raise _Busy(f"{m} HTTP {r.status_code}, free again in {wait:.0f}s", wait)
            r.raise_for_status()
            return (r.json()["choices"][0]["message"].get("content") or "").strip()

    return _each_model("groq", _models("GROQ_VISION_MODEL" if images else "GROQ_MODEL"), call)


def _gemini(prompt, max_tokens, model, images):
    key = _key("GEMINI_API_KEY")
    parts = [{"inline_data": {"mime_type": mt, "data": b64}} for mt, b64 in images or []] + [{"text": prompt}]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": max(max_tokens, 400) + GEMINI_THINK_ROOM}}

    def call(m):
        r = requests.post(GEMINI_URL.format(m), headers={"x-goog-api-key": key}, json=body, timeout=45)
        if r.status_code == 429 or r.status_code >= 500:
            wait = _retry_after(r, 60.0)
            raise _Busy(f"{m} HTTP {r.status_code}, free again in {wait:.0f}s", wait)
        if r.status_code == 404:                             # model withdrawn or not offered to this key
            raise _Busy(f"{m} is not available", 900.0)
        r.raise_for_status()
        cand = (r.json().get("candidates") or [{}])[0]
        if cand.get("finishReason") == "MAX_TOKENS":         # cut off mid-answer (its thinking used the allowance): unusable
            return ""
        return "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []
                       if not p.get("thought")).strip()

    return _each_model("gemini", _models("GEMINI_MODEL"), call)


GEMINI_THINK_ROOM = int(os.getenv("GEMINI_THINK_ROOM", "3000"))   # Gemini's own thinking is counted as output tokens
_CALLS = {"anthropic": _anthropic, "groq": _groq, "gemini": _gemini}


def _order(images) -> list[str]:
    import config  # noqa: F401  (loads .env)
    first = [p.strip() for p in os.getenv("LLM_PROVIDER", "").lower().split(",") if p.strip() in PROVIDERS]
    order = list(dict.fromkeys(first)) + [p for p in PROVIDERS if p not in first]
    if images and order.index("gemini") > order.index("groq"):   # Gemini reads photos/screenshots better than Groq's small vision model
        order.remove("gemini")
        order.insert(order.index("groq"), "gemini")
    return order


def complete(prompt: str, max_tokens: int = 1000, model: str | None = None, images=None) -> str:
    global last_failure
    order = _order(images)
    errors: list[str] = []
    for attempt in (1, 2):
        errors, waits = [], []
        for provider in order:
            now = time.time()
            if now < _down_until.get(provider, 0):
                errors.append(f"{provider}: unavailable (no credit / key rejected)")
                continue
            if now < _rest_until.get(provider, 0):
                waits.append(_rest_until[provider] - now)
                errors.append(f"{provider}: rate limited for {_rest_until[provider] - now:.0f}s more")
                continue
            try:
                text = _CALLS[provider](prompt, max_tokens, model, images)
                if not text:
                    raise RuntimeError("empty answer")
                if provider != order[0]:
                    log.info(f"AI answer came from {provider} ({order[0]} unavailable)")
                return text
            except _Busy as e:
                _rest_until[provider] = time.time() + e.wait
                waits.append(e.wait)
                errors.append(str(e)[:140])
                log.warning(f"LLM provider {provider} busy: {str(e)[:160]}")
            except Exception as e:
                msg = str(e)
                errors.append(f"{provider}: {msg[:140]}")
                log.warning(f"LLM provider {provider} failed: {msg[:160]}")
                if provider == "anthropic":
                    if "credit balance" in msg:
                        _down_until[provider] = time.time() + 1800
                        _tell_owner("Anthropic credits are exhausted - the agent is using its free AI providers (Groq, "
                                    "Gemini) instead. Top up at console.anthropic.com > Billing to switch back to Claude.")
                    elif "authentication" in msg.lower() or "invalid x-api-key" in msg.lower():
                        _down_until[provider] = time.time() + 1800
                        _tell_owner("The Anthropic API key was rejected - using Groq / Gemini instead.")
        if attempt == 1 and waits and min(waits) <= 45:      # everyone is rate limited: wait for the first to clear, once
            time.sleep(min(waits) + 0.5)
            continue
        break
    last_failure = time.time()
    raise RuntimeError("all LLM providers failed: " + " | ".join(errors))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    print("order for text:", _order(None), "| with images:", _order([1]))
    for name in PROVIDERS:
        t = time.time()
        try:
            ans = _CALLS[name]('Reply in JSON exactly: {"word": "ok"}', 30, None, None)
            print(f"{name}: OK in {time.time() - t:.1f}s -> {ans[:60]!r}")
        except Exception as e:
            print(f"{name}: FAILED - {str(e)[:220]}")
