from __future__ import annotations

from memory_core.errors import LLMSchemaError


class ScriptedLLM:
    """Returns scripted replies in order; an Exception entry is raised; running out of script fails loudly."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[tuple[list[dict], dict]] = []

    async def complete(self, messages, schema):
        self.calls.append((messages, schema))
        if not self.script:
            raise AssertionError("LLM called more times than scripted")
        out = self.script.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class AlwaysBadLLM:
    calls = 0

    async def complete(self, messages, schema):
        AlwaysBadLLM.calls += 1
        raise LLMSchemaError("not json")


# adapters for the "config selects auth and parser" test: referenced as "tests.fakes:build_auth" etc.
def build_auth(aux, **kw):
    from memory_core.errors import AuthError
    from memory_core.types import Principal

    class DemoAuth:
        async def resolve(self, headers):
            if headers.get("x-demo") != "yes":
                raise AuthError("no")
            return Principal("demo", "demo-user")

    return DemoAuth()


def build_parser(**kw):
    class DemoParser:
        def parse(self, data, mime):
            return "parsed:" + data.decode()

    return DemoParser()


def char_counter(text):
    return len(text)  # for the "token counter is selectable by config" test
