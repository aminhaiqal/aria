FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system aria \
    && adduser --system --ingroup aria aria \
    && mkdir -p /var/lib/aria/artifacts \
    && chown -R aria:aria /var/lib/aria

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY manage.py ./manage.py
RUN pip install --upgrade pip && pip install --editable .

USER aria

EXPOSE 8000

CMD ["gunicorn", "aria.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-"]
