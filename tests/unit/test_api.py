"""REST API v1: эндпоинты, авторизация, лимиты, ошибки (docs/API.md). Движок не
вызывается: задачи только ставятся в подменную очередь, состояние колоды правится в БД.

Эти тесты — контракт второго бота: если тест падает после правки, правка, скорее
всего, ломает обратную совместимость (CLAUDE.md)."""
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import _helpers  # noqa: F401
from _api import ApiHarness

from api import auth, store
from config import settings
from core.storage import files as deck_files
from core.storage.progress import set_progress
from db.models import Deck
from db.session import get_session

TOPIC = "Как работает фотосинтез"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def edit_deck(deck_id: str, **fields):
    async with get_session() as session:
        deck = await session.get(Deck, deck_id)
        for name, value in fields.items():
            setattr(deck, name, value)


async def load_deck(deck_id: str) -> Deck:
    async with get_session() as session:
        return await session.get(Deck, deck_id)


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.h = ApiHarness()
        self.client, self.key = self.h.new_client()
        self.headers = self.h.auth(self.key)

    def tearDown(self):
        self.h.close()

    def post(self, body=None, headers=None, **kw):
        return self.h.http.post("/v1/decks", json=body if body is not None else {"input": {"topic": TOPIC}},
                                headers={**self.headers, **(headers or {})}, **kw)

    def create(self, **body) -> str:
        r = self.post({"input": {"topic": TOPIC}, **body})
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()["id"]

    def assertError(self, response, status: int, code: str):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        return body["error"]["message"]


