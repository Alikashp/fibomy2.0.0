"""REST API v1 (docs/API.md, 04_CONTRACTS.md, 7; D-055).

Запуск (сервис Railway «api», railway.api.toml):
    cd src && uvicorn api.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'

POST /v1/decks кладёт материал во временное хранилище (как бот), создаёт строку decks
и ставит generate_deck_job(deck_id) в ту же очередь ARQ. Воркер собирает колоду тем же
core.generate_deck и сохраняет файлы (core.storage.files); клиент опрашивает статус и
скачивает файлы через API с ключом.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import API_VERSION, limits, store
from api.auth import parse_authorization
from api.errors import ApiError
from api.schemas import DeckCreate, DeckStatus, ErrorResponse, Health, Themes, Usage
from api.status import build_status
from api.webhook import url_ok
from bot.delivery import safe_filename
from config import settings
from core.models.ids import new_deck_id
from core.models.request import DeckRequest
from core.models.theme import enabled_theme_ids, load_theme
from core.pipeline import ENGINE_VERSION
from core.storage import files as deck_files
from core.storage.progress import get_progress
from core.storage.redis import close_redis, get_redis
from db.models import ApiClient
from logging_setup import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Расширение файла → mime, как в боте (main._MATERIAL_MIME_BY_EXT)
MIME_BY_EXT = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
}
JSON_MAX_BYTES = 1024 * 1024          # JSON-тело: текст до 200 000 знаков помещается с запасом
IDEMPOTENCY_KEY_MAX = 128
FORMATS = ("pptx", "pdf")

UNSUPPORTED_PITCH = ("Питч-дек через API пока недоступен: он появится после переезда питч-дека на новый движок. "
                     "Сейчас поддерживается только presentation_type = \"doklad\".")


def _error_responses(*codes: int) -> dict:
    return {code: {"model": ErrorResponse} for code in codes}


# ── Зависимости ─────────────────────────────────────────────────────────────

async def authenticate(request: Request) -> ApiClient:
    """Ключ из Authorization: Bearer; затем лимит частоты запросов ключа."""
    key = parse_authorization(request.headers.get("authorization"))
    if key is None:
        raise ApiError(401, "UNAUTHORIZED", "Нужен заголовок Authorization: Bearer <ключ API>.")
    try:
        client = await store.client_by_key(key)
    except store.StoreUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "API временно недоступно: не настроена база данных.")
    if client is None:
        raise ApiError(401, "UNAUTHORIZED", "Ключ API не найден или отозван.")
    window = await limits.hit("req", client.id, client.rate_limit_per_min)
    if not window.allowed:
        raise ApiError(429, "RATE_LIMITED",
                       f"Слишком много запросов: не больше {client.rate_limit_per_min} в минуту на ключ. "
                       f"Повторите через {window.reset_in} с.",
                       headers={**window.headers(), "Retry-After": str(window.reset_in)})
    # Заголовки X-RateLimit-* добавляет middleware ко всем ответам запроса
    request.state.rate_headers = window.headers()
    return client


def base_url(request: Request) -> str:
    return settings.api_public_url or str(request.base_url)


# ── Приложение ──────────────────────────────────────────────────────────────

def create_app(*, arq_pool=None, manage_resources: bool = True) -> FastAPI:
    """arq_pool и manage_resources=False — для тестов: БД и Redis уже подготовлены."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if manage_resources:
            from arq import create_pool
            from arq.connections import RedisSettings
            from db.session import close_db, init_db

            await init_db()
            app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            logger.info("API started", extra={"version": API_VERSION})
        try:
            yield
        finally:
            if manage_resources:
                from db.session import close_db
                from generation.uploads import close_uploads

                await app.state.arq.aclose()
                await close_uploads()
                await close_redis()
                await close_db()

    app = FastAPI(
        title="Fibonacci AI API", version=API_VERSION, lifespan=lifespan,
        description="Генерация презентаций (PPTX + PDF) для внешних клиентов. Руководство — docs/API.md.",
        openapi_url="/v1/openapi.json", docs_url="/v1/docs", redoc_url=None,
    )
    app.state.arq = arq_pool

    @app.middleware("http")
    async def rate_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in getattr(request.state, "rate_headers", {}).items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError):
        return JSONResponse(ApiError(400, "BAD_REQUEST", _validation_message(exc.errors())).body(), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "BAD_REQUEST")
        message = {404: "Такого адреса нет. Список запросов — docs/API.md.",
                   405: "Метод не поддерживается для этого адреса."}.get(exc.status_code, str(exc.detail))
        return JSONResponse(ApiError(exc.status_code, code, message).body(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def on_crash(request: Request, exc: Exception):
        logger.exception("API request crashed", extra={"path": request.url.path})
        return JSONResponse(ApiError(500, "INTERNAL", "Внутренняя ошибка. Повторите запрос.").body(), status_code=500)

    # ── health, themes, usage ───────────────────────────────────────────────

    @app.get("/v1/health", response_model=Health, tags=["service"],
             responses={503: {"model": Health}})
    async def health():
        db = await store.ping()
        try:
            await get_redis().ping()
            redis = "ok"
        except Exception:
            redis = "error"
        body = {"status": "ok" if db == "ok" and redis == "ok" else "degraded", "version": API_VERSION,
                "engine": ENGINE_VERSION, "db": db, "redis": redis}
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    @app.get("/v1/themes", response_model=Themes, tags=["decks"], responses=_error_responses(401, 429))
    async def themes(client: ApiClient = Depends(authenticate)):
        out = []
        for theme_id in enabled_theme_ids():
            theme = load_theme(theme_id)
            out.append({"id": theme.id, "name": theme.name, "mode": theme.mode, "tags": theme.tags})
        return {"themes": out}

    @app.get("/v1/usage", response_model=Usage, tags=["service"], responses=_error_responses(401, 429))
    async def usage(client: ApiClient = Depends(authenticate)):
        now = datetime.now(timezone.utc)
        used = await store.count_today(client.id, now)
        return {"client_id": client.id, "name": client.name, "watermark": client.watermark,
                "daily_limit": client.daily_limit, "used_today": used,
                "remaining_today": max(0, client.daily_limit - used), "resets_at": store.next_day(now),
                "rate_limit_per_min": client.rate_limit_per_min, "decks_per_min": client.decks_per_min}

    # ── колоды ──────────────────────────────────────────────────────────────

    @app.post("/v1/decks", status_code=202, response_model=DeckStatus, tags=["decks"],
              responses={200: {"model": DeckStatus, "description": "Повтор с тем же Idempotency-Key"},
                         **_error_responses(400, 401, 413, 429, 503)},
              openapi_extra={"requestBody": {"required": True, "content": {
                  "application/json": {"schema": {"$ref": "#/components/schemas/DeckCreate"}},
                  "multipart/form-data": {"schema": {"type": "object", "required": ["params"], "properties": {
                      "params": {"type": "string", "description": "DeckCreate в JSON"},
                      "file": {"type": "string", "format": "binary",
                               "description": ".pdf, .docx, .pptx или .txt до 20 МБ"}}}},
              }}})
    async def create_deck(request: Request, client: ApiClient = Depends(authenticate)):
        idem = request.headers.get("idempotency-key")
        if idem is not None and not (1 <= len(idem) <= IDEMPOTENCY_KEY_MAX):
            raise ApiError(400, "BAD_REQUEST", f"Idempotency-Key — от 1 до {IDEMPOTENCY_KEY_MAX} знаков.")
        params, upload = await _read_create_body(request)
        _check_params(params)

        if idem:
            existing = await store.deck_by_idempotency(client.id, idem)
            if existing is not None:
                return _deck_response(request, existing, 200)

        window = await limits.hit("create", client.id, client.decks_per_min)
        if not window.allowed:
            raise ApiError(429, "RATE_LIMITED",
                           f"Слишком много новых колод: не больше {client.decks_per_min} в минуту на ключ. "
                           f"Повторите через {window.reset_in} с.",
                           headers={"Retry-After": str(window.reset_in)})

        from generation.uploads import delete_upload, put_upload

        material, ref = None, None
        try:
            if upload is not None:
                data, name, ext = upload
                ref = await put_upload(data, MIME_BY_EXT[ext])
                material = {"kind": "document", "ref": ref, "mime": MIME_BY_EXT[ext], "name": name}
            elif params.input.text:
                ref = await put_upload(params.input.text.encode("utf-8"), "text/plain")
                material = {"kind": "text", "ref": ref, "mime": "text/plain", "name": None}
        except Exception:
            logger.exception("Failed to store API material")
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Не удалось сохранить материал. Повторите запрос.")

        deck_id = new_deck_id()
        deck_request = _deck_request(params, client, material)
        payload = deck_request.model_dump(mode="json")
        outcome, deck, used = await store.create_deck(client.id, deck_id, payload, idempotency_key=idem or None,
                                                      daily_limit=client.daily_limit)
        if outcome != "created":
            if ref:
                await delete_upload(ref)
            if outcome == "duplicate":
                return _deck_response(request, deck, 200)
            now = datetime.now(timezone.utc)
            reset = store.next_day(now)
            raise ApiError(429, "DAILY_LIMIT_EXCEEDED",
                           f"Исчерпан суточный лимит ключа: {client.daily_limit} колод. Счётчик обнулится "
                           f"в {reset:%H:%M} UTC.",
                           headers={"Retry-After": str(int((reset - now).total_seconds()) + 1)})

        try:
            await request.app.state.arq.enqueue_job("generate_deck_job", deck_id, _job_id=deck_id)
        except Exception:
            logger.exception("Failed to enqueue API deck", extra={"deck_id": deck_id})
            await store.fail_deck(deck_id, "INTERNAL")
            if ref:
                await delete_upload(ref)
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Очередь генерации недоступна. Повторите запрос через минуту.")
        logger.info("API deck enqueued", extra={"deck_id": deck_id, "api_client_id": client.id,
                                                "mode": deck_request.mode})
        return _deck_response(request, deck, 202,
                              extra_headers={"X-Daily-Remaining": str(max(0, client.daily_limit - used - 1))})

    @app.get("/v1/decks/{deck_id}", response_model=DeckStatus, tags=["decks"],
             responses=_error_responses(401, 404, 429))
    async def get_deck(deck_id: str, request: Request, client: ApiClient = Depends(authenticate)):
        deck = await _own_deck(client, deck_id)
        return _deck_response(request, deck, 200, progress=await get_progress(deck_id))

    @app.get("/v1/decks/{deck_id}/files/{fmt}", tags=["decks"],
             responses={200: {"description": "Файл колоды", "content": {
                 deck_files.MIME["pptx"]: {"schema": {"type": "string", "format": "binary"}},
                 deck_files.MIME["pdf"]: {"schema": {"type": "string", "format": "binary"}}}},
                 **_error_responses(401, 404, 409, 410, 429)})
    async def get_file(deck_id: str, fmt: str, client: ApiClient = Depends(authenticate)):
        if fmt not in FORMATS:
            raise ApiError(404, "NOT_FOUND", "Формат файла — pptx или pdf.")
        deck = await _own_deck(client, deck_id)
        status = build_status(deck)
        if status["status"] == "failed":
            raise ApiError(409, "DECK_FAILED", f"Колода не собралась ({status['error']['code']}): файлов нет.")
        if status["status"] not in ("done", "done_pdf_pending"):
            raise ApiError(409, "DECK_NOT_READY", "Колода ещё не готова. Дождитесь status = \"done\".")
        refs = deck.files or {}
        if not refs.get(fmt):
            raise ApiError(404, "FILE_NOT_AVAILABLE", "PDF не получился у этой колоды — скачайте PPTX."
                           if fmt == "pdf" else "Файла нет.")
        expires = refs.get("expires_at")
        if expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            raise ApiError(410, "FILE_EXPIRED", "Срок хранения файлов истёк. Создайте колоду заново.")
        try:
            data = await deck_files.get_deck_file(refs[fmt])
        except Exception:
            logger.exception("File storage failed", extra={"deck_id": deck_id})
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Хранилище файлов недоступно. Повторите запрос.")
        if data is None:
            raise ApiError(410, "FILE_EXPIRED", "Срок хранения файлов истёк. Создайте колоду заново.")
        filename = safe_filename(status["title"], fmt)
        return Response(data, media_type=deck_files.MIME[fmt], headers={
            "Content-Disposition": f"attachment; filename=\"{_ascii(filename, fmt)}\"; "
                                   f"filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, no-store"})

    def openapi():
        if app.openapi_schema is None:
            app.openapi_schema = _openapi(app)
        return app.openapi_schema

    app.openapi = openapi
    return app


def _openapi(app: FastAPI) -> dict:
    """Схема OpenAPI: + DeckCreate (тело POST /v1/decks читается вручную — JSON или
    multipart), − автоматические ответы 422 (ошибки параметров API отдаёт кодом 400)."""
    from fastapi.openapi.utils import get_openapi

    spec = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    create = DeckCreate.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(create.pop("$defs", {}))
    schemas["DeckCreate"] = create
    for methods in spec["paths"].values():
        for op in methods.values():
            op.get("responses", {}).pop("422", None)
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    return spec


# ── Разбор POST /v1/decks ───────────────────────────────────────────────────

def _validation_message(errors) -> str:
    parts = []
    for err in errors[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body",))
        parts.append(f"{loc or 'тело'}: {err.get('msg')}")
    return "Неверные параметры. " + "; ".join(parts)


async def _read_create_body(request: Request) -> tuple[DeckCreate, tuple[bytes, str, str] | None]:
    ctype = request.headers.get("content-type", "").lower()
    length = request.headers.get("content-length")
    max_bytes = settings.api_max_file_mb * 1024 * 1024
    if ctype.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        if length and length.isdigit() and int(length) > max_bytes + JSON_MAX_BYTES:
            raise ApiError(413, "FILE_TOO_LARGE", f"Файл больше {settings.api_max_file_mb} МБ.")
        try:
            form = await request.form(max_files=1, max_fields=4)
        except Exception:
            raise ApiError(400, "BAD_REQUEST", "Не удалось разобрать multipart/form-data: нужны поля params "
                                               "и (необязательно) file.")
        try:
            return await _read_form(form, max_bytes)
        finally:
            await form.close()
    if ctype.startswith("application/json") or not ctype:
        if length and length.isdigit() and int(length) > JSON_MAX_BYTES:
            raise ApiError(413, "FILE_TOO_LARGE", "JSON-тело больше 1 МБ. Длинный материал пришлите файлом.")
        body = await request.body()
        if len(body) > JSON_MAX_BYTES:
            raise ApiError(413, "FILE_TOO_LARGE", "JSON-тело больше 1 МБ. Длинный материал пришлите файлом.")
        return _parse_params(body), None
    raise ApiError(400, "BAD_REQUEST", "Content-Type — application/json или multipart/form-data.")


async def _read_form(form, max_bytes: int) -> tuple[DeckCreate, tuple[bytes, str, str] | None]:
    raw = form.get("params")
    if raw is None or not isinstance(raw, str):
        raise ApiError(400, "BAD_REQUEST", "В multipart нужно поле params с параметрами колоды в JSON.")
    params = _parse_params(raw)
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return params, None
    name = (upload.filename or "").strip()[:255]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in MIME_BY_EXT:
        raise ApiError(400, "FILE_UNSUPPORTED", "Поддерживаются файлы .pdf, .docx, .pptx и .txt "
                                                "(тип определяется по расширению имени файла).")
    data = await upload.read()
    if len(data) > max_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", f"Файл больше {settings.api_max_file_mb} МБ.")
    if not data:
        raise ApiError(400, "BAD_REQUEST", "Файл пустой.")
    if params.input.text:
        raise ApiError(400, "BAD_REQUEST", "Передайте материал одним способом: файлом или input.text.")
    return params, (data, name, ext)


def _parse_params(raw: str | bytes) -> DeckCreate:
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise ApiError(400, "BAD_REQUEST", "Тело запроса — не JSON.")
    if not isinstance(data, dict):
        raise ApiError(400, "BAD_REQUEST", "Параметры колоды — JSON-объект.")
    try:
        return DeckCreate.model_validate(data)
    except ValidationError as e:
        raise ApiError(400, "BAD_REQUEST", _validation_message(e.errors()))


def _check_params(params: DeckCreate) -> None:
    if params.presentation_type == "pitch_deck":
        raise ApiError(400, "UNSUPPORTED_TYPE", UNSUPPORTED_PITCH)
    if params.presentation_type != "doklad":
        raise ApiError(400, "UNSUPPORTED_TYPE",
                       f"Тип «{params.presentation_type[:40]}» не поддерживается. Сейчас доступен только \"doklad\".")
    if params.theme_id not in enabled_theme_ids():
        raise ApiError(400, "BAD_REQUEST", f"Тема {params.theme_id} недоступна. Список — GET /v1/themes.")
    if params.webhook_url is not None and not url_ok(params.webhook_url):
        raise ApiError(400, "BAD_REQUEST", "webhook_url — адрес http:// или https://.")
    if not params.input.topic.strip() or len(params.input.topic.strip()) < 3:
        raise ApiError(400, "BAD_REQUEST", "input.topic: тема — от 3 знаков.")


def _deck_request(params: DeckCreate, client: ApiClient, material: dict | None) -> DeckRequest:
    return DeckRequest(
        client={"kind": "api", "api_client_id": client.id, "plan": client.plan if client.plan in
                ("free", "starter", "pro", "team") else "free"},
        presentation_type="doklad",
        input={"topic": params.input.topic.strip(), "material": material},
        source_mode=(params.source_mode or "strict") if material else None,
        language=params.language,
        audience=params.audience,
        slides_count=params.slides_count,
        theme_id=params.theme_id,
        image_mode="none",
        author=params.author.model_dump() if params.author else None,
        watermark=client.watermark,
        seed=params.seed,
        webhook_url=params.webhook_url,
    )


async def _own_deck(client: ApiClient, deck_id: str):
    if not deck_id.startswith("dk_") or len(deck_id) > 32:
        raise ApiError(404, "NOT_FOUND", "Колода не найдена.")
    deck = await store.deck_for_client(client.id, deck_id)
    if deck is None:
        raise ApiError(404, "NOT_FOUND", "Колода не найдена.")
    return deck


def _deck_response(request: Request, deck, status_code: int, progress=None, extra_headers: dict | None = None):
    body = DeckStatus(**build_status(deck, base_url=base_url(request), progress=progress))
    headers = {"Location": f"/v1/decks/{deck.id}", **(extra_headers or {})}
    return JSONResponse(json.loads(body.model_dump_json()), status_code=status_code, headers=headers)


def _ascii(filename: str, fmt: str) -> str:
    """Имя для старых клиентов без filename*: только латиница и цифры, иначе presentation."""
    stem = filename[: -len(fmt) - 1]
    plain = "_".join("".join(c for c in stem if c.isascii() and (c.isalnum() or c in "-_ ")).split())
    return f"{plain or 'presentation'}.{fmt}"


app = create_app()
