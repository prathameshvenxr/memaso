import json

import httpx
import pytest

from memory_core.mcp_server import create_mcp
from memory_core.sdk import MemoryClient
from memory_core.types import Principal


def payload(result) -> dict:
    return result.structured_content if getattr(result, "structured_content", None) else json.loads(result.content[0].text)


async def test_mcp_lists_exactly_four_tools_and_none_take_a_tenant(c):
    mcp = create_mcp(c, Principal("acme", "u"))
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {"memory_add", "memory_search", "memory_profile", "memory_forget"}
    for t in tools:
        assert not {"tenant", "subject", "tenant_id"} & set(t.input_schema.get("properties", {}))


async def test_mcp_add_search_forget_round_trip(c):
    mcp = create_mcp(c, Principal("acme", "u"))
    added = payload(await mcp.call_tool("memory_add", {"content": "Budget is 50k"}))
    assert added["status"] == "queued"
    await c.queue.join()
    res = payload(await mcp.call_tool("memory_search", {"query": "budget"}))
    assert "50k" in res["context"]
    mid = res["results"][0]["id"]
    assert payload(await mcp.call_tool("memory_forget", {"memory_id": mid}))["deleted"] == 1
    assert payload(await mcp.call_tool("memory_search", {"query": "budget"}))["results"] == []


async def test_mcp_cannot_forget_another_subjects_or_tenants_memory(c):
    mine, theirs = create_mcp(c, Principal("acme", "u")), create_mcp(c, Principal("globex", "u"))
    await mine.call_tool("memory_add", {"content": "Budget is 50k"})
    await c.queue.join()
    mid = payload(await mine.call_tool("memory_search", {"query": "budget"}))["results"][0]["id"]
    assert payload(await theirs.call_tool("memory_forget", {"memory_id": mid}))["deleted"] == 0
    assert payload(await create_mcp(c, Principal("acme", "other")).call_tool("memory_forget", {"memory_id": mid}))["deleted"] == 0


def test_mcp_needs_a_subject(c):
    with pytest.raises(ValueError):
        create_mcp(c, Principal("acme", None))


async def test_sdk_round_trip_against_the_app(c, client_for):
    raw = await client_for("acme")
    sdk = MemoryClient("http://t", raw.headers["Authorization"][7:], subject="u",
                       transport=httpx.ASGITransport(app=client_for.app))
    doc = await sdk.add("Budget is 50k")
    await c.queue.join()
    assert (await sdk.document(doc["id"]))["status"] == "indexed"
    res = await sdk.search("budget")
    assert res.results[0].memory.scope.tenant == "acme" and "50k" in res.context
    assert (await sdk.profile()).context
    assert len((await sdk.export())["memories"]) == 1
    receipt = await sdk.forget(subject="u")
    assert receipt.counts["memories"] == 1
    await sdk.aclose()