class Auth(ApiCase):

    def test_health_without_key(self):
        r = self.h.http.get("/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r.json()["db"], "ok")

    def test_missing_wrong_and_revoked_key(self):
        self.assertError(self.h.http.get("/v1/themes"), 401, "UNAUTHORIZED")
        self.assertError(self.h.http.get("/v1/themes", headers={"Authorization": "Bearer fib_wrongwrongwrongwrong"}),
                         401, "UNAUTHORIZED")
        self.assertError(self.h.http.get("/v1/themes", headers={"Authorization": self.key}), 401, "UNAUTHORIZED")
        self.assertEqual(self.h.http.get("/v1/themes", headers=self.headers).status_code, 200)
        self.h.run(store.update_client, self.client.id, active=False)
        self.assertError(self.h.http.get("/v1/themes", headers=self.headers), 401, "UNAUTHORIZED")

    def test_key_stored_as_hash_only(self):
        row = self.h.run(store.get_client, self.client.id)
        self.assertEqual(row.key_hash, auth.hash_key(self.key))
        self.assertNotIn(self.key, json.dumps({k: str(v) for k, v in vars(row).items()}))
        self.assertTrue(self.key.startswith("fib_") and len(self.key) > 40)

    def test_rotate_key(self):
        new_key = self.h.run(store.rotate_key, self.client.id)
        self.assertError(self.h.http.get("/v1/themes", headers=self.headers), 401, "UNAUTHORIZED")
        self.assertEqual(self.h.http.get("/v1/themes", headers=self.h.auth(new_key)).status_code, 200)

    def test_parse_authorization(self):
        self.assertEqual(auth.parse_authorization(f"bearer {self.key}"), self.key)
        self.assertIsNone(auth.parse_authorization("Basic abc"))
        self.assertIsNone(auth.parse_authorization("Bearer short"))
        self.assertIsNone(auth.parse_authorization(None))


class CreateDeck(ApiCase):

    def test_topic_deck_queued(self):
        r = self.post({"input": {"topic": TOPIC}, "slides_count": 7, "language": "en", "theme_id": "graphite_dark",
                       "author": {"name": "Иванов И.", "group": "Б-21"}})
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertTrue(body["id"].startswith("dk_"))
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["title"], TOPIC)
        self.assertIsNone(body["files"])
        self.assertEqual(r.headers["Location"], f"/v1/decks/{body['id']}")
        self.assertEqual(r.headers["X-Daily-Remaining"], "99")
        self.assertIn("X-RateLimit-Remaining", r.headers)
        self.assertEqual(self.h.pool.jobs, [("generate_deck_job", (body["id"],), {"_job_id": body["id"]})])
        deck = self.h.run(load_deck, body["id"])
        self.assertEqual(deck.api_client_id, self.client.id)
        self.assertIsNone(deck.user_id)
        req = deck.request
        self.assertEqual(req["client"]["kind"], "api")
        self.assertEqual(req["client"]["api_client_id"], self.client.id)
        self.assertIsNone(req["client"]["chat_id"])
        self.assertEqual((req["slides_count"], req["language"], req["theme_id"]), (7, "en", "graphite_dark"))
        self.assertIsNone(req["input"]["material"])
        self.assertIsNone(req["source_mode"], "по теме source_mode не передаётся")
        self.assertTrue(req["watermark"])
        self.assertEqual(req["author"], {"name": "Иванов И.", "group": "Б-21"})

    def test_watermark_follows_client(self):
        client, key = self.h.new_client("без знака", watermark=False)
        r = self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}}, headers=self.h.auth(key))
        req = self.h.run(load_deck, r.json()["id"]).request
        self.assertFalse(req["watermark"])
        self.assertEqual(req["client"]["plan"], "pro")

    def test_text_material(self):
        text = "Выручка выросла на 12%. " * 20
        deck_id = self.create(input={"topic": TOPIC, "text": text})
        req = self.h.run(load_deck, deck_id).request
        material = req["input"]["material"]
        self.assertEqual(material["kind"], "text")
        self.assertEqual(req["source_mode"], "strict")
        from generation.uploads import get_upload
        self.assertEqual(self.h.run(get_upload, material["ref"]).decode(), text)

    def test_extend_mode(self):
        deck_id = self.create(input={"topic": TOPIC, "text": "Материал " * 30}, source_mode="extend")
        self.assertEqual(self.h.run(load_deck, deck_id).request["source_mode"], "extend")

    def test_multipart_file(self):
        params = json.dumps({"input": {"topic": "Итоги квартала"}, "slides_count": 9})
        r = self.h.http.post("/v1/decks", headers=self.headers, data={"params": params},
                             files={"file": ("отчёт Q3.docx", io.BytesIO(b"PK fake docx"), DOCX)})
        self.assertEqual(r.status_code, 202, r.text)
        req = self.h.run(load_deck, r.json()["id"]).request
        material = req["input"]["material"]
        self.assertEqual((material["kind"], material["mime"], material["name"]), ("document", DOCX, "отчёт Q3.docx"))
        self.assertEqual(req["source_mode"], "strict")

    def test_multipart_params_only(self):
        r = self.h.http.post("/v1/decks", headers=self.headers,
                             data={"params": json.dumps({"input": {"topic": TOPIC}})})
        self.assertEqual(r.status_code, 202, r.text)

    def test_file_errors(self):
        params = json.dumps({"input": {"topic": TOPIC}})
        r = self.h.http.post("/v1/decks", headers=self.headers, data={"params": params},
                             files={"file": ("scan.jpg", io.BytesIO(b"x" * 10), "image/jpeg")})
        self.assertError(r, 400, "FILE_UNSUPPORTED")
        r = self.h.http.post("/v1/decks", headers=self.headers, data={"params": params},
                             files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")})
        self.assertError(r, 400, "BAD_REQUEST")
        both = json.dumps({"input": {"topic": TOPIC, "text": "текст"}})
        r = self.h.http.post("/v1/decks", headers=self.headers, data={"params": both},
                             files={"file": ("a.txt", io.BytesIO(b"abc"), "text/plain")})
        self.assertIn("одним способом", self.assertError(r, 400, "BAD_REQUEST"))
        r = self.h.http.post("/v1/decks", headers=self.headers,
                             files={"file": ("a.txt", io.BytesIO(b"abc"), "text/plain")})
        self.assertIn("params", self.assertError(r, 400, "BAD_REQUEST"))
        with mock.patch.object(settings, "api_max_file_mb", 1):
            r = self.h.http.post("/v1/decks", headers=self.headers, data={"params": params},
                                 files={"file": ("big.txt", io.BytesIO(b"a" * (1024 * 1024 + 1)), "text/plain")})
        self.assertError(r, 413, "FILE_TOO_LARGE")
        self.assertEqual(self.h.pool.jobs, [])

    def test_pitch_deck_unsupported(self):
        r = self.post({"presentation_type": "pitch_deck", "input": {"topic": "Аренда инструмента"}})
        self.assertIn("Питч-дек", self.assertError(r, 400, "UNSUPPORTED_TYPE"))
        self.assertError(self.post({"presentation_type": "lecture", "input": {"topic": TOPIC}}), 400,
                         "UNSUPPORTED_TYPE")
        self.assertEqual(self.h.pool.jobs, [])

    def test_validation_errors(self):
        cases = [
            {"input": {"topic": "x" * 201}},
            {"input": {"topic": "ab"}},
            {"input": {}},
            {"input": {"topic": TOPIC}, "slides_count": 30},
            {"input": {"topic": TOPIC}, "language": "de"},
            {"input": {"topic": TOPIC}, "theme_id": "neon"},
            {"input": {"topic": TOPIC}, "unknown_field": 1},
            {"input": {"topic": TOPIC}, "webhook_url": "ftp://example.com/hook"},
            {"input": {"topic": TOPIC}, "source_mode": "loose"},
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertError(self.post(body), 400, "BAD_REQUEST")
        msg = self.assertError(self.post({"input": {"topic": "x" * 201}}), 400, "BAD_REQUEST")
        self.assertIn("input.topic", msg)
        r = self.h.http.post("/v1/decks", content=b"{not json", headers={**self.headers,
                                                                           "Content-Type": "application/json"})
        self.assertError(r, 400, "BAD_REQUEST")
        r = self.h.http.post("/v1/decks", content=b"<xml/>", headers={**self.headers, "Content-Type": "text/xml"})
        self.assertError(r, 400, "BAD_REQUEST")
        self.assertEqual(self.h.pool.jobs, [])

    def test_idempotency(self):
        r1 = self.post(headers={"Idempotency-Key": "order-42"})
        r2 = self.post(headers={"Idempotency-Key": "order-42"})
        self.assertEqual((r1.status_code, r2.status_code), (202, 200))
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        self.assertEqual(len(self.h.pool.jobs), 1)
        r3 = self.post(headers={"Idempotency-Key": "order-43"})
        self.assertNotEqual(r3.json()["id"], r1.json()["id"])
        # ключ у каждого клиента свой
        other, key = self.h.new_client("другой")
        r4 = self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}},
                              headers={**self.h.auth(key), "Idempotency-Key": "order-42"})
        self.assertEqual(r4.status_code, 202)
        self.assertError(self.post(headers={"Idempotency-Key": "k" * 129}), 400, "BAD_REQUEST")

    def test_idempotency_race_returns_existing(self):
        first = self.create()
        self.h.run(edit_deck, first, idempotency_key="race")
        outcome, deck, _ = self.h.run(store.create_deck, self.client.id, "dk_01J00000000000000000000RACE",
                                      {"input": {"topic": TOPIC}}, idempotency_key="race", daily_limit=100)
        self.assertEqual((outcome, deck.id), ("duplicate", first))

    def test_enqueue_failure(self):
        self.h.pool.fail = True
        r = self.post()
        self.assertError(r, 503, "SERVICE_UNAVAILABLE")
        deck = self.h.run(lambda: _only_deck(self.client.id))
        self.assertEqual((deck.status, deck.error_code), ("failed", "INTERNAL"))


