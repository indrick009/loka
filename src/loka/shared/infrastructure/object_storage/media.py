"""S3-compatible media storage.

PostgreSQL stores only the object key and metadata. Downloads go through
short-lived presigned URLs; identity documents use a much shorter TTL than
public listing photos.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from loka.shared.infrastructure.config.settings import ObjectStorageSettings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

IDENTITY_DOCUMENT_PREFIX = "identity"
PROPERTY_MEDIA_PREFIX = "properties"
CHAT_MEDIA_PREFIX = "chat"


class MediaStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    media_id: uuid.UUID
    object_key: str
    checksum: str
    size_bytes: int
    content_type: str
    bucket: str


class MediaStorage(Protocol):
    async def put(
        self,
        data: bytes,
        *,
        owner_id: uuid.UUID,
        prefix: str,
        content_type: str | None = None,
        encrypt: bool = False,
    ) -> StoredObject: ...

    async def presigned_download_url(self, object_key: str, *, ttl_seconds: int) -> str: ...

    async def delete(self, object_key: str) -> None: ...


class S3MediaStorage:
    """Thread-safe adapter: boto3 calls are pushed to a worker thread."""

    def __init__(self, settings: ObjectStorageSettings) -> None:
        self._settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key.get_secret_value(),
            aws_secret_access_key=settings.secret_key.get_secret_value(),
            region_name=settings.region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    async def put(
        self,
        data: bytes,
        *,
        owner_id: uuid.UUID,
        prefix: str,
        content_type: str | None = None,
        encrypt: bool = False,
    ) -> StoredObject:
        import asyncio

        media_id = uuid.uuid4()
        extension = _extension_for(content_type, data)
        object_key = f"{prefix}/{owner_id}/{media_id}{extension}"
        resolved_type = (
            content_type
            or mimetypes.guess_type(object_key)[0]
            or "application/octet-stream"
        )
        checksum = hashlib.sha256(data).hexdigest()

        extra: dict[str, str] = {"x-amz-meta-media-id": str(media_id)}
        if encrypt:
            extra["ServerSideEncryption"] = "AES256"

        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._settings.bucket,
                Key=object_key,
                Body=data,
                ContentType=resolved_type,
                Metadata=extra,
            )
        except ClientError as exc:
            raise MediaStoreError(f"object storage rejected upload: {exc}") from exc

        _logger.info("media_stored", object_key=object_key, size_bytes=len(data))
        return StoredObject(
            media_id=media_id,
            object_key=object_key,
            checksum=checksum,
            size_bytes=len(data),
            content_type=resolved_type,
            bucket=self._settings.bucket,
        )

    async def presigned_download_url(self, object_key: str, *, ttl_seconds: int) -> str:
        import asyncio

        try:
            return await asyncio.to_thread(
                self._client.generate_presigned_url,
                ClientMethod="get_object",
                Params={"Bucket": self._settings.bucket, "Key": object_key},
                ExpiresIn=ttl_seconds,
            )
        except ClientError as exc:
            raise MediaStoreError(f"cannot presign {object_key}: {exc}") from exc

    async def delete(self, object_key: str) -> None:
        import asyncio

        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._settings.bucket,
                Key=object_key,
            )
        except ClientError as exc:
            raise MediaStoreError(f"cannot delete {object_key}: {exc}") from exc


class InMemoryMediaStorage:
    """Deterministic test double."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(
        self,
        data: bytes,
        *,
        owner_id: uuid.UUID,
        prefix: str,
        content_type: str | None = None,
        encrypt: bool = False,
    ) -> StoredObject:
        media_id = uuid.uuid4()
        object_key = f"{prefix}/{owner_id}/{media_id}"
        resolved = content_type or "application/octet-stream"
        self.objects[object_key] = (data, resolved)
        return StoredObject(
            media_id=media_id,
            object_key=object_key,
            checksum=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            content_type=resolved,
            bucket="memory",
        )

    async def presigned_download_url(self, object_key: str, *, ttl_seconds: int) -> str:
        expires = datetime.utcnow().timestamp() + ttl_seconds
        return f"memory://{object_key}?expires={int(expires)}"

    async def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)


def _extension_for(content_type: str | None, data: bytes) -> str:
    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            return guessed
    return ".jpg" if data[:2] == b"\xff\xd8" else ".bin"