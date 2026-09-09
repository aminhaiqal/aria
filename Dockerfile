FROM node:22-bookworm-slim AS reader-ui

WORKDIR /reader

COPY frontend/reader/package.json frontend/reader/package-lock.json ./
RUN npm ci
COPY frontend/reader ./
RUN npm run build

FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system aria \
    && adduser --system --ingroup aria aria \
    && mkdir -p /var/lib/aria/artifacts /var/lib/aria/reader-ui \
    && chown -R aria:aria /var/lib/aria

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY manage.py ./manage.py
COPY --from=reader-ui /reader/dist /app/frontend/reader/dist
RUN pip install --upgrade pip && pip install --editable .

USER aria

EXPOSE 8000

CMD ["gunicorn", "aria.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-"]

FROM base AS app

FROM pgvector/pgvector:0.8.2-pg17-bookworm AS postgres-tools

FROM postgres-tools AS postgres-runtime-libraries
RUN mkdir -p /postgres-runtime-libraries \
    && case "$(uname -m)" in \
        x86_64) architecture=x86_64-linux-gnu ;; \
        aarch64) architecture=aarch64-linux-gnu ;; \
        *) echo "Unsupported backup architecture" >&2; exit 1 ;; \
    esac \
    && cp -a /usr/lib/"${architecture}"/libpq.so.5* /postgres-runtime-libraries/

FROM base AS backup

USER root
COPY --from=postgres-tools /usr/lib/postgresql/17 /usr/lib/postgresql/17
ENV PATH="/usr/lib/postgresql/17/bin:${PATH}" \
    LD_LIBRARY_PATH="/usr/local/lib"
RUN apt-get update \
    && apt-get install --yes --no-install-recommends age libpq5 \
    && mkdir -p /var/lib/aria/backups \
    && chown aria:aria /var/lib/aria/backups \
    && rm -rf /var/lib/apt/lists/*
COPY --from=postgres-runtime-libraries /postgres-runtime-libraries/ /usr/local/lib/
RUN ldconfig
USER aria

FROM base AS browser

USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir "playwright==1.61.0" \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright
USER aria

FROM base AS ocr

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ghostscript \
        ocrmypdf \
        tesseract-ocr-eng \
        tesseract-ocr-msa \
    && rm -rf /var/lib/apt/lists/*
USER aria
