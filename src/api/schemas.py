"""Модели запросов и ответов API v1 (04_CONTRACTS.md, 7; docs/API.md).

Контракт для второго бота: поля только добавляются (новые — необязательные в запросе),
не удаляются и не переименовываются. tests/unit/test_api_contract.py сверяет схему
OpenAPI со снимком tests/unit/api_contract_v1.json.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.models.request import TOPIC_MAX_CHARS, Audience, Language, SourceMode, ThemeId

# Текст материала: ingest берёт первые 40 000 знаков (дальше — предупреждение
# MATERIAL_TRUNCATED), больше 200 000 не принимаем вовсе
TEXT_MAX_CHARS = 200_000


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeckInput(_Request):
    topic: str = Field(min_length=3, max_length=TOPIC_MAX_CHARS,
                       description="Тема — заголовок титульного слайда дословно, до 200 знаков")
    text: Optional[str] = Field(default=None, max_length=TEXT_MAX_CHARS,
                                description="Текст-материал. Не вместе с файлом")


class Author(_Request):
    name: Optional[str] = Field(default=None, max_length=150, description="ФИО докладчика — на титуле и финале")
    group: Optional[str] = Field(default=None, max_length=150, description="Группа или организация")


class DeckCreate(_Request):
    """Параметры колоды: JSON-тело или поле params в multipart/form-data (с полем file)."""
    presentation_type: str = Field(default="doklad",
                                   description="doklad — доклад. pitch_deck пока не поддерживается (UNSUPPORTED_TYPE)")
    input: DeckInput
    source_mode: Optional[SourceMode] = Field(
        default=None, description="Только с материалом: strict — только материал (по умолчанию), "
                                  "extend — дополнить общими знаниями до slides_count")
    language: Language = "ru"
    audience: Audience = "general"
    slides_count: Optional[int] = Field(default=None, ge=4, le=20,
                                        description="Всего слайдов вместе с титулом и финалом; по умолчанию 9")
    theme_id: ThemeId = Field(default="graphite_light",
                              description="id из GET /v1/themes. Старые id (graphite_light, graphite_dark, "
                                          "azure_coral, fresh_green) принимаются и переводятся на новые темы; "
                                          "по умолчанию — business_slate")
    author: Optional[Author] = None
    webhook_url: Optional[str] = Field(default=None, max_length=2000,
                                       description="https://… — POST со статусом колоды по готовности")
    seed: Optional[int] = Field(default=None, ge=0, le=2 ** 31 - 1,
                                description="Повторяемый выбор вариантов слайдов; обычно не нужен")
    image_mode: Literal["ai", "none"] = Field(
        default="ai", description="ai — ИИ-картинки на титуле и 2 слайдах (по умолчанию), none — без картинок")


class Progress(BaseModel):
    slides_done: int
    slides_total: int


class Files(BaseModel):
    pptx: str = Field(description="Ссылка на скачивание через API (с тем же ключом)")
    pdf: Optional[str] = Field(description="null — PDF не получился (предупреждение PDF_FAILED)")
    expires_at: datetime = Field(description="После этого момента файлы недоступны (410 FILE_EXPIRED)")


class Notice(BaseModel):
    code: str
    message: str


class DeckStatus(BaseModel):
    id: str
    status: Literal["queued", "processing", "done", "done_pdf_pending", "failed"]
    stage: Optional[str] = Field(description="Этап: ingest, outline, content, render, convert; null — в очереди "
                                             "или завершена")
    progress: Optional[Progress] = Field(description="Слайдов готово из N — на этапе content")
    title: str
    slides: Optional[int] = Field(description="Слайдов в готовой колоде")
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    files: Optional[Files]
    warnings: list[Notice]
    error: Optional[Notice]


class ErrorResponse(BaseModel):
    error: Notice


class Theme(BaseModel):
    id: str
    name: dict[str, str] = Field(description="Название по языкам: {\"ru\": …, \"en\": …}")
    mode: Literal["light", "dark"]
    tags: list[str]


class Themes(BaseModel):
    themes: list[Theme]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    engine: str
    db: Literal["ok", "error", "disabled"]
    redis: Literal["ok", "error"]


class Usage(BaseModel):
    client_id: str
    name: str
    watermark: bool
    daily_limit: int
    used_today: int
    remaining_today: int
    resets_at: datetime = Field(description="Начало следующих суток UTC — счётчик обнуляется")
    rate_limit_per_min: int
    decks_per_min: int
