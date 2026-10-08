"""Контракты нового движка: модели, строгие схемы LLM, макеты, темы, миграция decks."""
import itertools
import re
import unittest

import _helpers  # noqa: F401 — путь к src/ и фиктивные ключи

from pydantic import ValidationError

from core.content.fill import content_schema
from core.models import schema as S
from core.models.deck import DeckSpec, Meta, Slide
from core.models.digest import SourceDigest
from core.models.ids import new_deck_id, slide_id
from core.models.layout import capacity, load_layouts, variants_of
from core.models.outline import OutlineResponse, outline_schema
from core.models.request import DeckRequest
from core.models.theme import all_theme_ids, contrast_ratio, load_theme


class Request(unittest.TestCase):

    def test_defaults_and_total(self):
        r = DeckRequest(input={"topic": "Как работает фотосинтез"})
        self.assertEqual((r.mode, r.total_slides, r.theme_id, r.presentation_type), ("topic", 9, "business_slate", "doklad"))
        self.assertEqual(r.image_mode, "ai")
        # старые id тем (сессии 1–3) переводятся на новые (D-063)
        self.assertEqual(DeckRequest(input={"topic": "Тема"}, theme_id="graphite_dark").theme_id, "ember_dark")
        self.assertEqual(DeckRequest(presentation_type="pitch_deck", input={"topic": "Тема"}).total_slides, 11)

    def test_topic_limit_200(self):
        DeckRequest(input={"topic": "т" * 200})
        with self.assertRaises(ValidationError):
            DeckRequest(input={"topic": "т" * 201})
        with self.assertRaises(ValidationError):
            DeckRequest(input={"topic": "ок"})

    def test_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError):
            DeckRequest(input={"topic": "Тема"}, colour="red")

    def test_round_trip_json(self):
        r = DeckRequest(input={"topic": "Тема"}, author={"name": "Иван", "group": "Б-21"},
                        client={"user_id": 5, "chat_id": 5, "status_message_id": 7})
        again = DeckRequest.model_validate(r.model_dump(mode="json"))
        self.assertEqual(again, r)
        self.assertEqual(again.author.line, "Иван · Б-21")

    def test_material_mode(self):
        r = DeckRequest(input={"topic": "Тема", "material": {"kind": "text", "ref": "redis:abc"}})
        self.assertEqual(r.mode, "material")