async def _only_deck(client_id):
    from sqlalchemy import select
    async with get_session() as session:
        return (await session.scalars(select(Deck).where(Deck.api_client_id == client_id))).one()


class Limits(ApiCase):

    def test_daily_limit(self):
        client, key = self.h.new_client("малый", daily_limit=2)
        headers = self.h.auth(key)
        ids = [self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}}, headers=headers).json()["id"]
               for _ in range(2)]
        r = self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}}, headers=headers)
        msg = self.assertError(r, 429, "DAILY_LIMIT_EXCEEDED")
        self.assertIn("2", msg)
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        # упавшая колода не списывает генерацию
        self.h.run(edit_deck, ids[0], status="failed", error_code="OUTLINE_FAILED")
        self.assertEqual(self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}}, headers=headers).status_code,
                         202)
        # вчерашние колоды не считаются
        self.h.run(edit_deck, ids[1], created_at=datetime.now(timezone.utc) - timedelta(days=1))
        usage = self.h.http.get("/v1/usage", headers=headers).json()
        self.assertEqual((usage["used_today"], usage["remaining_today"]), (1, 1))
        # повтор с ключом идемпотентности при исчерпанном лимите — та же колода, не 429
        self.h.run(edit_deck, ids[1], idempotency_key="again")
        r = self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}},
                             headers={**headers, "Idempotency-Key": "again"})
        self.assertEqual(r.status_code, 200)

    def test_decks_per_minute(self):
        client, key = self.h.new_client("быстрый", decks_per_min=1)
        headers = self.h.auth(key)
        self.assertEqual(self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}}, headers=headers).status_code,
                         202)
        r = self.h.http.post("/v1/decks", json={"input": {"topic": TOPIC}}, headers=headers)
        self.assertIn("в минуту", self.assertError(r, 429, "RATE_LIMITED"))
        self.assertIn("Retry-After", r.headers)

    def test_requests_per_minute(self):
        client, key = self.h.new_client("частый", rate_limit_per_min=3)
        headers = self.h.auth(key)
        for i in range(3):
            r = self.h.http.get("/v1/themes", headers=headers)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["X-RateLimit-Remaining"], str(2 - i))
        r = self.h.http.get("/v1/themes", headers=headers)
        self.assertError(r, 429, "RATE_LIMITED")
        self.assertEqual(r.headers["X-RateLimit-Limit"], "3")
        self.assertTrue(1 <= int(r.headers["Retry-After"]) <= 60)
        # у другого ключа свой счётчик
        self.assertEqual(self.h.http.get("/v1/themes", headers=self.headers).status_code, 200)

    def test_limiter_fails_open_without_redis(self):
        from api import limits

        class Broken:
            async def incr(self, key):
                raise ConnectionError("down")

        with mock.patch.object(limits, "get_redis", return_value=Broken()):
            self.assertEqual(self.h.http.get("/v1/themes", headers=self.headers).status_code, 200)


