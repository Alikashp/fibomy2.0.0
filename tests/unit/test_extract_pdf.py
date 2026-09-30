"""Правка 7: таблицы из PDF — через extract_tables, в формате docx."""
import asyncio
import unittest

from _helpers import FIXTURES

from generation.content_extractor import extract_from_document

PDF = "application/pdf"


def _extract(name: str) -> str:
    return asyncio.run(extract_from_document((FIXTURES / name).read_bytes(), PDF))


class PdfTables(unittest.TestCase):

    def test_tables_in_docx_format_and_in_place(self):
        text = _extract("unit/pdf_with_tables.pdf")
        self.assertIn(
            "[Таблица 1]\n"
            "Склад | Отгрузки, т | Рост к прошлому году, %\n"
            "Север | 640 | +5\n"
            "Юг | 310 | −12\n"
            "Итого | 950 | —",
            text,
        )
        self.assertIn("[Таблица 2]\nПозиция | Количество\nПогрузчик электрический | 2", text)
        # порядок документа: текст до таблицы → таблица → текст после
        self.assertLess(text.index("Склад Юг на ремонте"), text.index("[Таблица 1]"))
        self.assertLess(text.index("[Таблица 1]"), text.index("Текст после первой таблицы"))
        self.assertLess(text.index("Вторая страница"), text.index("[Таблица 2]"))

    def test_table_cells_are_not_duplicated_as_plain_text(self):
        text = _extract("unit/pdf_with_tables.pdf")
        self.assertEqual(text.count("640"), 1)
        self.assertEqual(text.count("Погрузчик электрический"), 1)

    def test_pdf_without_tables_unchanged(self):
        """В ТЗ хакатона таблиц нет — текст как у page.extract_text()."""
        import pdfplumber
        with pdfplumber.open(FIXTURES / "test_hakaton_Q3.pdf") as pdf:
            expected = "\n\n".join(t for t in (p.extract_text() for p in pdf.pages) if t)
        self.assertEqual(_extract("test_hakaton_Q3.pdf"), expected)
        self.assertNotIn("[Таблица", expected)


if __name__ == "__main__":
    unittest.main()
