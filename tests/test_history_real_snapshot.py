"""Real-kind mechanics tested only with invented temporary fixture databases."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from app import create_app
from src import history_extraction as e, history_snapshots as s, history_real_snapshot as real


class RealSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='queue-atlas-real-entry-fixtures-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.scope=e.ExtractionScope(tuple(sorted(e.APPROVED)),real.START,real.END)
        with e.FixtureDatabase() as source:
            # Intentionally fake observations, in the approved time range.
            for ident in e.APPROVED:
                source.add(ident,'2026-11-08T19:43:55.123456+00:00',wait=0)
                source.add(ident,'2026-11-08T19:48:55.123456Z',wait=20)
                source.add(ident,'2026-11-08T20:08:55.123456Z',status='CLOSED',wait=None,flags=('missing_wait',))
            source.writer.close() # checkpoint only our invented temporary database
            self.database=self.root/'private/themeparks/pilot/pilot.sqlite3'
            self.database.parent.mkdir(parents=True)
            shutil.copyfile(source.path,self.database)
        self.destination=self.root/'private/themeparks/snapshots'/real.BUNDLE_NAME

    def generate(self):
        with patch.object(real,'require_ignored'),patch.object(real,'collector_is_running',return_value=False),patch.object(real,'restrict_directory'):
            return real.generate(self.scope,root=self.root)

    def test_closed_wal_database_immutable_read_preserves_all_source_files(self):
        before=real.fingerprint(self.database)
        path,report=self.generate()
        self.assertEqual(before,real.fingerprint(self.database))
        self.assertFalse(Path(str(self.database)+'-wal').exists())
        self.assertFalse(Path(str(self.database)+'-shm').exists())
        self.assertEqual(report['counts']['records'],21)
        self.assertEqual(report['counts']['numeric'],14)
        self.assertEqual(report['counts']['genuine_zeroes'],7)
        self.assertEqual(report['counts']['closed'],7)
        self.assertEqual(report['counts']['gaps_over_450_seconds'],7)
        data=s.load(real.PrivateLocalStore(path),allow_real=True)
        self.assertEqual(data['dataset_kind'],'collected_observations')
        self.assertEqual(data['records'][0]['observed_at'],'2026-11-08T19:43:55.123456+00:00')
        raw=s.canonical(data)
        self.assertNotIn(b'FIXTURE_SECRET',raw)
        self.assertNotIn(b'raw_body',raw)
        with self.assertRaises(s.SnapshotError):s.load(real.PrivateLocalStore(path))

    def test_exact_scope_only_and_existing_output_refused_before_read(self):
        wrong=e.ExtractionScope(tuple(sorted(e.APPROVED)),'2026-11-08T19:43:54Z',real.END)
        with patch.object(e,'_read_rows') as read:
            with self.assertRaises(e.ExtractionError):real.generate(wrong,root=self.root)
            read.assert_not_called()
        path,_=self.generate()
        before={str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}
        with patch.object(e,'_read_rows') as read:
            with self.assertRaises(FileExistsError):self.generate()
            read.assert_not_called()
        self.assertEqual(before,{str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()})

    def test_active_collector_or_sidecar_blocks_without_read(self):
        with patch.object(real,'require_ignored'),patch.object(real,'collector_is_running',return_value=True),patch.object(e,'_read_rows') as read:
            with self.assertRaises(e.ExtractionError):real.generate(self.scope,root=self.root)
            read.assert_not_called()
        Path(str(self.database)+'-wal').write_bytes(b'fixture sidecar')
        with patch.object(real,'require_ignored'),patch.object(e,'_read_rows') as read:
            with self.assertRaises(e.ExtractionError):real.generate(self.scope,root=self.root)
            read.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_source_change_blocks_output(self):
        before=real.fingerprint(self.database);after={**before,'database':'changed'}
        with patch.object(real,'require_ignored'),patch.object(real,'collector_is_running',return_value=False),patch.object(real,'fingerprint',side_effect=[before,after]):
            with self.assertRaises(e.ExtractionError):real.generate(self.scope,root=self.root)
        self.assertFalse(self.destination.exists())

    def test_git_ignore_is_required_before_read(self):
        with patch.object(real,'require_ignored',side_effect=e.ExtractionError('not private')),patch.object(e,'_read_rows') as read:
            with self.assertRaises(e.ExtractionError):real.generate(self.scope,root=self.root)
            read.assert_not_called()

    def test_private_store_refuses_arbitrary_keys_and_corruption(self):
        path,_=self.generate();store=real.PrivateLocalStore(path)
        for key in ('../pilot/pilot.sqlite3','aggregate_report.json','history/v1/not-a-hash.json'):
            with self.assertRaises(s.SnapshotError):store.get(key)
        manifest=s.decode(store.get('manifest.json'))
        (path/manifest['key']).write_bytes(b'corrupted fixture')
        with self.assertRaises(s.SnapshotError):s.load(store,allow_real=True)

    def test_private_local_charts_real_labels_all_windows_no_sqlite(self):
        path,_=self.generate();store=real.PrivateLocalStore(path)
        with patch('sqlite3.connect',side_effect=AssertionError('No database access during private chart preview')),patch('app.load_new_model',return_value=object()):
            client=create_app({'HISTORY_PRIVATE_LOCAL':True,'HISTORY_SNAPSHOT_STORE':store}).test_client()
            home=client.get('/').get_data(as_text=True)
            self.assertIn('PRIVATE LOCAL HISTORICAL SNAPSHOT',home)
            self.assertNotIn('SYNTHETIC TEST FIXTURES ONLY',home)
            for hours in ('6','24','168'):
                response=client.get('/history/chart',query_string={'attraction_id':sorted(e.APPROVED)[0],'hours':hours,'mode':'archive'})
                self.assertEqual(response.status_code,200)
                html=response.get_data(as_text=True)
                self.assertIn('Archived historical window',html)
                self.assertIn('Incomplete history',html)
                self.assertIn('PRIVATE ARCHIVED POSTED WAITS',html)
                self.assertNotIn('synthetic fixture',html)
                self.assertNotIn('provider_last_changed_at',html)
                self.assertIn('<svg',html)
            self.assertEqual(client.get('/api/history?attraction_id=anything').status_code,503)
            self.assertEqual(client.get('/',environ_overrides={'REMOTE_ADDR':'203.0.113.1'}).status_code,403)
            self.assertEqual(client.get('/',base_url='http://untrusted.example').status_code,403)

    def test_public_app_cannot_enable_private_mode_or_serve_real_store(self):
        path,_=self.generate();store=real.PrivateLocalStore(path)
        with self.assertRaises(ValueError):create_app({'PUBLIC_DEPLOYMENT':True,'HISTORY_PRIVATE_LOCAL':True,'HISTORY_SNAPSHOT_STORE':store})
        with patch('app.load_new_model',return_value=object()):
            client=create_app({'PUBLIC_DEPLOYMENT':True,'HISTORY_SNAPSHOT_STORE':store}).test_client()
        response=client.get('/history/chart',query_string={'attraction_id':sorted(e.APPROVED)[0]})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('records',response.get_data(as_text=True))

    def test_real_kind_must_be_v2_and_default_fixture_writer_rejects(self):
        path,_=self.generate();data=s.load(real.PrivateLocalStore(path),allow_real=True)
        with self.assertRaises(s.SnapshotError):s.validate(data)
        with self.assertRaises(s.SnapshotError):e.write_fixture_bundle(data,self.root/'invalid-bundle')
        data['schema_version']=1
        with self.assertRaises(s.SnapshotError):s.validate(data,allow_real=True)


class HistoryWindowEdgeTests(unittest.TestCase):
    def test_missing_leading_interval_is_incomplete_even_inside_global_coverage(self):
        data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())
        ident=data['records'][0]['attraction_id']
        data['records']=[r for r in data['records'] if r['attraction_id']==ident and r['observed_at']>='2026-11-01T06:40:00Z']
        data['coverage_start']='2026-10-31T00:00:00Z'
        s.validate(data)
        view=s.window({'attraction_id':ident,'hours':'6'},data,now=datetime(2026,11,2,tzinfo=timezone.utc))
        self.assertFalse(any(p['gap'] for p in view['points']))
        self.assertTrue(view['incomplete'])


class PrivateACLTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows ACL verification')
    def test_private_directory_acl_applied_and_verified_on_owned_temporary_path(self):
        with tempfile.TemporaryDirectory(prefix='queue-atlas-private-acl-test-') as tmp:
            path=Path(tmp)/'private-fixture';path.mkdir()
            real.restrict_directory(path)
            (path/'fixture.txt').write_text('invented test data')
            self.assertEqual((path/'fixture.txt').read_text(),'invented test data')
