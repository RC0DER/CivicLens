"""Evidence ingest must destroy what identifies the person who uploaded it.

A photograph taken at a municipal counter carries the GPS coordinates of that
counter, the camera serial number, and often the owner's name in the EXIF
maker note. These tests assert against the bytes actually written to storage,
not against what the ingest function claims to have done.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.evidence import EvidenceRejected, ingest
from app.storage import get_storage


def _jpeg_with_identifying_exif() -> bytes:
    img = Image.new("RGB", (64, 48), (120, 90, 40))
    exif = Image.Exif()
    exif[271] = "ACME"                  # Make
    exif[272] = "Counter-Cam 9000"      # Model
    exif[315] = "R. Complainant"        # Artist - names the reporter outright
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (28.0, 36.0, 0.0)
    gps[3], gps[4] = "E", (77.0, 12.0, 0.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_exif_is_gone_from_the_stored_bytes():
    raw = _jpeg_with_identifying_exif()
    assert b"ACME" in raw and b"R. Complainant" in raw  # the upload really carries it

    stored = ingest(raw)
    written = get_storage().get(stored.stored_name)

    assert b"ACME" not in written
    assert b"Counter-Cam 9000" not in written
    assert b"R. Complainant" not in written
    with Image.open(io.BytesIO(written)) as im:
        assert dict(im.getexif()) == {}
        assert im.size == (64, 48)
        assert im.getpixel((1, 1)) == (120, 90, 40)  # the evidence itself survives
    assert stored.metadata_stripped is True


def test_the_uploaders_filename_never_reaches_storage():
    """"raju_bribe_receipt.jpg" identifies a person as surely as a signature."""
    stored = ingest(_jpeg_with_identifying_exif())
    assert "raju" not in stored.stored_name
    assert stored.stored_name.endswith(".jpg")
    assert len(stored.stored_name) == 32 + 4  # generated hex, nothing of the original


def test_pdf_metadata_is_stripped():
    pikepdf = pytest.importorskip("pikepdf")

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    with pdf.open_metadata() as meta:
        meta["dc:creator"] = ["R. Complainant"]
    pdf.docinfo["/Author"] = "workstation-user"
    buf = io.BytesIO()
    pdf.save(buf)
    raw = buf.getvalue()
    assert b"workstation-user" in raw

    written = get_storage().get(ingest(raw).stored_name)
    assert b"workstation-user" not in written
    assert b"R. Complainant" not in written


def test_type_is_decided_by_content_not_by_the_declared_extension():
    """A .jpg that is really a zip is still a zip."""
    with pytest.raises(EvidenceRejected) as exc:
        ingest(b"PK\x03\x04" + b"\x00" * 64)
    assert exc.value.reason == "unsupported_type"


def test_empty_and_oversized_files_are_refused():
    from app.config import get_settings

    with pytest.raises(EvidenceRejected) as empty:
        ingest(b"")
    assert empty.value.reason == "empty"

    oversize = b"\xff\xd8\xff" + b"\x00" * (get_settings().max_evidence_mb * 1024 * 1024 + 1)
    with pytest.raises(EvidenceRejected) as big:
        ingest(oversize)
    assert big.value.reason == "too_large"


def test_a_polyglot_jpeg_is_re_encoded_not_passed_through():
    """Appended payloads after the JPEG data do not survive re-encoding."""
    raw = _jpeg_with_identifying_exif() + b"<?php system($_GET['c']); ?>"
    written = get_storage().get(ingest(raw).stored_name)
    assert b"<?php" not in written
