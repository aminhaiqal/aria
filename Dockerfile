FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS reader-ui

WORKDIR /reader

COPY frontend/reader/package.json frontend/reader/package-lock.json ./
RUN npm ci
COPY frontend/reader ./
RUN npm run build

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system aria \
    && adduser --system --ingroup aria aria \
    && mkdir -p /var/lib/aria/artifacts /var/lib/aria/reader-ui \
    && chown -R aria:aria /var/lib/aria

COPY requirements.lock ./
RUN pip install --require-hashes --requirement requirements.lock
COPY config/certificates ./config/certificates
COPY src ./src
COPY manage.py ./manage.py
COPY --from=reader-ui /reader/dist /app/frontend/reader/dist

USER aria

EXPOSE 8000

CMD ["gunicorn", "aria.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-"]

FROM base AS app

FROM base AS test

USER root
COPY requirements-dev.lock ./requirements-dev.lock
RUN pip install --require-hashes --requirement requirements-dev.lock
USER aria

FROM pgvector/pgvector:0.8.2-pg17-bookworm@sha256:feb68f4f15446397d8cac7f4fe48fe4586de83160d1fc48b46283312d1a33966 AS postgres-tools

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
COPY requirements-browser.lock ./requirements-browser.lock
RUN pip install --require-hashes --requirement requirements-browser.lock \
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