class Status(ApiCase):

    def test_own_deck_only(self):
        deck_id = self.create()
        self.assertEqual(self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).status_code, 200)
        other, key = self.h.new_client("чужой")
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}", headers=self.h.auth(key)), 404, "NOT_FOUND")
        self.assertError(self.h.http.get("/v1/decks/dk_01J000000000000000000NOPE0", headers=self.headers), 404,
                         "NOT_FOUND")
        self.assertError(self.h.http.get("/v1/decks/123", headers=self.headers), 404, "NOT_FOUND")

    def test_processing_progress(self):
        deck_id = self.create()
        self.h.run(edit_deck, deck_id, status="processing", stage="content", started_at=datetime.now(timezone.utc))
        self.h.run(set_progress, deck_id, 3, 7)
        body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual((body["status"], body["stage"]), ("processing", "content"))
        self.assertEqual(body["progress"], {"slides_done": 3, "slides_total": 7})
        self.h.run(edit_deck, deck_id, stage="render")
        body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual((body["stage"], body["progress"]), ("render", None))

    def test_failed_deck(self):
        deck_id = self.create()
        self.h.run(edit_deck, deck_id, status="failed", error_code="SCAN_WITHOUT_TEXT",
                   finished_at=datetime.now(timezone.utc))
        body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "SCAN_WITHOUT_TEXT")
        self.assertIn("скан", body["error"]["message"])
        self.h.run(edit_deck, deck_id, error_code="SOMETHING_NEW")
        body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual(body["error"]["code"], "INTERNAL")

    def test_stale_deck_reported_failed(self):
        deck_id = self.create()
        self.h.run(edit_deck, deck_id, status="processing", stage="content",
                   created_at=datetime.now(timezone.utc) - timedelta(minutes=16))
        body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual((body["status"], body["error"]["code"]), ("failed", "DEADLINE_EXCEEDED"))

    def test_done_with_files_and_warnings(self):
        deck_id = self.create()
        files = self.h.run(deck_files.put_deck_files, deck_id, {"pptx": b"PPTX", "pdf": None})
        self.h.run(edit_deck, deck_id, status="done", files=files, finished_at=datetime.now(timezone.utc),
                   warnings=["slides_short:7/9", "truncated:40000/62000", "pdf_failed"],
                   spec={"meta": {"title": TOPIC}, "slides": [{}] * 7})
        body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["slides"], 7)
        self.assertTrue(body["files"]["pptx"].endswith(f"/v1/decks/{deck_id}/files/pptx"))
        self.assertTrue(body["files"]["pptx"].startswith("http"))
        self.assertIsNone(body["files"]["pdf"])
        self.assertEqual([w["code"] for w in body["warnings"]], ["SLIDES_SHORT", "MATERIAL_TRUNCATED", "PDF_FAILED"])
        self.assertIn("7 слайдов из 9", body["warnings"][0]["message"])
        self.assertIn("40 000", body["warnings"][1]["message"])
        with mock.patch.object(settings, "api_public_url", "https://api.example.com"):
            body = self.h.http.get(f"/v1/decks/{deck_id}", headers=self.headers).json()
        self.assertEqual(body["files"]["pptx"], f"https://api.example.com/v1/decks/{deck_id}/files/pptx")


