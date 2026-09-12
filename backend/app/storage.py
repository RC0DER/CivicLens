"""Evidence storage: local disk for development, S3-compatible in production.

Downloads are never served as a plain path. Both backends hand out a URL that
expires, so a link copied out of a departmental portal stops working, and the
object store's own access log records who fetched what.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from abc import ABC, abstractmethod

from .config import get_settings


class StorageError(RuntimeError):
    pass


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, media_type: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str: ...


class LocalStorage(Storage):
    """Development only - production configuration refuses to start on this."""

    def __init__(self, directory: str) -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, key: str) -> str:
        # Keys are generated hex + extension; refuse anything that could walk.
        if "/" in key or "\\" in key or ".." in key:
            raise StorageError("bad object key")
        return os.path.join(self.directory, key)

    def put(self, key: str, data: bytes, media_type: str) -> None:
        with open(self._path(key), "wb") as fh:
            fh.write(data)

    def get(self, key: str) -> bytes:
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError as exc:
            raise StorageError("object not found") from exc

    def delete(self, key: str) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        s = get_settings()
        expires = int(time.time()) + (ttl_seconds or s.signed_url_ttl_seconds)
        sig = sign_local(key, expires)
        return f"/api/evidence/{key}?expires={expires}&signature={sig}"


def sign_local(key: str, expires: int) -> str:
    s = get_settings()
    return hmac.new(s.jwt_secret.encode(), f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]


def verify_local(key: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(sign_local(key, expires), signature)


class S3Storage(Storage):
    def __init__(self) -> None:
        import boto3  # lazy import keeps boto3 optional for local development

        s = get_settings()
        if not s.s3_bucket:
            raise StorageError("S3_BUCKET is not configured")
        self.bucket = s.s3_bucket
        self.client = boto3.client("s3", region_name=s.s3_region, endpoint_url=s.s3_endpoint_url)

    def put(self, key: str, data: bytes, media_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=media_type,
            # Server-side encryption, and no public read under any circumstance.
            ServerSideEncryption="AES256", ACL="private",
        )

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        s = get_settings()
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl_seconds or s.signed_url_ttl_seconds,
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = S3Storage() if s.storage_backend == "s3" else LocalStorage(s.evidence_dir)
    return _storage


def reset_for_tests() -> None:
    global _storage
    _storage = None
