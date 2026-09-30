from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Все настройки — только отсюда. os.getenv в обход Settings не используем.
    Полный список переменных окружения — в .env.example в корне репозитория."""

    telegram_bot_token: str
    openai_api_key: str
    openai_base_url: str = "https://api.proxyapi.ru/openai/v1"
    openai_model: str = "gpt-4o"
    # Уровень рассуждений для моделей с рассуждениями (gpt-6-luna: none, low,
    # medium, high, xhigh, max). Без параметра модель работает на своём уровне
    # по умолчанию (у gpt-6-luna — medium: медленнее и дороже). Моделям без
    # рассуждений не передаётся — см. generation/llm_models.py, prompts/models.yaml.
    openai_reasoning_effort: str = "low"
    # Явный таймаут одного запроса к LLM (ТЗ 4.6). Без него зависший запрос
    # ограничивал только job_timeout воркера.
    openai_timeout_seconds: float = 45.0
    watermark_free_tier: bool = True

    # ── БД ───────────────────────────────────────────────────────────────────
    database_url: str = ""          # пусто = бот и воркер работают без БД

    # ── ARQ / очередь генерации, FSM, кэш картинок ───────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Картинки ─────────────────────────────────────────────────────────────
    unsplash_access_key: str = ""
    pexels_api_key: str = ""

    # ── S3 / MinIO — хранилище готовых PDF ──────────────────────────────────
    s3_endpoint_url: str = ""       # пусто = MinIO/S3 не настроен, worker пропустит загрузку
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "fibonacci-presentations"
    s3_region: str = "us-east-1"

    class Config:
        env_file = ".env"


settings = Settings()
