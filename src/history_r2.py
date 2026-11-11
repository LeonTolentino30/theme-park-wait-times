"""Private GET-only R2 storage and bounded validated memory caching. No startup I/O."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import http.client
import re
from threading import Lock
import time
from urllib.parse import quote
from . import history_snapshots as snapshots

MANIFEST_LIMIT = 16_384


class StorageUnavailable(snapshots.SnapshotError):
    def __init__(self, retry_after=30):
        super().__init__("Historical snapshot storage is temporarily unavailable")
        self.retry_after = max(5, retry_after)


@dataclass(frozen=True)
class R2Config:
    account_id: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{32}", self.account_id) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
            raise ValueError("Invalid private history storage configuration")
        for value in (self.access_key_id, self.secret_access_key):
            if not isinstance(value, str) or not 8 <= len(value) <= 256 or not re.fullmatch(r"[A-Za-z0-9/+=_.-]+", value):
                raise ValueError("Invalid private history credentials")

    @classmethod
    def from_env(cls, env):
        # Explicit names only: never scan/log the environment or use publisher keys.
        try:
            return cls(env["QUEUE_ATLAS_R2_ACCOUNT_ID"], env["QUEUE_ATLAS_R2_BUCKET"],
                       env["QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID"], env["QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY"])
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError("Private history storage is not fully configured") from error


def signed_get(host, path, access_key, secret_key, now, *, region="auto", extra_headers=None):
    """AWS SigV4 GET only. Tested against AWS's published S3 signature vector."""
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    body_hash = hashlib.sha256(b"").hexdigest()
    headers = {"host": host, "x-amz-content-sha256": body_hash, "x-amz-date": stamp}
    headers.update(extra_headers or {})
    names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{' '.join(headers[key].split())}\n" for key in sorted(headers))
    canonical = "\n".join(("GET", path, "", canonical_headers, names, body_hash))
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = ("AWS4"+secret_key).encode()
    for component in (date, region, "s3", "aws4_request"):
        key = hmac.new(key, component.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},SignedHeaders={names},Signature={signature}"
    return headers


@dataclass(frozen=True)
class ObjectResult:
    status: int
    body: bytes
    etag: str | None = None
    retry_after: int = 30


