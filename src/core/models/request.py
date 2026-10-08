"""DeckRequest — параметры колоды, строка decks.request (04_CONTRACTS.md, 2)."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

PresentationType = Literal["doklad", "pitch_deck"]
Language = Literal["ru", "en", "uz", "kk"]
Audience = Literal["general", "students", "colleagues", "management", "clients", "investors"]
# Темы сессии 4 (D-063) и старые id сессий 1–3: старые принимаются и переводятся на новые
# (themes/*.yaml, поле replaces) — у бота в профиле и у клиентов API в запросах.
ThemeId = Literal["business_slate", "ember_dark", "sunny_cream", "mint_coral",
                  "graphite_light", "graphite_dark", "azure_coral", "fresh_green"]
SourceMode = Literal["strict", "extend"]

# Сообщение длиннее — это текст-материал, а не тема (вопрос 8, D-039)
TOPIC_MAX_CHARS = 200

DEFAULT_SLIDES = {"doklad": 9, "pitch_deck": 11}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Client(_Model):
    kind: Literal["bot", "api"] = "bot"
    user_id: Optional[int] = None
    api_client_id: Optional[str] = None
    plan: Literal["free", "starter", "pro", "team"] = "free"
    # Только бот: куда воркер отправляет файлы и какое статусное сообщение правит
    chat_id: Optional[int] = None
    status_message_id: Optional[int] = None


class Material(_Model):
    kind: Literal["text", "document", "digest"]
    ref: str = Field(description="uploads/…, redis:… (D-008) или digests/{deck_id} при «Повторить»")
    mime: Optional[str] = None
    name: Optional[str] = None


class Input(_Model):
    topic: str = Field(min_length=3, max_length=TOPIC_MAX_CHARS)
    material: Optional[Material] = None
    brief: Optional[str] = Field(default=None, max_length=2000)


class Author(_Model):
    name: Optional[str] = None
    group: Optional[str] = None

    @property
    def line(self) -> str:
        return " · ".join(p for p in (self.name, self.group) if p)


class DeckRequest(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    client: Client = Field(default_factory=Client)
    presentation_type: PresentationType = "doklad"
    input: Input
    source_mode: Optional[SourceMode] = None
    language: Language = "ru"
    audience: Audience = "general"
    slides_count: Optional[int] = Field(default=None, ge=4, le=20,
                                        description="null — число задаёт сюжет (питч-дек, 11)")
    theme_id: ThemeId = "business_slate"
    # web (Pexels) не используется с сессии 4 (D-006): строки decks прошлых колод читаются как ai
    image_mode: Literal["none", "web", "ai"] = "ai"
    author: Optional[Author] = None
    watermark: bool = True
    seed: Optional[int] = None
    parent_deck_id: Optional[str] = None
    webhook_url: Optional[str] = None

    @field_validator("theme_id", mode="after")
    @classmethod
    def _current_theme(cls, value: str) -> str:
        from core.models.theme import resolve_theme_id
        return resolve_theme_id(value)

    @property
    def images(self) -> bool:
        return self.image_mode != "none"

    @property
    def mode(self) -> Literal["topic", "material"]:
        return "material" if self.input.material else "topic"

    @property
    def total_slides(self) -> int:
        """Сколько слайдов всего, вместе с титулом и финалом."""
        return self.slides_count or DEFAULT_SLIDES[self.presentation_type]