class Files(ApiCase):

    def _done(self, pdf=b"%PDF-1.4 test", title=TOPIC) -> str:
        deck_id = self.create()
        files = self.h.run(deck_files.put_deck_files, deck_id, {"pptx": b"PK pptx bytes", "pdf": pdf})
        self.h.run(edit_deck, deck_id, status="done", files=files, spec={"meta": {"title": title}, "slides": [{}]})
        return deck_id

    def test_download(self):
        deck_id = self._done()
        r = self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"PK pptx bytes")
        self.assertEqual(r.headers["Content-Type"], deck_files.MIME["pptx"])
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertIn("filename*=UTF-8''", r.headers["Content-Disposition"])
        r = self.h.http.get(f"/v1/decks/{deck_id}/files/pdf", headers=self.headers)
        self.assertEqual((r.status_code, r.content[:4]), (200, b"%PDF"))
        self.assertEqual(r.headers["Content-Type"], "application/pdf")

    def test_download_needs_own_key(self):
        deck_id = self._done()
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx"), 401, "UNAUTHORIZED")
        other, key = self.h.new_client("чужой")
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.h.auth(key)), 404,
                         "NOT_FOUND")

    def test_not_ready_failed_and_bad_format(self):
        deck_id = self.create()
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers), 409,
                         "DECK_NOT_READY")
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/docx", headers=self.headers), 404, "NOT_FOUND")
        self.h.run(edit_deck, deck_id, status="failed", error_code="OUTLINE_FAILED")
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers), 409, "DECK_FAILED")

    def test_pdf_missing(self):
        deck_id = self._done(pdf=None)
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pdf", headers=self.headers), 404,
                         "FILE_NOT_AVAILABLE")
        self.assertEqual(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers).status_code, 200)

    def test_expired(self):
        deck_id = self._done()
        deck = self.h.run(load_deck, deck_id)
        files = dict(deck.files, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.h.run(edit_deck, deck_id, files=files)
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers), 410,
                         "FILE_EXPIRED")

    def test_redis_ttl_gone(self):
        deck_id = self._done()
        self.h.run(self.h.redis.flushall)
        self.assertError(self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers), 410,
                         "FILE_EXPIRED")

    def test_redis_ttl_is_one_hour(self):
        deck_id = self._done()
        ttl = self.h.run(self.h.redis.ttl, f"deckfile:{deck_id}:pptx")
        self.assertTrue(3500 < ttl <= 3600, ttl)
        expires = datetime.fromisoformat(self.h.run(load_deck, deck_id).files["expires_at"])
        self.assertAlmostEqual((expires - datetime.now(timezone.utc)).total_seconds(), 3600, delta=60)

    def test_ascii_fallback_filename(self):
        deck_id = self._done(title="Как работает фотосинтез")
        r = self.h.http.get(f"/v1/decks/{deck_id}/files/pptx", headers=self.headers)
        self.assertIn('filename="presentation.pptx"', r.headers["Content-Disposition"])
        deck_id = self._done(title="Q3 sales: итоги")
        r = self.h.http.get(f"/v1/decks/{deck_id}/files/pdf", headers=self.headers)
        self.assertIn('filename="Q3_sales_.pdf"', r.headers["Content-Disposition"])


class S3Storage(unittest.TestCase):
    """С S3_ENDPOINT_URL файлы идут в бакет (ключи decks/ГГГГ/ММ/ДД/<id>.<ext>), срок — 24 ч."""

    def test_put_and_get(self):
        import asyncio
        from core.storage import s3

        class FakeS3:
            def __init__(self):
                self.objects = {}

            def put_object(self, Bucket, Key, Body, ContentType):
                self.objects[Key] = Body

            def get_object(self, Bucket, Key):
                if Key not in self.objects:
                    err = Exception("NoSuchKey")
                    err.response = {"Error": {"Code": "NoSuchKey"}}
                    raise err
                return {"Body": io.BytesIO(self.objects[Key])}

        fake = FakeS3()
        now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        with mock.patch.object(settings, "s3_endpoint_url", "https://s3.example.com"), \
                mock.patch.object(s3, "client", return_value=fake):
            files = asyncio.run(deck_files.put_deck_files("dk_X", {"pptx": b"P", "pdf": b"D"}, now=now))
            self.assertEqual(files["pptx"], "s3:decks/2026/10/01/dk_X.pptx")
            self.assertEqual(files["backend"], "s3")
            self.assertEqual(files["expires_at"], "2026-10-02T12:00:00+00:00")
            self.assertEqual(asyncio.run(deck_files.get_deck_file(files["pdf"])), b"D")
            self.assertIsNone(asyncio.run(deck_files.get_deck_file("s3:decks/missing.pptx")))


