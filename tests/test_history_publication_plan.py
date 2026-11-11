"""Fixture-only readiness plan tests; no real snapshot or cloud requests."""
from pathlib import Path
import unittest
from unittest.mock import patch
from src import history_snapshots as s
from src.history_publication_plan import plan_from_store


class PublicationPlanTests(unittest.TestCase):
    def store(self):
        data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())
        data['schema_version']=2;data['dataset_kind']='collected_observations'
        registry=s.public_registry()
        for row in data['records']:row['park_id']=registry[row['attraction_id']]['park_id']
        store=s.package(data,allow_real=True)
        digest=s.decode(store.get('manifest.json'))['sha256']
        return store,digest

    def test_plan_contains_only_metadata_and_no_upload_action(self):
        store,digest=self.store()
        with patch('socket.socket',side_effect=AssertionError('No network')),patch('sqlite3.connect',side_effect=AssertionError('No SQLite')):
            plan=plan_from_store(store,expected_sha256=digest)
        self.assertEqual(plan['snapshot_key'],'history/v2/'+digest+'.json')
        self.assertEqual(plan['manifest_key'],'manifest.json')
        self.assertFalse(plan['ready_for_upload'])
        self.assertNotIn('records',plan)
        self.assertIn('manifest.json last',plan['publication_order'][3])
        self.assertTrue(any('Restart only website workers' in line for line in plan['rollback']))
        self.assertNotIn('QUEUE_ATLAS_R2_PUBLISH_SECRET_ACCESS_KEY',plan['render_environment_names'])

    def test_wrong_approved_digest_is_rejected(self):
        store,_=self.store()
        with self.assertRaises(s.SnapshotError):plan_from_store(store,expected_sha256='0'*64)

    def test_fixture_kind_not_planned_as_real_data(self):
        with self.assertRaises(s.SnapshotError):plan_from_store(s.fixture_store())
