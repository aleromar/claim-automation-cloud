"""BlobMembreteSource cache + lock guards (spec C3 REVISED; delta review
2026-08-21). The real blob transport is covered by
tests/integration/test_membrete_blob.py — here the container client is faked
so download counts are observable.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from pipeline.membrete_source import BlobMembreteSource

PAYLOAD = b"png-bytes"


def _source_with_counting_container():
    service = MagicMock()
    container = service.get_container_client.return_value
    container.download_blob.return_value.readall.return_value = PAYLOAD
    return BlobMembreteSource(service, "membretes"), container


def test_get_downloads_once_and_caches():
    source, container = _source_with_counting_container()

    assert source.get("normal.png") == PAYLOAD
    assert source.get("normal.png") == PAYLOAD
    assert container.download_blob.call_count == 1


def test_concurrent_gets_download_once():
    """Timer + process-now can overlap; the lock must serialize the fetch so
    the unsafe transport is never entered concurrently (cf. StateStore)."""
    source, container = _source_with_counting_container()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: source.get("normal.png"), range(32)))

    assert all(result == PAYLOAD for result in results)
    assert container.download_blob.call_count == 1