def retry_delay(value, now):
    try:
        if not value or len(value) > 128:
            return 30
        if value.isdigit():
            return max(5, int(value))
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            return 30
        return max(5, int((when-now).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return 30


class R2ReadOnlyStore:
    """Only signed object GETs; no list/PUT/delete/proxy/presigned URL functionality."""
    def __init__(self, config, *, transport=None, wall_clock=None, monotonic=None):
        self.config = config
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._clock = monotonic or time.monotonic
        self._transport = transport or self._https_get

    def _https_get(self, host, path, headers, limit):
        # HTTPS certificate verification is provided by the standard-library default.
        # No redirects or proxy-environment handling: credentials stay on this host.
        connection = http.client.HTTPSConnection(host, timeout=2)
        deadline = self._clock()+4
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            status = response.status
            if status not in (200, 304):
                return ObjectResult(status, b"", retry_after=retry_delay(response.getheader("Retry-After"), self._wall_clock()))
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise StorageUnavailable()
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise StorageUnavailable()
            chunks, size = [], 0
            if status == 200:
                while True:
                    left = deadline-self._clock()
                    if left <= 0:
                        raise StorageUnavailable()
                    if connection.sock is not None:
                        connection.sock.settimeout(min(2, left))
                    chunk = response.read(min(65_536, limit-size+1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise StorageUnavailable()
                    chunks.append(chunk)
                if length is not None and size != int(length):
                    raise StorageUnavailable()
            etag = response.getheader("ETag")
            if etag is not None and (len(etag) > 256 or any(ord(ch)<32 or ord(ch)>126 for ch in etag)):
                raise StorageUnavailable()
            return ObjectResult(status, b"".join(chunks), etag)
        finally:
            connection.close()

    def fetch(self, key, *, etag=None):
        if key != "manifest.json" and not re.fullmatch(r"history/v[12]/[0-9a-f]{64}\.json", key):
            raise StorageUnavailable()
        if etag is not None and (len(etag)>256 or any(ord(ch)<32 or ord(ch)>126 for ch in etag)):
            raise StorageUnavailable()
        host = self.config.account_id+".r2.cloudflarestorage.com"
        path = "/"+quote(self.config.bucket, safe="")+"/"+quote(key, safe="/")
        headers = signed_get(host, path, self.config.access_key_id, self.config.secret_access_key,
                             self._wall_clock(), extra_headers={"if-none-match": etag} if etag else None)
        limit = MANIFEST_LIMIT if key == "manifest.json" else snapshots.MAX_BYTES
        try:
            result = self._transport(host, path, headers, limit)
            if result.status not in {200, 304}:
                raise StorageUnavailable(result.retry_after)
            if (not isinstance(result.body, bytes) or len(result.body)>limit
                    or result.status == 304 and (key != "manifest.json" or etag is None or result.body)):
                raise StorageUnavailable()
            return result
        except StorageUnavailable:
            raise
        except Exception as error:
            raise StorageUnavailable() from error

    def get(self, key):
        result = self.fetch(key)
        if result.status != 200:
            raise StorageUnavailable()
        return result.body


class ValidatedSnapshotCache:
    """One bounded snapshot per worker, atomic refresh, TTL/ETag, fail-closed cooldown."""
    def __init__(self, store, *, ttl=300, clock=None, wall_clock=None, allow_fixtures=False):
        if type(ttl) is not int or not 30 <= ttl <= 900:
            raise ValueError("History cache TTL must be 30-900 seconds")
        self.store = store
        self.ttl = ttl
        self._clock = clock or time.monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc))
        self._allow_fixtures = allow_fixtures # no environment option enables this test/demo seam
        self._lock = Lock()
        self._manifest = self._raw = self._etag = self._registry = None
        self._expires = self._retry_at = 0
        self._failed = False

    def snapshot(self, registry):
        registry_key = hashlib.sha256(snapshots.canonical(registry)).digest()
        with self._lock:
            now = self._clock()
            same_registry = self._registry == registry_key
            if self._failed and now < self._retry_at:
                raise StorageUnavailable(max(5, int(self._retry_at-now)))
            if self._raw is not None and same_registry and now < self._expires:
                return snapshots.decode(self._raw) # return a copy; callers cannot mutate the cache
            try:
                conditional = self._etag if same_registry and self._raw is not None else None
                result = self.store.fetch("manifest.json", etag=conditional)
                if result.status == 304:
                    if conditional is None or self._raw is None:
                        raise StorageUnavailable()
                    manifest, raw = self._manifest, self._raw
                else:
                    manifest = result.body
                    if len(manifest) > MANIFEST_LIMIT:
                        raise StorageUnavailable()
                    info = snapshots.decode(manifest)
                    if not isinstance(info, dict) or not isinstance(info.get("key"), str):
                        raise StorageUnavailable()
                    # Validate manifest before following any key (even an apparently valid key).
                    class Capture:
                        def __init__(self, backing, cached_key, cached_raw):
                            self.raw = None; self.backing = backing; self.cached_key = cached_key; self.cached_raw = cached_raw
                        def get(inner, key):
                            if key == "manifest.json":return manifest
                            inner.raw = inner.cached_raw if key == inner.cached_key and inner.cached_raw is not None else inner.backing.get(key)
                            return inner.raw
                    old = snapshots.decode(self._manifest) if self._manifest else {}
                    capture = Capture(self.store, old.get("key"), self._raw)
                    data = snapshots.load(capture, registry, allow_real=True)
                    raw = capture.raw
                    if data["dataset_kind"] != "collected_observations" and not self._allow_fixtures:
                        raise StorageUnavailable()
                data = snapshots.load(snapshots.MemorySnapshotStore({"manifest.json":manifest, snapshots.decode(manifest)["key"]:raw}), registry, allow_real=True)
                if snapshots.utc(data["generated_at"]) > self._wall():
                    raise StorageUnavailable()
                if data["dataset_kind"] != "collected_observations" and not self._allow_fixtures:
                    raise StorageUnavailable()
                if self._raw is not None and snapshots.utc(data["generated_at"]) < snapshots.utc(snapshots.decode(self._raw)["generated_at"]):
                    raise StorageUnavailable()
                self._manifest, self._raw, self._registry = manifest, raw, registry_key
                self._etag = result.etag or (self._etag if result.status == 304 else None)
                self._expires = self._clock()+self.ttl
                self._failed = False
                return snapshots.decode(raw)
            except Exception as error:
                delay = error.retry_after if isinstance(error, StorageUnavailable) else 30
                self._failed, self._retry_at = True, self._clock()+delay
                # Keep prior bytes for a later conditional recovery, but never serve them on failure.
                raise StorageUnavailable(delay) from error
