"""Minimal Gemini client using the generateContent REST endpoint.

Only the standard library is used, so no SDK version can break it. The
model must answer in JSON; the agent parses that JSON itself.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

API_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "{model}:generateContent")
# Tried in order until one works for this API key. GEMINI_MODEL in .env,
# if set, is tried first.
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                  "gemini-3.5-flash", "gemini-flash-latest",
                  "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
ATTEMPTS_PER_MODEL = 3
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(Exception):
    pass


class Gemini:
    """Sends a system prompt and a conversation, returns parsed JSON."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is missing from .env")
        preferred = model or os.environ.get("GEMINI_MODEL")
        self.models = ([preferred] if preferred else []) + [
            m for m in DEFAULT_MODELS if m != preferred]
        self.model = None

    def _post(self, model, body):
        request = urllib.request.Request(
            API_URL.format(model=model),
            data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": self.api_key,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    def complete_json(self, system, messages):
        """messages: list of (role, text) with role 'user' or 'model'."""
        contents = [{"role": role, "parts": [{"text": text}]}
                    for role, text in messages]
        # A model that already worked is tried first.
        order = ([self.model] if self.model else []) + [
            m for m in self.models if m != self.model]
        errors = []
        for model in order:
            for attempt in range(ATTEMPTS_PER_MODEL):
                body = {"systemInstruction": {"parts": [{"text": system}]},
                        "contents": contents}
                # JSON mode on the first try. Some models return an empty
                # MALFORMED_RESPONSE in JSON mode, so retries use plain text
                # and the JSON object is cut out of the reply.
                if attempt == 0:
                    body["generationConfig"] = {
                        "responseMimeType": "application/json"}
                try:
                    reply = self._parse(self._post(model, body))
                    self.model = model
                    return reply
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")
                    errors.append(f"{model}: HTTP {e.code} "
                                  f"{_short_message(detail)}")
                    # 429 is the free-tier rate limit, 500 and 503 mean the
                    # model is overloaded. All of them pass, so wait and retry.
                    if e.code in (429, 500, 503):
                        time.sleep(10 * (attempt + 1))
                        continue
                    break  # 400 or 404: this model will not work.
                except (LLMError, urllib.error.URLError, TimeoutError) as e:
                    errors.append(f"{model}: {e}")
                    time.sleep(2)
        raise LLMError("No Gemini model worked:\n  " + "\n  ".join(errors))

    @staticmethod
    def _parse(data):
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise LLMError(
                f"empty reply ({candidate.get('finishReason', 'unknown')})")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = JSON_OBJECT.search(text)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        raise LLMError(f"reply was not JSON: {text[:150]}")


def _short_message(detail):
    """Pulls the error message out of a Gemini error body."""
    try:
        return json.loads(detail)["error"]["message"][:200]
    except (ValueError, KeyError, TypeError):
        return detail[:200]