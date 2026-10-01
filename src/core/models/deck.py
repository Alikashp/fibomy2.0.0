"""DeckSpec — колода, единственное промежуточное представление (04_CONTRACTS.md, 5).

Все этапы после OUTLINE читают и дописывают DeckSpec, рендерер рисует его как
есть. Содержание слайда (content) хранится по схеме kind, вариант — только
раскладка (D-031), поэтому смена варианта и темы не требует LLM.
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from core.models.outline import PlannedSlide

SlideKind = Literal["title", "statement", "bullets", "comparison", "process", "metrics",
                    "chart_series", "chart_share", "conclusion", "closing"]


class Versions(BaseModel):
    engine: str = ""
    prompts: str = ""
    layouts: str = ""
    themes: str = ""


class Meta(BaseModel):
    title: str = Field(description="тема пользователя дословно (D-033, G-05)")
    subtitle: Optional[str] = None
    language: str
    presentation_type: str
    audience: str
    mode: Literal["topic", "material"]
    source_mode: Optional[Literal["strict", "extend"]] = None
    genre: str = "topic"
    theme_id: str
    seed: int
    image_mode: str = "none"
    watermark: bool = False
    author: Optional[dict] = None
    slides_requested: Optional[int] = None
    versions: Versions = Field(default_factory=Versions)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class Fit(BaseModel):
    styles: dict[str, str] = Field(default_factory=dict, description="слот → итоговый стиль типошкалы")
    shortened: list[str] = Field(default_factory=list)
    variant_from: Optional[str] = None
    truncated: bool = False


class Check(BaseModel):
    code: str
    severity: Literal["fixed", "warning", "error"]
    detail: Optional[str] = None


class Slide(BaseModel):
    id: str = Field(pattern=r"^s\d{2}$")
    index: int = Field(ge=1)
    kind: SlideKind
    role: str = "other"
    variant: str = Field(description="id LayoutSpec: «bullets.cards_grid»")
    plan: Optional[PlannedSlide] = None
    title: str
    content: dict = Field(default_factory=dict)
    data: Optional[dict] = None
    image: Optional[str] = None
    footnote: Optional[str] = None
    notes: str = ""
    fit: Fit = Field(default_factory=Fit)
    checks: list[Check] = Field(default_factory=list)
    user_locked: bool = False


class Degradation(BaseModel):
    stage: str
    slide_id: Optional[str] = None
    reason: str


class DeckSpec(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^dk_[0-9A-HJKMNP-TV-Z]{26}$")
    revision: int = 0
    meta: Meta
    slides: list[Slide] = Field(min_length=4, max_length=20)
    assets: dict[str, dict] = Field(default_factory=dict)
    degradations: list[Degradation] = Field(default_factory=list)

    def degrade(self, stage: str, reason: str, slide_id: str | None = None) -> None:
        self.degradations.append(Degradation(stage=stage, slide_id=slide_id, reason=reason))
