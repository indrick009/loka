FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Face detection (YuNet, MIT) and recognition (SFace, Apache 2.0) from the
# OpenCV model zoo, for dating: lookalike accounts and each member's taste.
# Before the sources, so every build reuses the layer.
ARG ZOO=https://github.com/opencv/opencv_zoo/raw/main/models
RUN mkdir -p /app/models \
    && curl -fsSL -o /app/models/face_detection_yunet_2023mar.onnx \
        $ZOO/face_detection_yunet/face_detection_yunet_2023mar.onnx \
    && curl -fsSL -o /app/models/face_recognition_sface_2021dec.onnx \
        $ZOO/face_recognition_sface/face_recognition_sface_2021dec.onnx \
    && printf '%s\n' \
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  /app/models/face_detection_yunet_2023mar.onnx" \
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79  /app/models/face_recognition_sface_2021dec.onnx" \
        | sha256sum -c -

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev,agent]"

FROM base AS test
COPY .env.example ./
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests
CMD ["pytest"]

FROM base AS migrate
COPY alembic ./alembic
COPY alembic.ini ./
CMD ["alembic", "upgrade", "head"]

FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "loka.interfaces.http.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
CMD ["python", "-m", "loka.interfaces.workers.main"]