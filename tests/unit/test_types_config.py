import pytest

from memory_core.config import load_config
from memory_core.types import Scope


def test_scope_is_hashable_and_requires_tenant_and_subject():
    assert len({Scope("t", "s"), Scope("t", "s")}) == 1
    with pytest.raises(ValueError):
        Scope("", "s")
    with pytest.raises(ValueError):
        Scope("t", "  ")


def test_config_env_overrides_yaml(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("llm: {adapter: openai, model: m1}\nqueue: {workers: 5}\n")
    cfg = load_config(str(f), {"MEMASO__LLM__MODEL": "m2", "MEMASO__QUEUE__WORKERS": "7"})
    assert cfg.llm == {"adapter": "openai", "model": "m2"}
    assert cfg.queue["workers"] == 7


def test_unknown_adapter_name_raises_and_lists_valid_names():
    with pytest.raises(ValueError, match="anthropic.*openai.*compat.*fake"):
        load_config(None, {"MEMASO__LLM__ADAPTER": "nope"})
