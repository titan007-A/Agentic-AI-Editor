"""Videos for Buffer kept on Cloudflare R2 instead of on this machine.

Buffer takes no uploads: it, then Instagram, fetch the video from a link when the post goes
out. Served from this machine, a big reel can fail at that moment, when several go out
together over its home connection (Instagram says only "ERROR"). So when an admin has set
R2 up, each video is put in the R2 bucket first and Buffer gets the bucket's public link;
buffer_posts deletes it once the post went out. Without R2, or when the upload fails, the
editor's own link is used as before.

Setting up (Cloudflare dashboard): R2 > Create bucket; the bucket's Settings > Custom
Domains > Connect a domain (a subdomain of a site on Cloudflare); R2 > Manage API tokens >
Create token with Object Read & Write on that bucket. The account ID is on the R2 page."""

import json
import logging
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from agentic_editor.db import Setting

log = logging.getLogger(__name__)

SETTING = "hosting:r2"
PREFIX = "reels/"
# Bytes the bucket may hold before videos go out with the editor's own link again: R2's
# free storage is 10 GB a month.
CAP = 8_000_000_000
# The files never change (each has its own random name), so caches may keep them.
CACHE = "public, max-age=604800, immutable"


class HostingError(RuntimeError):
    pass


@dataclass
class R2:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    # The bucket's public address, such as https://media.example.com.
    public_url: str
    # Off: new videos use the editor's own link; the ones in the bucket are still deleted.
    enabled: bool = True
    # Where the S3 API is; empty: R2's, from the account ID. Set by tests.
    endpoint: str = ""

    def api(self) -> str:
        return self.endpoint or f"https://{self.account_id}.r2.cloudflarestorage.com"

    def link(self, key: str) -> str:
        return f"{self.public_url.rstrip('/')}/{key}"


def load(session: Session) -> R2 | None:
    row = session.get(Setting, SETTING)
    if row is None:
        return None
    try:
        return R2(**json.loads(row.value))
    except (ValueError, TypeError):
        log.warning("the R2 setting can't be read; ignoring it")
        return None


def save(session: Session, config: R2 | None) -> None:
    row = session.get(Setting, SETTING)
    if config is None:
        if row is not None:
            session.delete(row)
        return
    value = json.dumps(asdict(config))
    if row is None:
        session.add(Setting(key=SETTING, value=value))
    else:
        row.value = value


def usable(config: R2 | None) -> bool:
    return bool(config and config.enabled)


def client(config: R2):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.api(),
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name="auto",
        # R2 asks for no checksums beyond what S3 requires.
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 4, "mode": "standard"},
            connect_timeout=15,
            read_timeout=120,
        ),
    )


def words(exc: Exception) -> str:
    """What went wrong, in a line, without the S3 jargon where it can be helped."""
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    known = {
        "InvalidAccessKeyId": "R2 doesn't know that access key ID",
        "SignatureDoesNotMatch": "the secret access key doesn't match the access key ID",
        "AccessDenied": "the token may not write to this bucket",
        "NoSuchBucket": "there is no bucket of that name in the account",
    }
    if code in known:
        return known[code]
    return str(exc).splitlines()[0][:300] or type(exc).__name__


def upload(config: R2, path: Path, key: str) -> None:
    try:
        with open(path, "rb") as body:
            client(config).put_object(
                Bucket=config.bucket,
                Key=key,
                Body=body,
                ContentType="video/mp4",
                CacheControl=CACHE,
            )
    except Exception as exc:
        raise HostingError(words(exc)) from exc


def delete(config: R2, key: str) -> None:
    try:
        client(config).delete_object(Bucket=config.bucket, Key=key)
    except Exception as exc:
        raise HostingError(words(exc)) from exc


def check(config: R2) -> None:
    """Puts a small file in the bucket, reads it back through the public address and
    deletes it; raises HostingError saying which step failed."""
    if not config.public_url.startswith("https://"):
        raise HostingError("the public address must start with https://")
    key = f"{PREFIX}check-{secrets.token_urlsafe(9)}.txt"
    body = f"aiedit {key}".encode()
    s3 = client(config)
    try:
        s3.put_object(Bucket=config.bucket, Key=key, Body=body, ContentType="text/plain")
    except Exception as exc:
        raise HostingError(f"putting a file in the bucket failed: {words(exc)}") from exc
    try:
        try:
            got = httpx.get(config.link(key), timeout=30, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise HostingError(f"the public address can't be reached: {exc}") from exc
        if got.status_code != 200 or got.content != body:
            raise HostingError(
                f"the public address answered {got.status_code} for the file just put there: "
                "is it connected to this bucket (Settings > Custom Domains)?"
            )
    finally:
        try:
            s3.delete_object(Bucket=config.bucket, Key=key)
        except Exception as exc:
            log.warning("R2 check: deleting %s failed: %s", key, words(exc))
