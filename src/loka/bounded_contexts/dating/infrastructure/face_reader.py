"""The face in a profile photo, as a vector, read on the server (see ``faces``).

OpenCV's YuNet finds the face, SFace turns it into 128 numbers. Both models are
baked into the image (see the Dockerfile); without them, or on any failure,
the reader answers ``None`` and the matching goes on without faces.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loka.bounded_contexts.dating.domain.faces import Vector
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStoreError, S3MediaStorage

_logger = get_logger(__name__)
MODELS = Path("/app/models")
_DETECTOR = "face_detection_yunet_2023mar.onnx"
_RECOGNIZER = "face_recognition_sface_2021dec.onnx"
_LONGEST_SIDE = 1024
_MIN_SCORE = 0.8


class OpenCvFaceReader:
    def __init__(self, storage: S3MediaStorage, models: Path = MODELS) -> None:
        self._storage = storage
        self._models = models
        self._loaded: tuple[Any, Any] | None = None

    async def vector(self, object_key: str) -> Vector | None:
        try:
            data, content_type = await self._storage.get(object_key)
        except MediaStoreError as exc:
            _logger.warning("dating_face_unreadable", error=str(exc))
            return None
        if not content_type.startswith("image/"):
            return None
        try:
            return await asyncio.to_thread(self._read, data)
        except Exception as exc:  # a face never costs the turn
            _logger.warning("dating_face_failed", error=type(exc).__name__)
            return None

    def _read(self, data: bytes) -> Vector | None:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        height, width = image.shape[:2]
        scale = min(1.0, _LONGEST_SIDE / max(height, width))
        if scale < 1.0:
            image = cv2.resize(image, (int(width * scale), int(height * scale)))
            height, width = image.shape[:2]
        detector, recognizer = self._models_loaded()
        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        if faces is None or len(faces) == 0:
            return None
        # The largest face is the member's; others are in the background.
        face = max(faces, key=lambda found: float(found[2] * found[3]))
        if float(face[14]) < _MIN_SCORE:
            return None
        aligned = recognizer.alignCrop(image, face)
        feature = recognizer.feature(aligned)
        return tuple(round(float(x), 5) for x in feature.flatten())

    def _models_loaded(self) -> tuple[Any, Any]:
        if self._loaded is None:
            import cv2

            detector = cv2.FaceDetectorYN.create(
                str(self._models / _DETECTOR), "", (320, 320), _MIN_SCORE
            )
            recognizer = cv2.FaceRecognizerSF.create(str(self._models / _RECOGNIZER), "")
            self._loaded = (detector, recognizer)
        return self._loaded


def face_reader(storage: S3MediaStorage, models: Path = MODELS) -> OpenCvFaceReader | None:
    """``None`` when the models are not there (a local run without the image)."""
    if not (models / _DETECTOR).exists() or not (models / _RECOGNIZER).exists():
        return None
    return OpenCvFaceReader(storage, models)
