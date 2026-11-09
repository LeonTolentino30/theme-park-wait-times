"""Explicit, one-shot ThemeParks.wiki reads. No model, app, or scheduler coupling."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from http.client import HTTPException
from pathlib import Path
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from uuid import UUID

BASE_URL = "https://api.themeparks.wiki/v1"
PARK_IDS = frozenset(("eb3f4560-2383-4a36-9152-6b3e5ed6bc57", "267615cc-8943-4c2a-ae2c-5da728ca591f"))
REGISTRY_PATH = Path(__file__).resolve().parents[1] / "private/themeparks/attraction_registry.proposed.json"
MAX_BYTES = 2_000_000
STATUSES = {"OPERATING", "DOWN", "CLOSED", "REFURBISHMENT"}


class ProviderError(Exception):
    """Safe error without upstream body, URLs, paths, or credentials."""
    def __init__(self, message, *, http_status=None, retry_after_seconds=None):
        super().__init__(message)
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class InvalidResponse(ProviderError):
    pass


class RateLimited(ProviderError):
    def __init__(self, retry_after_seconds=None):
        super().__init__("Provider rate limit reached; defer further requests.", http_status=429)
        self.retry_after_seconds = retry_after_seconds


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utc_now():
    return datetime.now(timezone.utc)


def aware_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Expected timezone-aware datetime")
    return value.astimezone(timezone.utc)


def timestamp(value):
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError()
        return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        raise InvalidResponse("Invalid provider timestamp.") from None


def retry_delay(value, now):
    try:
        if value is None:
            return None
        if value.strip().isdigit():
            return int(value)
        return max(0, int((aware_utc(parsedate_to_datetime(value)) - now).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(frozen=True)
class Attraction:
    alias: str
    provider_name: str | None
    park_id: str
    attraction_id: str | None
    retired: bool
    mapping_status: str

    def __post_init__(self):
        if self.park_id not in PARK_IDS or not isinstance(self.alias, str) or not self.alias:
            raise ValueError("Invalid attraction registry entry")
        if type(self.retired) is not bool or self.mapping_status not in {"verified", "manual_review"}:
            raise ValueError("Invalid registry review status")
        if self.attraction_id is not None:
            if not isinstance(self.attraction_id, str) or str(UUID(self.attraction_id)) != self.attraction_id:
                raise ValueError("Expected canonical attraction UUID")
        if self.mapping_status == "verified" and (not self.attraction_id or not self.provider_name):
            raise ValueError("Verified mappings require provider name and UUID")

    @property
    def collectable(self):
        return not self.retired and self.mapping_status == "verified"


def load_registry(path=REGISTRY_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["provider"] != "ThemeParks.wiki":
            raise ValueError()
        entries = tuple(Attraction(**{key: row[key] for key in Attraction.__dataclass_fields__}) for row in data["attractions"])
        if not entries or len({r.alias for r in entries}) != len(entries):
            raise ValueError()
        ids = [r.attraction_id for r in entries if r.attraction_id]
        if len(ids) != len(set(ids)):
            raise ValueError()
        return entries
    except (OSError, ValueError, KeyError, TypeError):
        raise ProviderError("Local attraction registry is missing or invalid; review it before use.") from None


@dataclass(frozen=True)
class Observation:
    alias: str
    park_id: str
    attraction_id: str
    status: str
    standby_wait_minutes: int | None
    provider_last_changed_at: datetime | None
    fetched_at: datetime
    present: bool
    quality_flags: tuple[str, ...]
    source: str = "ThemeParks.wiki"

    @property
    def usable_wait(self):
        return self.status == "OPERATING" and self.standby_wait_minutes is not None


def normalize_live(payload, park_id, registry, fetched_at):
    fetched_at = aware_utc(fetched_at)
    if not isinstance(payload, dict) or payload.get("id") != park_id or not isinstance(payload.get("liveData"), list):
        raise InvalidResponse("Invalid live response envelope.")
    by_id = {}
    for row in payload["liveData"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in by_id:
            raise InvalidResponse("Invalid or duplicate live entity.")
        by_id[row["id"]] = row
    observations = []
    for attraction in registry:
        if attraction.park_id != park_id or not attraction.collectable:
            continue
        row = by_id.get(attraction.attraction_id)
        flags = []
        if row is None:
            status, wait, changed = "UNAVAILABLE", None, None
            flags.append("missing_entity")
        else:
            raw_status = row.get("status")
            if raw_status is not None and not isinstance(raw_status, str):
                raise InvalidResponse("Invalid operating status.")
            status = raw_status if raw_status in STATUSES else "UNKNOWN"
            if status == "UNKNOWN":
                flags.append("unknown_status")
            queue = row.get("queue")
            if queue is None:
                queue = {}
            if not isinstance(queue, dict):
                raise InvalidResponse("Invalid queue data.")
            standby = queue.get("STANDBY")
            if standby is None:
                standby = {}
            if not isinstance(standby, dict):
                raise InvalidResponse("Invalid standby queue.")
            wait = standby.get("waitTime")
            if wait is not None and (type(wait) is not int or wait < 0):
                raise InvalidResponse("Invalid standby wait.")
            if wait is None:
                flags.append("missing_wait")
            elif status != "OPERATING":
                flags.append("non_operating_wait")
            changed = timestamp(row.get("lastUpdated"))
            if changed is None:
                flags.append("missing_provider_timestamp")
            elif changed > fetched_at:
                flags.append("future_provider_timestamp")
        observations.append(Observation(attraction.alias, park_id, attraction.attraction_id, status, wait, changed, fetched_at, row is not None, tuple(flags)))
    return tuple(observations)


class ThemeParksProvider:
    def __init__(self, *, timeout=15, opener=None, clock=utc_now):
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError("Timeout must be between zero and 60 seconds")
        self.timeout = timeout
        self.opener = opener if opener is not None else build_opener(NoRedirect())
        self.clock = clock
        self.response_headers = {}
        self.last_response_body = None

    def _get(self, park_id, resource):
        if park_id not in PARK_IDS or resource not in {"children", "live"}:
            raise ValueError("Only supported Universal park resources are allowed")
        self.response_headers = {}
        self.last_response_body = None
        request = Request(f"{BASE_URL}/entity/{park_id}/{resource}", headers={"Accept": "application/json", "User-Agent": "QueueAtlas/Phase3B (local research adapter)"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                self.response_headers = dict(response.headers.items())
                if response.status != 200:
                    raise ProviderError("Unexpected provider HTTP status.")
                if response.headers.get_content_type() != "application/json":
                    raise InvalidResponse("Expected provider JSON content.")
                raw = response.read(MAX_BYTES + 1)
                if len(raw) <= MAX_BYTES:
                    self.last_response_body = raw
            if len(raw) > MAX_BYTES:
                raise InvalidResponse("Provider response exceeds size limit.")
            data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return data, aware_utc(self.clock())
        except HTTPError as error:
            status = error.code
            self.response_headers = dict(error.headers.items())
            delay = retry_delay(error.headers.get("Retry-After"), aware_utc(self.clock()))
            error.close()
            if status == 429:
                raise RateLimited(delay) from None
            raise ProviderError(f"Provider HTTP request failed ({status}).", http_status=status, retry_after_seconds=delay) from None
        except (TimeoutError, socket.timeout):
            raise ProviderError("Provider request timed out.") from None
        except URLError:
            raise ProviderError("Provider connection failed.") from None
        except (UnicodeError, ValueError, RecursionError):
            raise InvalidResponse("Provider returned malformed JSON.") from None
        except (OSError, HTTPException):
            raise ProviderError("Provider request failed.") from None

    def directory(self, park_id):
        data, _ = self._get(park_id, "children")
        if not isinstance(data, dict) or data.get("id") != park_id or data.get("entityType") != "PARK" or not isinstance(data.get("children"), list):
            raise InvalidResponse("Invalid park directory response.")
        return data

    def live(self, park_id, registry):
        entries = tuple(registry)
        if not any(a.park_id == park_id and a.collectable for a in entries):
            raise ValueError("No reviewed active mappings for this park")
        payload, fetched_at = self._get(park_id, "live")
        return normalize_live(payload, park_id, entries, fetched_at)
