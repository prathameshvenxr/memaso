"""Core must depend on ports only: no adapter modules and no vendor SDKs, in any core file (round-1 review)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "memory_core"
CORE = [*ROOT.glob("ingest/*.py"), *ROOT.glob("retrieval/*.py"), *ROOT.glob("manage/*.py"), *ROOT.glob("api/*.py"),
        ROOT / "container.py", ROOT / "metering.py", ROOT / "mcp_server.py", ROOT / "serialize.py",
        ROOT / "ports.py", ROOT / "types.py", ROOT / "config.py"]
BANNED = re.compile(r"^\s*(from|import)\s+(\.+adapters|memory_core\.adapters|anthropic|openai|redis|asyncpg|httpx)", re.M)


def test_no_core_file_imports_an_adapter_or_vendor_sdk():
    assert len(CORE) > 20
    bad = [f.name for f in CORE if BANNED.search(f.read_text())]
    assert bad == []


def test_bootstrap_is_the_only_core_module_naming_concrete_adapters():
    named = [f.name for f in ROOT.glob("*.py") if re.search(r"adapters\.(pg_|llm_|embed_|queue_)", f.read_text())]
    assert sorted(named) == ["bootstrap.py"]