class Ids(unittest.TestCase):

    def test_deck_and_slide_ids(self):
        ids = {new_deck_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        for deck_id in ids:
            self.assertRegex(deck_id, r"^dk_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertEqual(slide_id(3), "s03")


class Schemas(unittest.TestCase):

    def test_outline_schema_strict_and_kinds(self):
        schema = outline_schema(("statement", "bullets", "conclusion"), 1, 9)
        self.assertTrue(S.is_strict(schema))
        kinds = schema["properties"]["deck"]["properties"]["slides"]["items"]["properties"]["kind"]["enum"]
        self.assertEqual(kinds, ["statement", "bullets", "conclusion"])

    def test_outline_response_parses_fake(self):
        resp = OutlineResponse.model_validate(_helpers.fake_outline(7))
        self.assertEqual(len(resp.deck.slides), 7)

    def test_content_schemas_strict_with_limits(self):
        layouts = load_layouts()
        schema, lines = content_schema("bullets", layouts["bullets.cards_grid"], 4)
        self.assertTrue(S.is_strict(schema))
        items = schema["properties"]["items"]
        self.assertEqual((items["minItems"], items["maxItems"]), (3, 4))
        # вместимость — минимум по раскладкам 3 и 4 (05_LAYOUTS.md, 3.3: 36 и 28 знаков)
        self.assertEqual(items["items"]["properties"]["heading"]["maxLength"], 28)
        self.assertEqual(items["items"]["properties"]["text"]["maxLength"], 122)
        comparison, _ = content_schema("comparison", layouts["comparison.two_columns"], None)
        self.assertEqual(comparison["properties"]["polarity"]["enum"], ["neutral", "pros_cons", "before_after"])
        self.assertIn("- пунктов: от 3 до 4", lines)
        for kind, variant, n in (("statement", "statement.big_quote", None),
                                 ("conclusion", "conclusion.numbered_takeaways", 3)):
            schema, _ = content_schema(kind, layouts[variant], n)
            self.assertTrue(S.is_strict(schema), kind)

    def test_nullable(self):
        self.assertEqual(S.nullable(S.string(10)), {"type": ["string", "null"], "maxLength": 10})
        self.assertEqual(S.nullable(S.enum(["a"]))["enum"], ["a", None])


class Layouts(unittest.TestCase):

    def test_catalog_session_4(self):
        """22 варианта 05_LAYOUTS.md + пауза statement.color_pause и два варианта «текст + картинка» (сессия 4)."""
        ids = set(load_layouts())
        self.assertEqual(ids, {
            "title.cover_center", "title.cover_band", "title.cover_split_image",
            "statement.big_quote", "statement.highlight_band", "statement.statement_image", "statement.color_pause",
            "bullets.icon_list", "bullets.cards_grid", "bullets.numbered_columns", "bullets.text_image_split",
            "bullets.image_numbered", "comparison.two_columns", "comparison.pros_cons",
            "process.horizontal_steps", "process.chevrons", "process.vertical_steps",
            "metrics.big_number", "metrics.kpi_cards", "chart_series.column_chart", "chart_series.line_chart",
            "chart_share.donut", "conclusion.numbered_takeaways", "conclusion.cards", "closing.thanks_center"})
        for kind in ("title", "statement", "bullets", "conclusion", "closing", "metrics", "chart_series",
                     "chart_share", "comparison", "process"):
            self.assertTrue(any(v.fallback for v in variants_of(kind)), kind)

    def test_capacity_matches_design_tables(self):
        """Вместимость из YAML = таблицы 05_LAYOUTS.md (база и минимальный стиль)."""
        L = load_layouts()
        expect = {
            ("title.cover_center", None, "title"): (59, 280),
            ("title.cover_center", None, "subtitle"): (120, 144),
            ("statement.big_quote", None, "statement"): (118, 248),
            ("statement.big_quote", None, "body"): (194, 418),
            ("statement.color_pause", None, "statement"): (129, 225),
            ("bullets.cards_grid", 3, "item.heading"): (36, 43),
            ("bullets.cards_grid", 3, "item.text"): (172, 279),
            ("bullets.cards_grid", 4, "item.heading"): (28, 34),
            ("bullets.cards_grid", 4, "item.text"): (122, 198),
            ("bullets.cards_grid", 6, "item.text"): (64, 111),
            ("bullets.cards_grid", 3, "title"): (73, 102),
            ("bullets.text_image_split", 3, "item.text"): (99, 172),
            ("conclusion.numbered_takeaways", 5, "item.text"): (124, 149),
            ("metrics.big_number", 1, "item.value"): (7, 12),
            ("closing.thanks_center", None, "title"): (22, 37),
        }
        for (variant, n, slot), (base, minimum) in expect.items():
            e = L[variant].text_slots(n)[slot]
            self.assertEqual((capacity(e), capacity(e, e.min_style)), (base, minimum), (variant, n, slot))

    def test_elements_inside_slide_and_text_inside_margins(self):
        for spec in load_layouts().values():
            groups = [(spec.elements(), False)]
            if spec.arrangements:
                for n in spec.arrangements:
                    cells = spec.item_elements(n)
                    groups += [(cell, i == len(cells) - 1) for i, cell in enumerate(cells)]
            for group, last in groups:
                for e in group:
                    if e.skip_last and last:
                        continue                      # соединитель последнего элемента не рисуется
                    b = e.box
                    # колонтитул (y ≥ 1000) свободен; заходить туда может только декоративная полоса «в край»
                    bottom = 1080 if e.type == "rect" and e.name == "band" else 1000
                    self.assertTrue(0 <= b.x and 0 <= b.y and b.x + b.w <= 1920 and b.y + b.h <= bottom,
                                    (spec.id, e.name, b))
                    if e.is_text:
                        self.assertTrue(80 <= b.x and b.x + b.w <= 1840 + 0.01, (spec.id, e.name, b))


class Themes(unittest.TestCase):

    def test_four_themes_contrast(self):
        self.assertEqual(all_theme_ids(), ["business_slate", "ember_dark", "sunny_cream", "mint_coral"])
        for tid in all_theme_ids():
            t = load_theme(tid)
            for a, b in t.contrast_pairs:
                self.assertGreaterEqual(contrast_ratio(t.colors[a], t.colors[b]), 4.5, (tid, a, b))

    def test_chart_colors_visible_and_distinct(self):
        for tid in all_theme_ids():
            t = load_theme(tid)
            self.assertEqual(len(t.chart), 6)
            for c in t.chart:  # графика: порог WCAG 3:1 к фону и к карточке
                self.assertGreaterEqual(contrast_ratio(c, t.colors["bg"]), 3.0, (tid, c))
                self.assertGreaterEqual(contrast_ratio(c, t.colors["surface"]), 3.0, (tid, c))
            for a, b in itertools.combinations(t.chart[:5], 2):
                self.assertGreaterEqual(delta_e2000(a, b), 20, (tid, a, b))

    def test_all_themes_enabled_since_session_2(self):
        self.assertTrue(all(load_theme(t).enabled for t in all_theme_ids()))

    def test_tokens_and_style_pairs(self):
        """Все токены на месте; пары, которые задаёт style темы (цитата, пауза, номер), — тоже ≥ 4.5:1."""
        from core.models.theme import COLOR_TOKENS
        for tid in all_theme_ids():
            t = load_theme(tid)
            self.assertEqual(set(COLOR_TOKENS) - set(t.colors), set(), tid)
            st = t.style
            pairs = [(st.get("quote_text", "primary"), st.get("quote_plate", "surface_alt")),
                     ("text_muted", st.get("quote_plate", "surface_alt")),
                     (st.get("badge_text", "on_primary"), st.get("badge_fill", "primary")),
                     (st.get("pause_text", "on_brand"), "accent" if st.get("pause") == "accent" else "brand"),
                     ("on_paper", "paper")] + ([("text", "paper"), ("text_muted", "paper")] if t.mode == "light" else [])
            for a, b in pairs:
                self.assertGreaterEqual(contrast_ratio(t.colors[a], t.colors[b]), 4.5, (tid, a, b))
            self.assertIn(st["header"], ("plate", "underline", "marker", "bar"), tid)
            self.assertIn(st["badge"], ("circle", "square", "flag", "pennant", "chevron"), tid)
            self.assertTrue({"cover", "content"} <= set(t.decor), tid)

    def test_owner_review_d070(self):
        """Тёмная: светлые заголовки, акцент тёплый (оранжевый — янтарный, без малинового), целиком
        цветные только титул и финал. Деловая: плоская (brand = brand_2), без декора на слайдах, без плашки."""
        import colorsys
        ember = load_theme("ember_dark")
        self.assertNotIn("title_color", ember.style)
        self.assertEqual((ember.style["heading_color"], ember.style["quote_text"]), ("text", "text"))
        self.assertEqual(ember.style["full_color"], "cover")
        for token in ("primary", "brand", "brand_2", "accent"):
            r, g, b = (int(ember.colors[token][i:i + 2], 16) / 255 for i in (1, 3, 5))
            hue = colorsys.rgb_to_hsv(r, g, b)[0] * 360
            self.assertTrue(18 <= hue <= 50, (token, hue))          # оранжевый — янтарный
        slate = load_theme("business_slate")
        self.assertEqual(slate.colors["brand"], slate.colors["brand_2"])
        self.assertEqual(slate.style["header"], "underline")
        self.assertEqual((slate.decor.get("content"), slate.decor.get("header")), ([], []))
        self.assertEqual(slate.style["card_stripe"], "none")

    def test_old_theme_ids_map_to_new(self):
        from core.models.theme import legacy_theme_ids, resolve_theme_id
        self.assertEqual(legacy_theme_ids(), {"graphite_light": "business_slate", "graphite_dark": "ember_dark",
                                              "azure_coral": "mint_coral", "fresh_green": "mint_coral"})
        self.assertEqual(load_theme("graphite_dark").id, "ember_dark")   # DeckSpec прошлых колод рисуется
        self.assertEqual(resolve_theme_id("нет такой"), "business_slate")


def delta_e2000(a: str, b: str) -> float:
    import math

    def lab(h):
        def lin(c):
            c /= 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, bl = (lin(int(h.lstrip("#")[i:i + 2], 16)) for i in (0, 2, 4))
        x = (0.4124 * r + 0.3576 * g + 0.1805 * bl) / 0.95047
        y = 0.2126 * r + 0.7152 * g + 0.0722 * bl
        z = (0.0193 * r + 0.1192 * g + 0.9505 * bl) / 1.08883
        f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # noqa: E731
        return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))

    (L1, a1, b1), (L2, a2, b2) = lab(a), lab(b)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    G = 0.5 * (1 - math.sqrt(((C1 + C2) / 2) ** 7 / (((C1 + C2) / 2) ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p, h2p = math.degrees(math.atan2(b1, a1p)) % 360, math.degrees(math.atan2(b2, a2p)) % 360
    dh = h2p - h1p
    dh = 0 if C1p * C2p == 0 else dh - 360 if dh > 180 else dh + 360 if dh < -180 else dh
    dL, dC = L2 - L1, C2p - C1p
    dH = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lb, Cb = (L1 + L2) / 2, (C1p + C2p) / 2
    hb = (h1p + h2p) / 2 if abs(h1p - h2p) <= 180 else (h1p + h2p + 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hb - 30)) + 0.24 * math.cos(math.radians(2 * hb))
         + 0.32 * math.cos(math.radians(3 * hb + 6)) - 0.2 * math.cos(math.radians(4 * hb - 63)))
    Rc = 2 * math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))
    Rt = -math.sin(math.radians(60 * math.exp(-((hb - 275) / 25) ** 2))) * Rc
    Sl = 1 + 0.015 * (Lb - 50) ** 2 / math.sqrt(20 + (Lb - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cb, 1 + 0.015 * Cb * T
    return math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh))


