# Local base tag created by scripts/docker-up.ps1 from the cached
# python:3.12-slim image, avoiding Docker Hub auth during rebuilds.
FROM comet-python-base:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300 \
    UV_HTTP_RETRIES=5

WORKDIR /app

# System dependencies. Try several signed Debian mirrors because the default
# CDN and a single mirror can both be unreliable in this environment.
RUN set -eux; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/80-retries; \
    cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.orig; \
    for pair in \
      "https://mirrors.tuna.tsinghua.edu.cn/debian|https://mirrors.tuna.tsinghua.edu.cn/debian-security" \
      "https://mirrors.ustc.edu.cn/debian|https://mirrors.ustc.edu.cn/debian-security" \
      "https://mirrors.aliyun.com/debian|https://mirrors.aliyun.com/debian-security" \
      "http://deb.debian.org/debian|http://deb.debian.org/debian-security"; do \
      mirror="${pair%%|*}"; \
      security="${pair#*|}"; \
      cp /tmp/debian.sources.orig /etc/apt/sources.list.d/debian.sources; \
      sed -i "s|http://deb.debian.org/debian-security|${security}|g; s|http://deb.debian.org/debian|${mirror}|g" /etc/apt/sources.list.d/debian.sources; \
      echo "Trying Debian mirror: ${mirror}"; \
      apt-get update && \
      apt-get install -y --no-install-recommends build-essential curl && \
      rm -rf /var/lib/apt/lists/* && \
      exit 0; \
      echo "apt install failed with mirror: ${mirror}"; \
      rm -rf /var/lib/apt/lists/*; \
      sleep 8; \
    done; \
    exit 1

RUN python -m pip install --no-cache-dir --retries 5 --timeout 120 uv

# Install dependencies first to keep Docker cache useful.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

# Put the project virtualenv on PATH for uvicorn/celery.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
