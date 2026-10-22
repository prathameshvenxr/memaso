import pytest_asyncio

from memory_core.adapters.auth_apikey import ApiKeyAuth
from memory_core.adapters.store_postgres import build
from memory_core.migrate import create_api_key, create_tenant
from tests.conftest import ADMIN, APP
from tests.contract.base import AuthContract, StoreContract


class TestPostgresStore(StoreContract):
    @pytest_asyncio.fixture
    async def store(self, admin):
        s = build({"dsn": APP}, dim=8)
        await s.lifecycle.start()
        yield s
        await s.lifecycle.close()


class TestApiKeyAuth(AuthContract):
    async def make(self):
        from memory_core.adapters.pg_aux import PgAux
        from memory_core.adapters.pg_base import Pg

        await create_tenant(ADMIN, "acme")
        key = await create_api_key(ADMIN, "acme")
        pg = Pg(APP)
        await pg.start()
        self._pg = pg
        return ApiKeyAuth(PgAux(pg)), {"authorization": f"Bearer {key}"}, "acme"