class Misc(ApiCase):

    def test_themes(self):
        body = self.h.http.get("/v1/themes", headers=self.headers).json()
        ids = {t["id"] for t in body["themes"]}
        self.assertEqual(ids, {"graphite_light", "graphite_dark", "azure_coral", "fresh_green"})
        for theme in body["themes"]:
            self.assertEqual(set(theme), {"id", "name", "mode", "tags"})
            self.assertIn("ru", theme["name"])

    def test_disabled_theme_rejected(self):
        with mock.patch("api.app.enabled_theme_ids", return_value=["graphite_light"]):
            self.assertError(self.post({"input": {"topic": TOPIC}, "theme_id": "azure_coral"}), 400, "BAD_REQUEST")

    def test_unknown_route_and_method(self):
        self.assertError(self.h.http.get("/v1/nothing"), 404, "NOT_FOUND")
        self.assertError(self.h.http.delete("/v1/themes", headers=self.headers), 405, "METHOD_NOT_ALLOWED")

    def test_internal_error_format(self):
        with mock.patch.object(store, "deck_for_client", side_effect=RuntimeError("boom")):
            self.assertError(self.h.http.get("/v1/decks/dk_01J0000000000000000000000A", headers=self.headers), 500,
                             "INTERNAL")

    def test_health_degraded(self):
        with mock.patch.object(store, "ping", return_value="error"):
            r = self.h.http.get("/v1/health")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["status"], "degraded")

    def test_openapi_served(self):
        r = self.h.http.get("/v1/openapi.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/v1/decks", r.json()["paths"])


class Migration(unittest.TestCase):

    def test_0006_adds_api_clients_and_downgrades(self):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect
        from db import session as db_session

        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            db_session._run_migrations(conn)
            self.assertIn("api_clients", inspect(conn).get_table_names())
            cols = {c["name"] for c in inspect(conn).get_columns("api_clients")}
            self.assertTrue({"id", "name", "key_hash", "daily_limit", "rate_limit_per_min", "decks_per_min",
                             "watermark", "webhook_secret", "active"} <= cols)
            self.assertIn("warnings", {c["name"] for c in inspect(conn).get_columns("decks")})
            cfg = Config(str(db_session._ALEMBIC_INI))
            cfg.set_main_option("script_location", str(db_session._ALEMBIC_INI.parent / "db" / "migrations"))
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0005")
            self.assertNotIn("api_clients", inspect(conn).get_table_names())
            self.assertIn("decks", inspect(conn).get_table_names())


class KeysCli(unittest.TestCase):

    def test_create_prints_key_once_and_usage(self):
        import contextlib
        from api import keys
        h = ApiHarness()
        try:
            # init_db/close_db CLI — подменяем: база уже готова у стенда
            async def noop():
                return None
            out = io.StringIO()
            with mock.patch.object(keys, "init_db", noop), mock.patch.object(keys, "close_db", noop), \
                    contextlib.redirect_stdout(out):
                code = h.run(keys._run, keys.parse_args(["create", "--name", "Второй бот", "--daily-limit", "200",
                                                         "--no-watermark"]))
            self.assertEqual(code, 0)
            text = out.getvalue()
            key = next(line.split()[-1] for line in text.splitlines() if "API-ключ" in line)
            self.assertTrue(key.startswith("fib_"))
            self.assertIn("whsec_", text)
            client = self.h_client(h, key)
            self.assertEqual((client.daily_limit, client.watermark), (200, False))
            out = io.StringIO()
            with mock.patch.object(keys, "init_db", noop), mock.patch.object(keys, "close_db", noop), \
                    contextlib.redirect_stdout(out):
                h.run(keys._run, keys.parse_args(["list"]))
                h.run(keys._run, keys.parse_args(["usage", "--days", "7"]))
            self.assertNotIn(key, out.getvalue(), "ключ целиком больше не печатается")
            self.assertIn(key[:12], out.getvalue())
        finally:
            h.close()

    @staticmethod
    def h_client(h, key):
        return h.run(store.client_by_key, key)


if __name__ == "__main__":
    unittest.main()