class Deck(unittest.TestCase):

    def test_deckspec_validates(self):
        meta = Meta(title="Тема", language="ru", presentation_type="doklad", audience="general", mode="topic",
                    theme_id="graphite_light", seed=1)
        slides = [Slide(id=slide_id(i), index=i, kind="statement", variant="statement.big_quote", title="Т")
                  for i in range(1, 5)]
        spec = DeckSpec(id=new_deck_id(), meta=meta, slides=slides)
        again = DeckSpec.model_validate(spec.model_dump(mode="json"))
        self.assertEqual(again.slides[2].id, "s03")
        with self.assertRaises(ValidationError):
            DeckSpec(id="dk_bad", meta=meta, slides=slides)
        self.assertEqual(SourceDigest.for_topic("Т").mode, "topic")


class Migration(unittest.TestCase):

    def test_0005_creates_and_drops_decks(self):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect

        from db import session as db_session

        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            db_session._run_migrations(conn)
            tables = set(inspect(conn).get_table_names())
            self.assertTrue({"decks", "deck_revisions", "users", "presentations"} <= tables)
            cols = {c["name"] for c in inspect(conn).get_columns("decks")}
            self.assertTrue({"id", "status", "request", "spec", "usage", "cost_rub", "counted"} <= cols)
            cfg = Config(str(db_session._ALEMBIC_INI))
            cfg.set_main_option("script_location", str(db_session._ALEMBIC_INI.parent / "db" / "migrations"))
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0004")
            self.assertNotIn("decks", set(inspect(conn).get_table_names()))
            self.assertIn("presentations", set(inspect(conn).get_table_names()))


if __name__ == "__main__":
    unittest.main()
