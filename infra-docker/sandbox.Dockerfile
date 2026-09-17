# Trajecta Deep Agents sandbox.
# Build with: docker build -f infra-docker/sandbox.Dockerfile -t trajecta-sandbox:latest .
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    curl \
    ca-certificates \
    git \
    jq \
    ripgrep \
    zip \
    unzip \
    ffmpeg \
    tesseract-ocr \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Common offline data/script tooling. Runtime network is disabled by default;
# packages that the agent needs should therefore be baked into the image.
RUN pip install --no-cache-dir \
    numpy \
    pandas \
    openpyxl \
    pillow \
    pypdf \
    python-docx \
    python-pptx \
    pyyaml \
    beautifulsoup4

RUN mkdir -p /workspace /uploads && chmod 0777 /workspace
WORKDIR /workspace

CMD ["sh", "-lc", "while true; do sleep 3600; done"]
