"""OutlineResponse — анализ источника и план содержательных слайдов (04_CONTRACTS.md, 4.1).

Титул и финальный слайд модель не планирует (D-033): заголовок титула — тема
пользователя дословно, модель пишет только подзаголовок deck.subtitle.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from core.models import schema as S
from core.models.digest import Genre, ThesisStatus

# Все kinds содержательных слайдов фазы 1. Какие из них доступны в запросе,
# решает код (режим, реализованные варианты) — схема получает только их.
CONTENT_KINDS = ("statement", "bullets", "comparison", "process", "metrics",
                 "chart_series", "chart_share", "conclusion")
ROLES = ("context", "problem", "goal", "results", "details", "requirements", "constraints",
         "criteria", "plan", "risks", "ask", "conclusion", "team", "other")
GENRES = ("topic", "report", "requirements", "plan", "article", "other")
STATUSES = ("fact", "plan", "requirement", "constraint", "opinion")


class OutlineThesis(BaseModel):
    text: str
    status: ThesisStatus
    refs: list[str] = Field(default_factory=list)


class OutlineAsk(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)


class OutlineAnalysis(BaseModel):
    genre: Genre
    theses: list[OutlineThesis] = Field(default_factory=list)
    asks: list[OutlineAsk] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class PlannedDataset(BaseModel):
    id: str
    label_column: str
    value_columns: list[str]
    rows: Optional[list[str]] = None


class PlannedSlide(BaseModel):
    kind: str
    role: str = "other"
    title: str
    key_message: str
    items_planned: Optional[int] = None
    refs: list[str] = Field(default_factory=list)
    dataset: Optional[PlannedDataset] = None
    image_query: Optional[str] = None


class OutlineDeck(BaseModel):
    subtitle: str
    slides: list[PlannedSlide]


class OutlineResponse(BaseModel):
    analysis: OutlineAnalysis
    deck: OutlineDeck


def outline_schema(kinds: tuple[str, ...], min_slides: int, max_slides: int) -> dict:
    """Строгая JSON Schema ответа OUTLINE: только kinds, доступные в этом запросе."""
    planned = S.obj({
        "kind": S.enum(list(kinds)),
        "role": S.enum(list(ROLES)),
        "title": S.string(description="заголовок-вывод; для statement — само утверждение"),
        "key_message": S.string(description="одна мысль слайда"),
        "items_planned": S.integer_or_null(),
        "refs": S.array(S.string()),
        "dataset": S.nullable(S.obj({
            "id": S.string(),
            "label_column": S.string(),
            "value_columns": S.array(S.string(), max_items=3),
            "rows": S.nullable(S.array(S.string())),
        })),
        "image_query": S.nullable(S.string(description="на английском; только если фото уместно")),
    })
    return S.obj({
        "analysis": S.obj({
            "genre": S.enum(list(GENRES)),
            "theses": S.array(S.obj({
                "text": S.string(),
                "status": S.enum(list(STATUSES)),
                "refs": S.array(S.string()),
            }), max_items=20),
            "asks": S.array(S.obj({"text": S.string(), "refs": S.array(S.string())})),
            "missing": S.array(S.string()),
        }),
        "deck": S.obj({
            "subtitle": S.string(description="подзаголовок титула — здесь допускается переформулировка темы"),
            "slides": S.array(planned, min_items=min_slides, max_items=max_slides),
        }),
    })
