"""Fabricated provider fixtures only. No network access or captured provider data."""
from datetime import datetime, timezone
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from src import themeparks_provider as tp

PARK = sorted(tp.PARK_IDS)[0]
OTHER = sorted(tp.PARK_IDS)[1]
ENTITY = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 11, 8, 15, tzinfo=timezone.utc)
ACTIVE = tp.Attraction("Fabricated Ride", "Fabricated Provider Ride", PARK, ENTITY, False, "verified")


def envelope(row=None):
    if row is None:
        row = {"id": ENTITY, "status": "OPERATING", "queue": {"STANDBY": {"waitTime": 15}}, "lastUpdated": "2026-11-08T09:55:00-05:00"}
    return {"id": PARK, "liveData": [row]}


def normalize(row):
    return tp.normalize_live(envelope(row), PARK, [ACTIVE], NOW)[0]


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json", status=200):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type


def client(body=None, *, error=None, content_type="application/json", status=200):
    opener = Mock()
    if error:
        opener.open.side_effect = error
    else:
        opener.open.return_value = Response(body if body is not None else json.dumps(envelope()).encode(), content_type, status)
    return tp.ThemeParksProvider(opener=opener, clock=lambda: NOW), opener


class NormalizationTests(unittest.TestCase):
    def test_operating_wait_and_timezone(self):
        observation = tp.normalize_live(envelope(), PARK, [ACTIVE], NOW)[0]
        self.assertEqual(observation.standby_wait_minutes, 15)
        self.assertTrue(observation.usable_wait)
        self.assertEqual(observation.provider_last_changed_at.hour, 14)
        self.assertEqual(observation.fetched_at, NOW)
        self.assertEqual(observation.source, "ThemeParks.wiki")

    def test_zero_distinct_from_missing_and_closed(self):
        zero = normalize({"id": ENTITY, "status": "OPERATING", "queue": {"STANDBY": {"waitTime": 0}}})
        missing = normalize({"id": ENTITY, "status": "OPERATING"})
        closed = normalize({"id": ENTITY, "status": "CLOSED", "queue": {"STANDBY": {"waitTime": 0}}})
        self.assertEqual(zero.standby_wait_minutes, 0)
        self.assertTrue(zero.usable_wait)
        self.assertIsNone(missing.standby_wait_minutes)
        self.assertFalse(missing.usable_wait)
        self.assertFalse(closed.usable_wait)
        self.assertEqual(closed.status, "CLOSED")
        self.assertIn("non_operating_wait", closed.quality_flags)

    def test_all_nonoperating_statuses(self):
        for status in ("CLOSED", "DOWN", "REFURBISHMENT"):
            with self.subTest(status=status):
                item = normalize({"id": ENTITY, "status": status})
                self.assertEqual(item.status, status)
                self.assertIsNone(item.standby_wait_minutes)
                self.assertFalse(item.usable_wait)

    def test_absent_entity_not_closed(self):
        item = tp.normalize_live({"id": PARK, "liveData": []}, PARK, [ACTIVE], NOW)[0]
        self.assertEqual(item.status, "UNAVAILABLE")
        self.assertFalse(item.present)
        self.assertIsNone(item.standby_wait_minutes)

    def test_null_queue_and_missing_wait(self):
        for queue in (None, {}, {"STANDBY": None}, {"STANDBY": {"waitTime": None}}):
            with self.subTest(queue=queue):
                self.assertIsNone(normalize({"id": ENTITY, "status": "OPERATING", "queue": queue}).standby_wait_minutes)

    def test_invalid_waits_and_queue_shapes(self):
        for wait in (-1, True, 1.5, "10", [], {}):
            with self.subTest(wait=wait), self.assertRaises(tp.InvalidResponse):
                normalize({"id": ENTITY, "status": "OPERATING", "queue": {"STANDBY": {"waitTime": wait}}})
        for queue in ([], "bad", {"STANDBY": []}):
            with self.assertRaises(tp.InvalidResponse):
                normalize({"id": ENTITY, "queue": queue})

    def test_unknown_status_is_not_operating(self):
        for status in (None, "NEW_STATUS"):
            item = normalize({"id": ENTITY, "status": status})
            self.assertEqual(item.status, "UNKNOWN")
            self.assertIn("unknown_status", item.quality_flags)
        with self.assertRaises(tp.InvalidResponse):
            normalize({"id": ENTITY, "status": []})

    def test_provider_timestamps(self):
        for value in ("not-a-date", "2026-11-08T10:00:00", 123):
            with self.assertRaises(tp.InvalidResponse):
                normalize({"id": ENTITY, "lastUpdated": value})
        future = normalize({"id": ENTITY, "lastUpdated": "2030-01-01T00:00:00Z"})
        self.assertIn("future_provider_timestamp", future.quality_flags)
        old = normalize({"id": ENTITY, "lastUpdated": "2020-01-01T00:00:00Z"})
        self.assertNotIn("stale", old.quality_flags)  # Last change alone cannot prove stale transport.

    def test_invalid_envelopes_duplicates_and_rows(self):
        for payload in (None, [], {}, {"id": OTHER, "liveData": []}, {"id": PARK, "liveData": {}}, {"id": PARK, "liveData": [None]}, {"id": PARK, "liveData": [{"id": ENTITY}, {"id": ENTITY}]}):
            with self.subTest(payload=payload), self.assertRaises(tp.InvalidResponse):
                tp.normalize_live(payload, PARK, [ACTIVE], NOW)

    def test_retired_uncertain_and_other_park_excluded(self):
        retired = tp.Attraction("Retired", None, PARK, None, True, "manual_review")
        uncertain = tp.Attraction("Uncertain", None, PARK, None, False, "manual_review")
        other = tp.Attraction("Other", "Other", OTHER, ENTITY, False, "verified")
        result = tp.normalize_live(envelope(), PARK, [ACTIVE, retired, uncertain, other], NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].alias, ACTIVE.alias)


