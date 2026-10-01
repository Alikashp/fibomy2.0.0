FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# LibreOffice (PPTX → PDF нового движка, D-001) и шрифты: Liberation Sans —
# метрический клон Arial (D-027); DejaVu и Noto — подстановка недостающих глифов
# (₽, ₸ и т.п., docs/SPIKE_PPTX.md, 3.2). Playwright остаётся до удаления старого
# движка (docs/design/08_MIGRATION.md, сессия 5).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-impress \
       fonts-liberation fonts-crosextra-carlito fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Зависимости отдельным слоем — кешируется при изменении только кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright браузеры уже в образе — не скачиваем
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Исходники, промпты и декларативные файлы нового движка (src/core/paths.py)
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY layouts/ ./layouts/
COPY themes/ ./themes/
COPY fonts/ ./fonts/
COPY assets/ ./assets/

# Запуск
CMD ["python", "src/main.py"]
