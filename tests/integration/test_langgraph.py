import httpx
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from memory_core.integrations.langgraph import memory_hook, remember_tool
from memory_core.sdk import MemoryClient
from memory_core.tokens import count_tokens


class State(TypedDict, total=False):
    messages: list
    memory_context: str
    prompt: str


async def make_client(c, client_for) -> MemoryClient:
    raw = await client_for("acme")
    return MemoryClient("http://t", raw.headers["Authorization"][7:], transport=httpx.ASGITransport(app=client_for.app))


def build_graph(client, budget=1500):
    scope = lambda: {"subject": "u"}

    async def agent(state: State) -> State:  # stands in for the model call: records what the prompt would contain
        return {"prompt": state["memory_context"] + "\n" + state["messages"][-1]["content"]}

    g = StateGraph(State)
    g.add_node("memory", memory_hook(client, scope, budget))
    g.add_node("agent", agent)
    g.add_edge(START, "memory")
    g.add_edge("memory", "agent")
    g.add_edge("agent", END)
    return g.compile()


async def test_fact_from_chat_one_is_in_the_prompt_of_chat_two(c, client_for):
    client = await make_client(c, client_for)
    tool = remember_tool(client, lambda: {"subject": "u"})
    assert tool.name == "remember"
    assert await tool.ainvoke({"content": "Budget is 50k"}) == "saved"
    await c.queue.join()
    out = await build_graph(client).ainvoke({"messages": [{"role": "user", "content": "what is the budget"}]})
    assert "50k" in out["prompt"]


async def test_hook_respects_the_token_budget(c, client_for):
    client = await make_client(c, client_for)
    await client.add(". ".join(f"Metric{i} is value{i}" for i in range(30)), subject="u")
    await c.queue.join()
    out = await build_graph(client, budget=90).ainvoke({"messages": [{"role": "user", "content": "metric1 value1"}]})
    assert count_tokens(out["memory_context"]) <= 90


async def test_hook_failure_degrades_to_empty_context_not_a_crash(c):
    bad = MemoryClient("http://t", "mc_wrong", transport=httpx.ASGITransport(app=__import__("memory_core.api.app", fromlist=["x"]).create_app(c, False)))
    out = await build_graph(bad).ainvoke({"messages": [{"role": "user", "content": "hi"}]})
    assert out["memory_context"] == "" and out["prompt"].endswith("hi")
