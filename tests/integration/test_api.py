import random

from memory_core.api.limits import RateLimiter
from memory_core.metering import current_tenant


async def drain(c):
    await c.queue.join()


async def test_openapi_schema_is_served(client_for):
    cl = await client_for("acme")
    spec = (await cl.get("/openapi.json")).json()
    assert "/v1/search" in spec["paths"] and "/v1/forget" in spec["paths"]
    assert (await cl.get("/healthz")).json() == {"ok": True}


async def test_full_flow_add_status_search_edit_export_delete(c, client_for):
    cl = await client_for("acme")
    doc = (await cl.post("/v1/memories", json={"content": "Budget is 50k", "subject": "u"})).json()
    assert doc["status"] == "queued"
    await drain(c)
    assert (await cl.get(f"/v1/documents/{doc['id']}")).json()["status"] == "indexed"
    hit = (await cl.post("/v1/search", json={"query": "budget", "subject": "u"})).json()["results"][0]
    edited = (await cl.patch(f"/v1/memories/{hit['id']}", json={"text": "Budget is 55k"})).json()
    assert edited["supersedes"] == [hit["id"]]
    assert (await cl.patch(f"/v1/memories/{hit['id']}", json={"text": "x"})).status_code == 409
    export = (await cl.get("/v1/export", params={"subject": "u"})).json()
    assert len(export["memories"]) == 2
    assert (await cl.delete(f"/v1/memories/{edited['id']}")).json()["counts"]["memories"] == 1
    r = await cl.post("/v1/forget", json={"subject": "u"})
    assert r.status_code == 200 and r.json()["counts"]["memories"] == 1


async def test_subject_is_required_unless_the_key_is_bound(client_for):
    cl = await client_for("acme")
    assert (await cl.get("/v1/profile")).status_code == 422
    bound = await client_for("acme", subject="alice")
    assert (await bound.get("/v1/profile")).status_code == 200
    assert (await bound.get("/v1/profile", params={"subject": "bob"})).status_code == 403
    assert (await bound.post("/v1/forget", json={"everything": True})).status_code == 403


async def test_bound_key_cannot_read_another_subjects_memory(c, client_for):
    admin_key = await client_for("acme")
    await admin_key.post("/v1/memories", json={"content": "Budget is 50k", "subject": "bob"})
    await drain(c)
    mid = (await admin_key.post("/v1/search", json={"query": "budget", "subject": "bob"})).json()["results"][0]["id"]
    alice = await client_for("acme", subject="alice")
    assert (await alice.get(f"/v1/memories/{mid}")).status_code == 404
    assert (await alice.delete(f"/v1/memories/{mid}")).status_code == 404


async def test_rate_limit_returns_429_with_retry_after(client_for):
    cl = await client_for("acme")
    client_for.app.state.limiter = RateLimiter(2)
    codes = [(await cl.get("/v1/profile", params={"subject": "u"})) for _ in range(4)]
    assert [r.status_code for r in codes][:2] == [200, 200]
    assert codes[-1].status_code == 429 and int(codes[-1].headers["Retry-After"]) >= 1


async def test_rate_limit_is_per_tenant(client_for):
    a, b = await client_for("a"), await client_for("b")
    client_for.app.state.limiter = RateLimiter(1)
    assert (await a.get("/v1/profile", params={"subject": "u"})).status_code == 200
    assert (await a.get("/v1/profile", params={"subject": "u"})).status_code == 429
    assert (await b.get("/v1/profile", params={"subject": "u"})).status_code == 200


async def test_budget_cap_returns_429_once_spent(c, client_for):
    cl = await client_for("acme", budget=50)
    assert (await cl.post("/v1/memories", json={"content": "Budget is 50k", "subject": "u"})).status_code == 202
    await drain(c)  # processing spends tokens (llm + embeddings)
    assert await c.aux.month_usage("acme") > 0
    await c.aux.add_usage("acme", 10_000)
    assert (await cl.post("/v1/search", json={"query": "budget", "subject": "u"})).status_code == 429
    assert (await cl.post("/v1/memories", json={"content": "more", "subject": "u"})).status_code == 429


async def test_isolation_fuzz(c, client_for):
    clients = {t: await client_for(t) for t in ("t1", "t2", "t3")}
    ids: dict[str, str] = {}
    for t, cl in clients.items():
        await cl.post("/v1/memories", json={"content": f"Budget is {t}", "subject": "u"})
    await drain(c)
    for t, cl in clients.items():
        ids[t] = (await cl.post("/v1/search", json={"query": "budget", "subject": "u"})).json()["results"][0]["id"]
    rnd = random.Random(3)
    words = ["budget", "t1", "t2", "t3", "is", "u", "Budget is t1", "Budget is t2"]
    for _ in range(300):
        me, other = rnd.sample(list(clients), 2)
        op = rnd.choice(["search", "get", "delete", "list", "export"])
        cl = clients[me]
        if op == "search":
            res = (await cl.post("/v1/search", json={"query": rnd.choice(words), "subject": "u"})).json()
            assert all(r["tenant"] == me for r in res["results"])
        elif op == "get":
            assert (await cl.get(f"/v1/memories/{ids[other]}")).status_code == 404
        elif op == "delete":
            assert (await cl.delete(f"/v1/memories/{ids[other]}")).status_code == 404
        elif op == "list":
            assert all(m["tenant"] == me for m in (await cl.get("/v1/memories", params={"subject": "u"})).json()["memories"])
        else:
            assert all(m["tenant"] == me for m in (await cl.get("/v1/export", params={"subject": "u"})).json()["memories"])
    for t in clients:  # nothing was destroyed by the cross-tenant delete attempts
        assert await c.mems.get(t, ids[t]) is not None


def test_metering_context_is_clean():
    assert current_tenant.get() is None
