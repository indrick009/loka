"""The media bucket is created when missing, and only then.

Nothing used to create it: a fresh MinIO started empty and every landlord's
photo was answered with a 503. Driven through botocore's Stubber, so no test
reaches a storage server.
"""

from __future__ import annotations

import pytest
from botocore.stub import Stubber

from loka.shared.infrastructure.config.settings import ObjectStorageSettings
from loka.shared.infrastructure.object_storage.media import MediaStoreError, S3MediaStorage

pytestmark = pytest.mark.unit


def _storage() -> tuple[S3MediaStorage, Stubber]:
    storage = S3MediaStorage(ObjectStorageSettings())
    return storage, Stubber(storage._client)


async def test_a_missing_bucket_is_created() -> None:
    storage, stub = _storage()
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stub.add_response("create_bucket", {}, {"Bucket": "loka-media"})

    with stub:
        created = await storage.ensure_bucket()

    assert created is True
    stub.assert_no_pending_responses()


async def test_an_existing_bucket_is_left_alone() -> None:
    storage, stub = _storage()
    stub.add_response("head_bucket", {}, {"Bucket": "loka-media"})

    with stub:
        created = await storage.ensure_bucket()

    assert created is False
    stub.assert_no_pending_responses()


async def test_a_bucket_it_may_not_read_is_reported_not_recreated() -> None:
    storage, stub = _storage()
    stub.add_client_error("head_bucket", service_error_code="403", http_status_code=403)

    with stub, pytest.raises(MediaStoreError):
        await storage.ensure_bucket()
