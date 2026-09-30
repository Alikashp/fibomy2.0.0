"""
Модели БД. SQLAlchemy async.

Таблицы:
- users       — все кто писал боту
- presentations — каждая сгенерированная презентация
- payments    — платежи через Telegram Stars

Решения:
- Схема меняется только миграциями Alembic (src/db/migrations). После правки
  моделей: cd src && alembic revision --autogenerate -m "..." — и проверить файл.
- user_id = Telegram user id (int64) — естественный PK, не суррогатный
- presentations хранят тему и тип — для статистики и анализа, плюс JSON самой
  презентации (spec) и длительности этапов — для разбора жалоб и переэкспорта
"""

from datetime import datetime
from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Integer,
    String, Text, ForeignKey, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class Base(DeclarativeBase):
    pass


# JSONB на Postgres, обычный JSON на остальных диалектах (удобно для тестов)
JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class PlanType(str, enum.Enum):
    FREE    = "free"
    STARTER = "starter"
    PRO     = "pro"
    TEAM    = "team"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(8))
    plan: Mapped[str] = mapped_column(
        String(16), default="free", server_default="free"
    )
    presentations_count: Mapped[int] = mapped_column(Integer, default=0)
    # ФИО докладчика / группа-организация — задаются в профиле бота (main.py),
    # подставляются в PresentationMeta.author_name/author_group для DOKLAD
    # (worker.py), а не заполняются моделью.
    author_name: Mapped[str | None] = mapped_column(String(150))
    author_group: Mapped[str | None] = mapped_column(String(150))
    # Последний выбор «strict» / «extend» (schemas.presentation.SourceMode) —
    # подставляется в сводку по умолчанию, если приложен материал (dialog.py).
    # NULL — ещё не выбирал.
    source_mode: Mapped[str | None] = mapped_column(String(16))
    # Последний выбор в сводке перед генерацией (main.py, dialog.py) — подставляется
    # по умолчанию в следующий раз. NULL — ещё не выбирал.
    last_language: Mapped[str | None] = mapped_column(String(8))
    last_slide_count: Mapped[int | None] = mapped_column(Integer)
    last_audience: Mapped[str | None] = mapped_column(String(32))
    last_color_scheme: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    presentations: Mapped[list["Presentation"]] = relationship(back_populates="user")
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")

    @property
    def free_limit(self) -> int:
        return 2

    @property
    def can_generate(self) -> bool:
        if self.plan != "free":
            return True
        return self.presentations_count < self.free_limit

    @property
    def presentations_left(self) -> int:
        if self.plan != "free":
            return 999
        return max(0, self.free_limit - self.presentations_count)


class Presentation(Base):
    __tablename__ = "presentations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    topic: Mapped[str] = mapped_column(Text)
    presentation_type: Mapped[str] = mapped_column(String(32))
    audience: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8))
    slide_count: Mapped[int | None] = mapped_column(Integer)
    has_brief: Mapped[bool] = mapped_column(Boolean, default=False)
    watermark: Mapped[bool] = mapped_column(Boolean, default=True)
    # job_id ARQ-задачи — тот же, что в логах (deck_id) и в статус-сообщении
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    # PresentationSchema.model_dump(mode="json") — сама презентация
    spec: Mapped[dict | None] = mapped_column(JsonColumn)
    # {"ingest": 120, "llm": 18234, ...} — миллисекунды по этапам
    durations_ms: Mapped[dict | None] = mapped_column(JsonColumn)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="presentations")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(256), unique=True)
    plan: Mapped[str] = mapped_column(String(16))
    stars_amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="payments")
