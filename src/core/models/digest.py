"""SourceDigest — что извлечено из источника (04_CONTRACTS.md, 3).

Создаёт INGEST, поле analysis дописывает OUTLINE. По теме digest содержит
только тему: fragments и datasets пусты. Разбор файлов (datasets с адресами
ячеек) — сессия 2.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

ThesisStatus = Literal["fact", "plan", "requirement", "constraint", "opinion"]
Genre = Literal["topic", "report", "requirements", "plan", "article", "other"]


class Fragment(BaseModel):
    id: str = Field(pattern=r"^f\d+$")
    text: str
    kind: Literal["heading", "paragraph", "list_item", "note"] = "paragraph"
    loc: dict = Field(default_factory=dict)


class Column(BaseModel):
    id: str
    name: str
    unit: Optional[str] = None
    type: Literal["label", "number", "percent", "date", "text"] = "text"


class Cell(BaseModel):
    raw: str
    value: Optional[float] = None


class Row(BaseModel):
    id: str
    label: str
    is_total: bool = False
    cells: dict[str, Cell] = Field(default_factory=dict)


class Dataset(BaseModel):
    id: str = Field(pattern=r"^ds\d+$")
    title: Optional[str] = None
    origin: Literal["docx_table", "pdf_table", "pptx_table", "pptx_chart", "text_series"]
    loc: dict = Field(default_factory=dict)
    columns: list[Column]
    rows: list[Row]
    axis: Optional[Literal["time", "category"]] = None
    sums: dict = Field(default_factory=dict)


class Source(BaseModel):
    kind: Literal["text", "pdf", "docx", "pptx", "txt"]
    name: Optional[str] = None
    pages: Optional[int] = None
    chars_total: int = 0
    chars_used: int = 0
    truncated: bool = False


class Thesis(BaseModel):
    text: str
    status: ThesisStatus
    refs: list[str] = Field(default_factory=list)


class Ask(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)


class Analysis(BaseModel):
    genre: Genre = "topic"
    theses: list[Thesis] = Field(default_factory=list)
    asks: list[Ask] = Field(default_factory=list)
    goals: list[dict] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class SourceDigest(BaseModel):
    schema_version: Literal[1] = 1
    mode: Literal["topic", "material"]
    topic: str
    source: Optional[Source] = None
    fragments: list[Fragment] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    analysis: Optional[Analysis] = None
    warnings: list[str] = Field(default_factory=list)
    chunks: list[dict] = Field(default_factory=list)

    @classmethod
    def for_topic(cls, topic: str) -> "SourceDigest":
        return cls(mode="topic", topic=topic)
