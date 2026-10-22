async def audit(c, tenant: str, actor: str, action: str, target: str | None = None, detail: dict | None = None):
    await c.aux.audit(tenant, actor, action, target, detail)
