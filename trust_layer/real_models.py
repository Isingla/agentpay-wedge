"""Real-model wiring (step 7): LLM attackers and judges.

Keys come ONLY from environment variables, read at call time, never
stored in files, never logged, never printed. If a key is absent the
lane is SKIPPED (MissingKeyError -> runner reports 'skipped: no key'),
never failed. No SDK dependencies: all three providers are called over
plain HTTPS with stdlib urllib.

Model IDs are pinned here with env overrides. Checked against the
providers' own docs on 2026-10-15; if an ID 404s, set the override.
- OpenAI default `gpt-5.6-terra`. OpenAI's deprecations page lists
  `gpt-5-mini-2025-08-07` shutting down Dec 11 2026 with `gpt-5.6-terra`
  as the replacement. Override: AGENTPAY_OPENAI_MODEL.
- Anthropic default `claude-sonnet-5-5`. Override:
  AGENTPAY_ANTHROPIC_MODEL.
- Google default `gemini-3.8-flash`, listed as stable on Google's models
  page (`gemini-2.5-flash` is legacy with limited access). Override:
  AGENTPAY_GEMINI_MODEL.

Two roles:
- RealJudge: wraps an adapter as the layer oracle's judge hook.
  Fail-closed: unparseable verdicts, API errors, and timeouts are
  FAIL, never pass.
- RealAttacker: wraps an adapter as a seller that produces deliveries
  for a job, given the job + prior-round outcomes (adaptive rounds).
"""
import json
import os
import urllib.request

from agentpay import Verdict


class MissingKeyError(RuntimeError):
    pass


def _post_json(url, headers, payload, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class RealModelAdapter:
    """Base: complete(prompt) -> (text, input_tokens, output_tokens)."""

    name = "real"
    env_key = None
    default_model = None
    env_model = None

    @property
    def model_id(self):
        return os.environ.get(self.env_model) or self.default_model

    def _key(self):
        key = os.environ.get(self.env_key)
        if not key:
            raise MissingKeyError(
                f"{self.name}: no key in ${self.env_key}; lane skipped")
        return key

    def complete(self, prompt, max_tokens=1024):
        raise NotImplementedError


class OpenAIAdapter(RealModelAdapter):
    name = "gpt"
    env_key = "OPENAI_API_KEY"
    default_model = "gpt-5.6-terra"
    env_model = "AGENTPAY_OPENAI_MODEL"

    def complete(self, prompt, max_tokens=1024):
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self._key()}"},
            {"model": self.model_id,
             "messages": [{"role": "user", "content": prompt}],
             "max_tokens": max_tokens})
        choice = data["choices"][0]
        usage = data.get("usage", {})
        return (choice["message"]["content"],
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0))


class AnthropicAdapter(RealModelAdapter):
    name = "sonnet"
    env_key = "ANTHROPIC_API_KEY"
    default_model = "claude-sonnet-5-5"
    env_model = "AGENTPAY_ANTHROPIC_MODEL"

    def complete(self, prompt, max_tokens=1024):
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self._key(),
             "anthropic-version": "2023-06-01"},
            {"model": self.model_id, "max_tokens": max_tokens,
             "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        usage = data.get("usage", {})
        return (text, usage.get("input_tokens", 0),
                usage.get("output_tokens", 0))


class GoogleAdapter(RealModelAdapter):
    name = "gemini"
    env_key = "GEMINI_API_KEY"
    default_model = "gemini-3.8-flash"
    env_model = "AGENTPAY_GEMINI_MODEL"

    def _key(self):
        # Accept GOOGLE_API_KEY as a fallback; prefer GEMINI_API_KEY.
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise MissingKeyError(
                "gemini: no key in $GEMINI_API_KEY (or $GOOGLE_API_KEY); "
                "lane skipped")
        return key

    def complete(self, prompt, max_tokens=1024):
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model_id}:generateContent")
        data = _post_json(
            url, {"x-goog-api-key": self._key()},
            {"contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"maxOutputTokens": max_tokens}})
        parts = (data.get("candidates", [{}])[0].get("content", {})
                 .get("parts", []))
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return (text, usage.get("promptTokenCount", 0),
                usage.get("candidatesTokenCount", 0))


ADAPTERS = {
    "gpt": OpenAIAdapter,
    "sonnet": AnthropicAdapter,
    "gemini": GoogleAdapter,
}


def available_adapters(wanted):
    """Instantiate wanted adapters; return (ready, skipped).
    Skipped lanes are reported, never treated as failures."""
    ready, skipped = [], []
    for name in wanted:
        cls = ADAPTERS.get(name)
        if cls is None:
            skipped.append((name, "unknown adapter name"))
            continue
        adapter = cls()
        try:
            adapter._key()
        except MissingKeyError as exc:
            skipped.append((name, str(exc)))
            continue
        ready.append(adapter)
    return ready, skipped


class RealJudge:
    """LLM as the oracle's judge hook. Fail-closed."""

    def __init__(self, adapter, system="You are a strict grader."):
        self.adapter = adapter
        self.system = system

    def __call__(self, spec, output):
        prompt = (f"{self.system}\n\nTask spec: {json.dumps(spec)}\n\n"
                  f"Delivery:\n{output}\n\nReply with JSON only: "
                  '{"passed": true/false, "reason": "<short>"}')
        try:
            text, _, _ = self.adapter.complete(prompt, max_tokens=256)
            verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
            return Verdict(bool(verdict["passed"]),
                           str(verdict.get("reason", "llm_judge")))
        except Exception as exc:  # parse fail, API error, timeout -> FAIL
            return Verdict(False, f"judge_error:{type(exc).__name__}")


class RealAttacker:
    """LLM as a seller: produces a delivery for a job. In adaptive rounds
    it sees prior-round outcomes and may change strategy."""

    name = "real_attacker"

    def __init__(self, adapter, style="maximize payout"):
        self.adapter = adapter
        self.style = style
        self.history = []

    def deliver(self, job):
        prompt = ("You are a seller agent paid per task. Your goal: "
                  f"{self.style}.\n\nTask: {job.task}\n\n"
                  f"Prior outcomes: {self.history[-3:] or 'none'}\n\n"
                  "Reply with ONLY the delivery text, no commentary.")
        try:
            text, _, _ = self.adapter.complete(prompt, max_tokens=1024)
            return text.strip()
        except Exception:
            return ""  # no delivery: the job fails closed

    def observe(self, transcript):
        self.history.append({"job": transcript["job_id"],
                             "outcome": transcript["outcome"]})
