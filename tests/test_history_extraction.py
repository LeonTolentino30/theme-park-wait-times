"""All SQLite data here is invented inside owned temporary fixture databases."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from src import history_snapshots as s
from src import history_extraction as e
from src.history_chart import display


class TimestampCompatibilityTests(unittest.TestCase):
    def legacy(self):
        return s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())

    def test_fractional_seconds_and_utc_forms_preserved_exactly(self):
        for value in ('2026-11-01T05:50:00Z','2026-11-01T05:50:00+00:00','2026-11-01T05:50:00.1Z','2026-11-01T05:50:00.100000+00:00','2026-11-01T05:50:00.123456Z'):
            data=self.legacy();data['records'][0]['observed_at']=value
            data['records'][0]['provider_last_changed_at']=value
            loaded=s.load(s.package(data))
            self.assertEqual(loaded['records'][0]['observed_at'],value)
            self.assertEqual(loaded['records'][0]['provider_last_changed_at'],value)

    def test_submicrosecond_and_non_utc_values_rejected_not_rounded(self):
        for value in ('2026-11-08T00:00:00.1234567Z','2026-11-08T00:00:00.123456789Z','2026-11-08T00:00:00.123456','2026-11-08T00:00:00-05:00','2026-11-08T00:00:00-00:00','2026-11-08T00:00:60Z','2026-11-08T00:00:00.Z'):
            with self.subTest(value=value),self.assertRaises(s.SnapshotError):s.utc(value)

    def test_whole_second_legacy_integrity_unchanged(self):
        data=self.legacy();store=s.package(data)
        manifest=s.decode(store.get('manifest.json'))
        self.assertEqual(manifest['schema_version'],1)
        self.assertEqual(manifest['key'],'history/v1/'+hashlib.sha256(s.canonical(data)).hexdigest()+'.json')
        self.assertEqual(s.load(store),data)

    def test_equivalent_instants_duplicate_despite_different_strings(self):
        data=self.legacy();row=deepcopy(data['records'][0]);row['observed_at']='2026-11-01T05:50:00.000000+00:00';data['records'].append(row)
        with self.assertRaises(s.SnapshotError):s.validate(data)

    def test_subsecond_sort_is_chronological_not_lexical(self):
        data=self.legacy();ident=data['records'][0]['attraction_id']
        row=deepcopy(data['records'][0]);row['observed_at']='2026-11-01T05:50:00.000001Z';data['records'].insert(0,row)
        data['coverage_start']='2026-11-01T05:50:00+00:00'
        view=s.window({'attraction_id':ident},s.load(s.package(data)),now=datetime(2026,11,2,tzinfo=timezone.utc))
        self.assertEqual(view['points'][0]['when'].microsecond,0)
        self.assertEqual(view['points'][1]['when'].microsecond,1)

    def test_dst_transitions_preserve_order_and_fraction(self):
        before=s.utc('2026-11-01T05:59:59.999999+00:00')
        after=s.utc('2026-11-01T06:00:00.000001Z')
        self.assertEqual((after-before).microseconds,2)
        self.assertIn('EDT',display(before));self.assertIn('EST',display(after))
        before=s.utc('2026-03-08T06:59:59.999999Z');after=s.utc('2026-03-08T07:00:00.000001+00:00')
        self.assertEqual((after-before).microseconds,2)
        self.assertIn('EST',display(before));self.assertIn('EDT',display(after))

    def test_manifest_and_data_versions_must_agree(self):
        data=self.legacy();raw=s.canonical(data);digest=hashlib.sha256(raw).hexdigest();key='history/v2/'+digest+'.json'
        manifest=s.canonical({'schema_version':2,'dataset_kind':'synthetic_test_fixture','sha256':digest,'bytes':len(raw),'key':key})
        with self.assertRaises(s.SnapshotError):s.load(s.MemorySnapshotStore({'manifest.json':manifest,key:raw}))


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.source=e.FixtureDatabase()
        self.addCleanup(self.source.close)
        self.ids=tuple(sorted(e.APPROVED));self.ident=self.ids[0]
        self.scope=e.ExtractionScope(self.ids,'2026-11-08T00:00:00Z','2026-11-15T00:00:00Z')

    def extract(self,scope=None):
        return e.extract_fixture(self.source,scope or self.scope,generated_at='2026-11-16T00:00:00+00:00')

    def test_exact_approved_fields_and_no_private_metadata(self):
        self.source.add(self.ident,'2026-11-08T12:00:00.123456+00:00',changed='2026-11-08T11:59:59.999999Z')
        data=self.extract();row=data['records'][0]
        self.assertEqual(data['schema_version'],2)
        self.assertEqual(set(row),{'attraction_id','park_id','observed_at','provider_last_changed_at','status','wait_minutes','present','quality_flags'})
        self.assertEqual(row['observed_at'],'2026-11-08T12:00:00.123456+00:00')
        self.assertEqual(row['provider_last_changed_at'],'2026-11-08T11:59:59.999999Z')
        self.assertEqual(row['park_id'],s.public_registry()[self.ident]['park_id'])
        text=s.canonical(data).decode()
        for forbidden in ('FIXTURE_SECRET','FIXTURE_PRIVATE_RAW_RESPONSE','FIXTURE_LOCAL_PATH','raw_body','headers','error','sqlite3'):
            self.assertNotIn(forbidden,text)

    def test_no_write_or_sidecar_creation_and_reader_closes(self):
        self.source.add(self.ident,'2026-11-08T12:00:00Z')
        paths=[self.source.path,Path(str(self.source.path)+'-wal'),Path(str(self.source.path)+'-shm')]
        before={p.name:(p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths[:2]}
        self.extract()
        after={p.name:(p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths[:2]}
        self.assertEqual(before,after)
        self.assertTrue(all(p.exists() for p in paths))
        # A writer can immediately proceed; reader did not retain its transaction.
        self.source.writer.execute('BEGIN IMMEDIATE');self.source.writer.rollback()

    def test_all_seven_explicit_allowlist_required(self):
        for ids in ((),self.ids[:-1],self.ids+(self.ids[0],),tuple(['bad']*7),list(self.ids)):
            with self.assertRaises(e.ExtractionError):e.ExtractionScope(ids,self.scope.start,self.scope.end)

    def test_date_envelope_and_seven_day_limit(self):
        for start,end in (('2026-11-07T23:59:59Z','2026-11-09T00:00:00Z'),('2026-11-15T00:00:00Z','2026-11-16T00:00:00.000001Z'),('2026-11-08T00:00:00Z','2026-11-16T00:00:00Z'),('2026-11-08T00:00:00Z','2026-11-08T00:00:00Z'),('2026-11-09T00:00:00Z','2026-11-08T00:00:00Z'),('2026-11-08T00:00:00-05:00','2026-11-09T00:00:00Z')):
            with self.subTest(start=start,end=end),self.assertRaises(e.ExtractionError):e.ExtractionScope(self.ids,start,end)
        e.ExtractionScope(self.ids,'2026-11-15T23:59:59.999998Z','2026-11-16T00:00:00Z')

    def test_microsecond_precise_inclusive_start_exclusive_end(self):
        for text in ('2026-11-08T12:00:00.123455Z','2026-11-08T12:00:00.123456+00:00','2026-11-08T12:00:00.123457Z','2026-11-08T12:00:00.123458Z'):
            self.source.add(self.ident,text)
        scope=e.ExtractionScope(self.ids,'2026-11-08T12:00:00.123456Z','2026-11-08T12:00:00.123458+00:00')
        data=self.extract(scope)
        self.assertEqual([r['observed_at'] for r in data['records']],['2026-11-08T12:00:00.123456+00:00','2026-11-08T12:00:00.123457Z'])
        self.assertEqual(e.micros(data['records'][1]['observed_at'])-e.micros(data['records'][0]['observed_at']),1)

    def test_unsuccessful_other_source_and_out_of_scope_rows_excluded(self):
        self.source.add(self.ident,'2026-11-08T12:00:00Z')
        self.source.add(self.ident,'2026-11-08T12:05:00Z',outcome='error')
        self.source.add(self.ident,'2026-11-07T23:59:59.999999Z')
        self.source.add(self.ident,'2026-11-15T00:00:00Z')
        self.assertEqual(len(self.extract()['records']),1)

    def test_unrelated_attraction_excluded(self):
        other='00000000-0000-4000-8000-000000000001';park=s.public_registry()[self.ident]['park_id']
        with self.source.writer:self.source.writer.execute('INSERT INTO attractions VALUES(?,?,?)',(other,park,'Unrelated fixture'))
        self.source.add(other,'2026-11-08T12:00:00Z',park_id=park)
        self.assertFalse(self.extract()['records'])

    def test_duplicate_equivalent_instants_rejected(self):
        self.source.add(self.ident,'2026-11-08T12:00:00.1Z')
        self.source.add(self.ident,'2026-11-08T12:00:00.100000+00:00')
        with self.assertRaises(s.SnapshotError):self.extract()

    def test_invalid_values_rejected_not_coerced(self):
        self.source.add(self.ident,'2026-11-08T12:00:00Z',present=2)
        with self.assertRaises(e.ExtractionError):self.extract()

    def test_invalid_timestamps_rejected(self):
        self.source.add(self.ident,'2026-11-08T12:00:00.1234567Z')
        with self.assertRaises(e.ExtractionError):self.extract()

    def test_wrong_park_rejected(self):
        park=next(p for p in {r['park_id'] for r in s.public_registry().values()} if p!=s.public_registry()[self.ident]['park_id'])
        self.source.add(self.ident,'2026-11-08T12:00:00Z',park_id=park)
        with self.assertRaises(s.SnapshotError):self.extract()

    def test_quality_flags_compatible_and_preserved(self):
        flags=('missing_wait','missing_provider_timestamp')
        self.source.add(self.ident,'2026-11-08T12:00:00Z',status='CLOSED',wait=None,flags=flags)
        self.assertEqual(self.extract()['records'][0]['quality_flags'],list(flags))
        self.source.add(self.ident,'2026-11-08T12:05:00Z',status='DOWN',wait=0,flags=('non_operating_wait',))
        view=s.window({'attraction_id':self.ident},self.extract(),now=datetime(2026,11,17,tzinfo=timezone.utc))
        self.assertTrue(all(p['wait'] is None for p in view['points']))

    def test_dry_run_summary_retains_zero_closures_down_missing_and_gaps(self):
        e.seed_demo(self.source);data=self.extract();report=e.quality_summary(data)
        self.assertEqual(report['counts']['records'],49)
        self.assertEqual(report['counts']['numeric'],21)
        self.assertEqual(report['counts']['genuine_zeroes'],7)
        self.assertEqual(report['counts']['closed'],7)
        self.assertEqual(report['counts']['down'],7)
        self.assertEqual(report['counts']['missing_waits'],28)
        self.assertEqual(report['counts']['gaps_over_450_seconds'],7)
        self.assertNotIn('observed_at',json.dumps(report))
        self.assertNotIn('provider_last_changed_at',json.dumps(report))
        self.assertNotIn('wait_minutes',json.dumps(report))
        self.assertEqual(len(data['records']),49) # gaps never become extra rows

    def test_empty_scope_has_valid_insufficient_chart_snapshot(self):
        data=self.extract();self.assertFalse(data['records'])
        self.assertEqual(e.quality_summary(data)['counts']['records'],0)
        self.assertFalse(s.window({'attraction_id':self.ident},data,now=datetime(2026,11,17,tzinfo=timezone.utc))['meaningful'])

    def test_real_path_and_non_owned_source_blocked_before_connect(self):
        with patch('src.history_extraction.sqlite3.connect') as connect:
            with self.assertRaises(e.ExtractionError):e.extract_fixture(Path('private/themeparks/pilot/pilot.sqlite3'),self.scope)
            original=self.source._path
            try:
                self.source._path=Path('private/themeparks/pilot/pilot.sqlite3')
                with self.assertRaises(e.ExtractionError):self.extract()
            finally:self.source._path=original
            connect.assert_not_called()

    def test_symlink_and_missing_wal_blocked_before_connect(self):
        with patch.object(Path,'is_symlink',return_value=True),patch('src.history_extraction.sqlite3.connect') as connect:
            with self.assertRaises(e.ExtractionError):self.extract()
            connect.assert_not_called()
        with patch.object(Path,'is_file',return_value=False),patch('src.history_extraction.sqlite3.connect') as connect:
            with self.assertRaises(e.ExtractionError):self.extract()
            connect.assert_not_called()

    def test_v2_roundtrip_and_write_once_bundle(self):
        self.source.add(self.ident,'2026-11-08T12:00:00.123456Z')
        data=self.extract();store=s.package(data);manifest=s.decode(store.get('manifest.json'))
        self.assertEqual(manifest['schema_version'],2)
        self.assertTrue(manifest['key'].startswith('history/v2/'))
        self.assertEqual(s.load(store),data)
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'bundle';e.write_fixture_bundle(data,dest)
            before={str(p.relative_to(dest)):p.read_bytes() for p in dest.rglob('*') if p.is_file()}
            with self.assertRaises(FileExistsError):e.write_fixture_bundle(data,dest)
            self.assertEqual(before,{str(p.relative_to(dest)):p.read_bytes() for p in dest.rglob('*') if p.is_file()})

    def test_corrupted_fixture_still_fails_integrity(self):
        self.source.add(self.ident,'2026-11-08T12:00:00.123456Z')
        store=s.package(self.extract());m=s.decode(store.get('manifest.json'))
        with self.assertRaises(s.SnapshotError):s.load(s.MemorySnapshotStore({'manifest.json':store.get('manifest.json'),m['key']:store.get(m['key'])+b' '}))
