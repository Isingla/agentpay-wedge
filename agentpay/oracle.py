"""Verification oracle: decides who was right.

Deterministic checks first (hash match, JSON schema, summary constraints).
An optional judge (e.g. an LLM) can be layered on top, but only ever after the
deterministic checks pass, so a judge can tighten a verdict, never loosen it.

When the oracle is down it raises OracleUnavailable. The escrow treats that,
and any other oracle error, as "no verdict": it neither pays nor refunds.
"""
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Optional


class OracleUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str


_TYPES = {"str": str, "int": int, "float": (int, float), "list": list,
          "dict": dict, "bool": bool}


class Oracle:
    def __init__(self, judge: Optional[Callable[[dict, str], Verdict]] = None):
        self.up = True
        self.judge = judge
        self.calls = 0

    def verify(self, spec: dict, output) -> Verdict:
        self.calls += 1
        if not self.up:
            raise OracleUnavailable("oracle offline")
        v = self._deterministic(spec, output)
        if v.passed and self.judge is not None and spec.get("judge"):
            v = self.judge(spec, output)
        return v

    def _deterministic(self, spec: dict, output) -> Verdict:
        if not isinstance(output, str):
            return Verdict(False, "output_not_text")
        kind = spec.get("kind")

        if kind == "sha256":
            got = hashlib.sha256(output.encode()).hexdigest()
            return Verdict(got == spec["sha256"], "hash_match" if got == spec["sha256"]
                           else "hash_mismatch")

        if kind == "json_schema":
            try:
                obj = json.loads(output)
            except ValueError:
                return Verdict(False, "not_json")
            if not isinstance(obj, dict):
                return Verdict(False, "not_object")
            for key, tname in spec["required"].items():
                if key not in obj:
                    return Verdict(False, f"missing:{key}")
                if not isinstance(obj[key], _TYPES[tname]):
                    return Verdict(False, f"type:{key}")
            return Verdict(True, "schema_match")

        if kind == "summary":
            text = output.strip()
            if not text:
                return Verdict(False, "empty")
            printable = sum(ch.isprintable() for ch in text) / len(text)
            if printable < 0.95:
                return Verdict(False, "non_printable")
            if len(text.split()) > spec.get("max_words", 50):
                return Verdict(False, "too_long")
            low = text.lower()
            for term in spec.get("must_include", []):
                if term.lower() not in low:
                    return Verdict(False, f"missing_term:{term}")
            src = spec.get("source")
            if src and len(text) >= len(src.strip()):
                return Verdict(False, "not_shorter_than_source")
            return Verdict(True, "summary_ok")

        return Verdict(False, f"unknown_spec_kind:{kind}")
