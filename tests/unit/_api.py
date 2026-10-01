"""Общее для тестов REST API: SQLite-база с миграциями, fakeredis, подменная очередь ARQ,
TestClient FastAPI. Всё асинхронное выполняется в цикле TestClient (portal), чтобы
соединения fakeredis и aiosqlite жили в одном цикле событий."""
import tempfile

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api import store
from api.app import create_app
from core.storage import redis as redis_store
from db import session as db_session
from generation import uploads


class FakePool:
    """Очередь ARQ: запоминает задачи; fail=True — очередь недоступна."""

    def __init__(self):
        self.jobs: list[tuple[str, tuple, dict]] = []
        self.fail = False

    async def enqueue_job(self, name, *args, **kwargs):
        if self.fail:
            raise ConnectionError("redis down")
        self.jobs.append((name, args, kwargs))
        return object()


class ApiHarness:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = f"{self._tmp.name}/api.db"
        sync = create_engine(f"sqlite:///{path}")
        with sync.begin() as conn:
            db_session._run_migrations(conn)
        sync.dispose()
        self._saved = (db_session.engine, db_session.SessionLocal, redis_store._client, uploads._redis)
        db_session.engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
        db_session.SessionLocal = async_sessionmaker(db_session.engine, class_=AsyncSession, expire_on_commit=False)
        self.redis = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer())
        redis_store.set_client(self.redis)
        uploads._redis = self.redis
        self.pool = FakePool()
        self.app = create_app(arq_pool=self.pool, manage_resources=False)
        self.http = TestClient(self.app, raise_server_exceptions=False)
        self.http.__enter__()

    def close(self) -> None:
        self.run(db_session.engine.dispose)
        self.run(self.redis.aclose)
        self.http.__exit__(None, None, None)
        db_session.engine, db_session.SessionLocal, redis_store._client, uploads._redis = self._saved
        self._tmp.cleanup()

    def run(self, fn, *args, **kwargs):
        """Корутина-функция в цикле TestClient."""
        async def call():
            return await fn(*args, **kwargs)
        return self.http.portal.call(call)

    def new_client(self, name: str = "Второй бот", **kw):
        client, key = self.run(store.create_client, name, **kw)
        return client, key

    @staticmethod
    def auth(key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}
