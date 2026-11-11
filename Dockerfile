FROM python:3.13-slim

# LibreOffice makes the PDF; Carlito is the Calibri-metric font (without it the PDF falls back to a
# different face and no longer matches the Word template); Noto core has the bullet symbol font.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer fonts-crosextra-carlito fonts-noto-core poppler-utils \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f

# the bullet font the template names (Noto Sans Symbols, OFL) - Debian's Noto packages don't include it
COPY fonts/NotoSansSymbols-Regular.ttf /usr/local/share/fonts/
RUN fc-cache -f

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY alembic alembic
COPY alembic.ini .
COPY scripts scripts
COPY seed seed
COPY templates templates
COPY web web

RUN useradd --create-home --uid 10001 quote && mkdir /data && chown quote /data
USER quote
ENV QUOTE_DATA_DIR=/data HOME=/home/quote PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/assets/app.css')"
# seed.py is a no-op once products exist; main.py applies migrations on startup
CMD ["sh", "-c", "python scripts/seed.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
