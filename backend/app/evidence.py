"""Evidence ingest.

A photograph taken at a municipal counter carries the GPS coordinates of that
counter, the camera serial number, and often the owner's name in the EXIF
maker note. A PDF scan carries the workstation username in /Author. Either one
identifies a whistleblower more reliably than a signature would.

So nothing the uploader sent is persisted. The file is decoded, re-encoded
from pixel data alone, and written under a generated name. The original
filename is discarded before it reaches the database.
"""

from __future__ import annotations

import io
import secrets
from dataclasses import dataclass

from .config import get_settings
from .observability import EVIDENCE_REJECTED
from .security import sha256_bytes
from .storage import get_storage

# Magic-byte signatures. The declared Content-Type is attacker-controlled and
# is not consulted.
_SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"%PDF-", "application/pdf", ".pdf"),
]


class EvidenceRejected(ValueError):
    def __init__(self, message: str, reason: str = "other") -> None:
        super().__init__(message)
        self.reason = reason
        EVIDENCE_REJECTED.labels(reason).inc()


@dataclass
class StoredEvidence:
    stored_name: str
    media_type: str
    size_bytes: int
    sha256: str
    metadata_stripped: bool


def _sniff(data: bytes) -> tuple[str, str]:
    if data[8:12] == b"WEBP" and data[:4] == b"RIFF":
        return "image/webp", ".webp"
    for magic, media_type, ext in _SIGNATURES:
        if data.startswith(magic):
            return media_type, ext
    raise EvidenceRejected(
        "That file type is not accepted. Attach a JPEG, PNG, WebP or PDF - "
        "office documents and archives can carry tracking data we cannot reliably remove.",
        reason="unsupported_type",
    )


def _strip_image(data: bytes, media_type: str) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im.load()
        fmt = im.format
        # getdata()/putdata() carries pixels across into a new image object,
        # leaving every EXIF, XMP and ICC block behind.
        clean = Image.new(im.mode, im.size)
        clean.putdata(list(im.getdata()))
        out = io.BytesIO()
        clean.save(out, format=fmt)
        return out.getvalue()


def _strip_pdf(data: bytes) -> tuple[bytes, bool]:
    try:
        import pikepdf
    except ImportError:  # pragma: no cover - deployment must install it
        raise EvidenceRejected(
            "PDF sanitising is unavailable on this server. Photograph the document instead.",
            reason="pdf_tooling_missing",
        ) from None
    out = io.BytesIO()
    # Two separate places carry authorship in a PDF and both must go:
    #   /Info in the trailer  - "Author", usually the workstation username
    #   /Metadata on the root - the XMP packet, which repeats it in XML
    # pikepdf types these as dynamic Objects, so mypy cannot verify the calls;
    # tests/test_evidence.py asserts against the saved bytes instead.
    with pikepdf.open(io.BytesIO(data)) as pdf:  # type: ignore[attr-defined]
        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        pdf.save(out, linearize=False)  # type: ignore[operator,arg-type]
    return out.getvalue(), True


def ingest(raw: bytes) -> StoredEvidence:
    s = get_settings()
    if not raw:
        raise EvidenceRejected("That file is empty.", reason="empty")
    limit = s.max_evidence_mb * 1024 * 1024
    if len(raw) > limit:
        raise EvidenceRejected(f"Files must be {s.max_evidence_mb} MB or smaller.", reason="too_large")

    media_type, ext = _sniff(raw)
    if media_type == "application/pdf":
        clean, stripped = _strip_pdf(raw)
    else:
        clean, stripped = _strip_image(raw, media_type), True

    stored_name = secrets.token_hex(16) + ext
    get_storage().put(stored_name, clean, media_type)

    return StoredEvidence(
        stored_name=stored_name,
        media_type=media_type,
        size_bytes=len(clean),
        sha256=sha256_bytes(clean),
        metadata_stripped=stripped,
    )