class TransportTests(unittest.TestCase):
    def test_fixed_documented_live_url_timeout_and_single_request(self):
        provider, opener = client()
        self.assertEqual(len(provider.live(PARK, [ACTIVE])), 1)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, f"{tp.BASE_URL}/entity/{PARK}/live")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 15})
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_directory_validation(self):
        payload = {"id": PARK, "entityType": "PARK", "children": []}
        provider, _ = client(json.dumps(payload).encode())
        self.assertEqual(provider.directory(PARK), payload)
        provider, _ = client(b'{}')
        with self.assertRaises(tp.InvalidResponse):
            provider.directory(PARK)

    def test_no_active_mappings_or_unsupported_park_no_request(self):
        provider, opener = client()
        for park, entries in ((PARK, []), ("untrusted", [ACTIVE])):
            with self.assertRaises(ValueError):
                provider.live(park, entries)
        with self.assertRaises(ValueError):
            provider.directory("https://untrusted.example")
        opener.open.assert_not_called()

    def test_timeouts_network_failures_no_retry(self):
        for error in (TimeoutError(), URLError("SECRET upstream detail"), OSError("SECRET")):
            provider, opener = client(error=error)
            with self.assertRaises(tp.ProviderError) as raised:
                provider.live(PARK, [ACTIVE])
            self.assertNotIn("SECRET", str(raised.exception))
            opener.open.assert_called_once()

    def test_http_failures_sanitized(self):
        for status in (301, 401, 403, 404, 500, 503):
            error = HTTPError("SECRET", status, "SECRET", {}, io.BytesIO(b"SECRET"))
            provider, opener = client(error=error)
            with self.assertRaises(tp.ProviderError) as raised:
                provider.live(PARK, [ACTIVE])
            self.assertNotIn("SECRET", str(raised.exception))
            opener.open.assert_called_once()

    def test_rate_limit_seconds_http_date_and_invalid_header(self):
        for header, delay in (("120", 120), ("Sun, 08 Nov 2026 15:02:00 GMT", 120), ("bad", None), (None, None)):
            headers = {} if header is None else {"Retry-After": header}
            provider, opener = client(error=HTTPError("SECRET", 429, "rate limit", headers, io.BytesIO()))
            with self.assertRaises(tp.RateLimited) as raised:
                provider.live(PARK, [ACTIVE])
            self.assertEqual(raised.exception.retry_after_seconds, delay)
            opener.open.assert_called_once()

    def test_bad_json_content_type_size_and_nonstandard_constants(self):
        for body, content_type in ((b'{bad', 'application/json'), (b'NaN', 'application/json'), (b'{}', 'text/html'), (b' ' * (tp.MAX_BYTES + 1), 'application/json')):
            provider, _ = client(body, content_type=content_type)
            with self.assertRaises(tp.InvalidResponse):
                provider.live(PARK, [ACTIVE])

    def test_cache_metadata_and_private_body_available(self):
        provider, opener = client()
        provider.live(PARK, [ACTIVE])
        self.assertIn("Content-Type", provider.response_headers)
        self.assertIsInstance(provider.last_response_body, bytes)
        self.assertEqual(json.loads(provider.last_response_body)["id"], PARK)

    def test_retry_after_on_service_unavailable(self):
        provider, opener = client(error=HTTPError("SECRET",503,"Unavailable",{"Retry-After":"600"},io.BytesIO()))
        with self.assertRaises(tp.ProviderError) as raised:
            provider.live(PARK,[ACTIVE])
        self.assertEqual(raised.exception.http_status,503)
        self.assertEqual(raised.exception.retry_after_seconds,600)

    def test_redirects_disabled(self):
        handler = tp.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'https://untrusted.example'))


class RegistryTests(unittest.TestCase):
    def test_missing_bad_duplicate_and_valid_local_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"registry.json"
            with self.assertRaises(tp.ProviderError):
                tp.load_registry(path)
            for data in ({}, {"provider": "wrong"}, {"provider": "ThemeParks.wiki", "attractions": []}):
                path.write_text(json.dumps(data))
                with self.assertRaises(tp.ProviderError):
                    tp.load_registry(path)
            row = dict(alias=ACTIVE.alias, provider_name=ACTIVE.provider_name, park_id=PARK, attraction_id=ENTITY, retired=False, mapping_status="verified")
            path.write_text(json.dumps({"provider": "ThemeParks.wiki", "attractions": [row]}))
            self.assertEqual(tp.load_registry(path), (ACTIVE,))
            path.write_text(json.dumps({"provider": "ThemeParks.wiki", "attractions": [row, row]}))
            with self.assertRaises(tp.ProviderError):
                tp.load_registry(path)

    def test_invalid_verified_mapping_and_uuid(self):
        for entity in (None, 123, "not-a-uuid"):
            with self.assertRaises(ValueError):
                tp.Attraction("Test", "Test", PARK, entity, False, "verified")


if __name__ == "__main__":
    unittest.main()
