"""BlobMembreteSource against real Azurite blob storage (spec C3 REVISED,
REQ-4): the identical fetch path prod uses — infra uploads the real letterheads
to the private `membretes` container; here synthetic sanitized ones stand in.
"""

import io
import uuid

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from PIL import Image as PILImage

from pipeline.claim_data import ClaimType
from pipeline.membrete_source import BlobMembreteSource
from pipeline.pdf_gen import generate_pdf_from_email

AZURITE_CONNECTION_STRING = "UseDevelopmentStorage=true"

MEMBRETE_NAMES = ("normal.png", "urgente.png", "asistencia.png")


def _synthetic_png() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (600, 80), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def membrete_container():
    """A fresh Azurite container seeded with sanitized synthetic letterheads."""
    service = BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING)
    container = f"membretes-test-{uuid.uuid4().hex[:12]}"
    client = service.create_container(container)
    png = _synthetic_png()
    for name in MEMBRETE_NAMES:
        client.upload_blob(name, png)
    yield container
    service.delete_container(container)


def test_blob_source_serves_pdf_generation(membrete_container):
    source = BlobMembreteSource.from_connection_string(
        AZURITE_CONNECTION_STRING, membrete_container
    )
    pdf = generate_pdf_from_email(
        "Compañía: Reale\nNif: H12345678\n",
        claim_type=ClaimType.DECLARACION_SINIESTRO,
        membrete_source=source,
    )
    assert pdf[:4] == b"%PDF"
    assert b"/XObject" in pdf


def test_blob_source_caches_bytes(membrete_container):
    source = BlobMembreteSource.from_connection_string(
        AZURITE_CONNECTION_STRING, membrete_container
    )
    first = source.get("normal.png")
    second = source.get("normal.png")
    assert first == second
    assert "normal.png" in source._cache


def test_missing_blob_raises(membrete_container):
    source = BlobMembreteSource.from_connection_string(
        AZURITE_CONNECTION_STRING, membrete_container
    )
    with pytest.raises(ResourceNotFoundError):
        source.get("does-not-exist.png")
