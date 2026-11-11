"""Offline only: public signing vectors, invented datasets and mocked HTTPS responses."""
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest
from unittest.mock import patch
from app import create_app
from src import history_snapshots as s
from src import history_r2 as r
from src.history_production import production_config
from src.history_production_fixture_demo import FixtureObjectStore, demo_app

NOW=datetime(2026,11,2,tzinfo=timezone.utc)
CONFIG=r.R2Config('a'*32,'fixture-private-bucket','fixture-read-key','fixture-read-secret')


def dataset():
    # Simulates a production-shaped dataset; every value still comes from invented test fixtures.
    data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())
    data['schema_version']=2;data['dataset_kind']='collected_observations'
    registry=s.public_registry()
    for row in data['records']:row['park_id']=registry[row['attraction_id']]['park_id']
    return data


class Store:
    def __init__(self,data=None):
        self.memory=s.package(data or dataset(),allow_real=True);self.calls=[];self.etag='"fixture-v1"';self.failure=None
    def fetch(self,key,*,etag=None):
        self.calls.append((key,etag))
        if self.failure:raise self.failure
        if key=='manifest.json' and etag==self.etag:return r.ObjectResult(304,b'',self.etag)
        return r.ObjectResult(200,self.memory.get(key),self.etag if key=='manifest.json' else None)
    def get(self,key):return self.fetch(key).body


class SigningTransportTests(unittest.TestCase):
    def test_official_aws_s3_signing_vector(self):
        # AWS public documentation example, NOT real credentials.
        key='AKIA'+'IOSFODNN7EXAMPLE'
        secret='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
        headers=r.signed_get('examplebucket.s3.amazonaws.com','/test.txt',key,secret,datetime(2013,5,24,tzinfo=timezone.utc),region='us-east-1',extra_headers={'range':'bytes=0-9'})
        self.assertTrue(headers['authorization'].endswith('Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'))

    def test_get_only_fixed_host_key_and_private_signing(self):
        calls=[]
        def transport(*args):calls.append(args);return r.ObjectResult(200,b'{}','"fixture"')
        store=r.R2ReadOnlyStore(CONFIG,transport=transport,wall_clock=lambda:NOW)
        self.assertEqual(store.get('manifest.json'),b'{}')
        host,path,headers,limit=calls[0]
        self.assertEqual(host,'a'*32+'.r2.cloudflarestorage.com')
        self.assertEqual(path,'/fixture-private-bucket/manifest.json')
        self.assertEqual(limit,r.MANIFEST_LIMIT)
        self.assertIn('/auto/s3/aws4_request',headers['authorization'])
        self.assertNotIn(CONFIG.secret_access_key,repr(CONFIG))
        for key in ('../secrets','https://other.test/file','history/v2/bad.json','aggregate_report.json','manifest.json?x=1'):
            with self.assertRaises(r.StorageUnavailable):store.get(key)
        self.assertEqual(len(calls),1)

    def test_conditionals_and_304_rules(self):
        calls=[]
        def transport(*args):calls.append(args);return r.ObjectResult(304,b'','"same"')
        store=r.R2ReadOnlyStore(CONFIG,transport=transport,wall_clock=lambda:NOW)
        self.assertEqual(store.fetch('manifest.json',etag='"same"').status,304)
        self.assertEqual(calls[0][2]['if-none-match'],'"same"')
        with self.assertRaises(r.StorageUnavailable):store.fetch('manifest.json')
        with self.assertRaises(r.StorageUnavailable):store.fetch('manifest.json',etag='bad\r\nInjected: true')

    def test_failures_statuses_and_redaction(self):
        for status in (301,302,307,401,403,404,429,500,503):
            store=r.R2ReadOnlyStore(CONFIG,transport=lambda *_,status=status:r.ObjectResult(status,b'PRIVATE_BODY',retry_after=90))
            with self.assertRaises(r.StorageUnavailable) as raised:store.get('manifest.json')
            self.assertNotIn('PRIVATE_BODY',str(raised.exception))
            self.assertEqual(raised.exception.retry_after,90)
        store=r.R2ReadOnlyStore(CONFIG,transport=lambda *_:(_ for _ in ()).throw(TimeoutError('SECRET PATH')))
        with self.assertRaises(r.StorageUnavailable) as raised:store.get('manifest.json')
        self.assertNotIn('SECRET',str(raised.exception))

    def test_oversized_transport_bodies_rejected(self):
        store=r.R2ReadOnlyStore(CONFIG,transport=lambda *_:r.ObjectResult(200,b'x'*(r.MANIFEST_LIMIT+1)))
        with self.assertRaises(r.StorageUnavailable):store.get('manifest.json')

    def test_http_stream_bounds_redirects_and_connection_cleanup(self):
        class Response(BytesIO):
            status=200
            def getheader(self,key,default=None):return default
        response=Response(b'123456')
        class Connection:
            sock=None
            closed=False
            calls=[]
            def __init__(self,*args,**kwargs):pass
            def request(self,*args,**kwargs):self.calls.append(args)
            def getresponse(self):return response
            def close(self):self.closed=True
        connection=Connection()
        store=r.R2ReadOnlyStore(CONFIG)
        with patch.object(r.http.client,'HTTPSConnection',return_value=connection):
            with self.assertRaises(r.StorageUnavailable):store._https_get('fixed.test','/manifest.json',{},5)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.calls[0][0],'GET')
        response.status=302;connection.closed=False
        with patch.object(r.http.client,'HTTPSConnection',return_value=connection):
            result=store._https_get('fixed.test','/manifest.json',{},5)
        self.assertEqual(result.status,302)
        self.assertEqual(result.body,b'')
        self.assertTrue(connection.closed)

    def test_retry_after_seconds_dates_and_bad_values(self):
        self.assertEqual(r.retry_delay('120',NOW),120)
        self.assertEqual(r.retry_delay('Mon, 02 Nov 2026 00:02:00 GMT',NOW),120)
        self.assertEqual(r.retry_delay('not valid',NOW),30)

    def test_environment_disabled_does_not_read_credentials_or_make_requests(self):
        class Disabled(dict):
            def __getitem__(self,key):raise AssertionError('Credentials must not be accessed')
        with patch.object(r.http.client,'HTTPSConnection',side_effect=AssertionError('Network forbidden')):
            self.assertEqual(production_config(Disabled()),{'PUBLIC_DEPLOYMENT':True})
        with self.assertRaises(ValueError):production_config({'QUEUE_ATLAS_HISTORY_ENABLED':'yes'})
        with self.assertRaises(ValueError):production_config({'QUEUE_ATLAS_HISTORY_ENABLED':'1'})

    def test_config_validation_and_lazy_activation(self):
        for account,bucket in (('http://127.0.0.1','fixture-bucket'),('a'*32,'../bucket'),('a'*32,'bucket.example.com')):
            with self.assertRaises(ValueError):r.R2Config(account,bucket,'fixture-key','fixture-secret')
        env={'QUEUE_ATLAS_HISTORY_ENABLED':'1','QUEUE_ATLAS_R2_ACCOUNT_ID':CONFIG.account_id,'QUEUE_ATLAS_R2_BUCKET':CONFIG.bucket,'QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID':CONFIG.access_key_id,'QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY':CONFIG.secret_access_key}
        with patch.object(r.http.client,'HTTPSConnection',side_effect=AssertionError('No startup network')):
            config=production_config(env)
        self.assertTrue(config['HISTORY_PRODUCTION_ENABLED'])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();self.now=0
        self.cache=r.ValidatedSnapshotCache(self.store,ttl=30,clock=lambda:self.now,wall_clock=lambda:NOW)
        self.registry=s.public_registry()

    def test_initial_validation_hot_cache_copy_and_conditional_refresh(self):
        data=self.cache.snapshot(self.registry)
        self.assertEqual(len(self.store.calls),2)
        data['records'].clear()
        self.assertTrue(self.cache.snapshot(self.registry)['records'])
        self.assertEqual(len(self.store.calls),2)
        self.now=31
        self.assertTrue(self.cache.snapshot(self.registry)['records'])
        self.assertEqual(self.store.calls[-1],('manifest.json','"fixture-v1"'))
        self.assertEqual(len(self.store.calls),3)

    def test_new_manifest_atomically_replaces_validated_object(self):
        self.cache.snapshot(self.registry);new=dataset();new['generated_at']='2026-11-01T08:00:00Z';new['records'][0]['wait_minutes']=2
        self.store.memory=s.package(new,allow_real=True);self.store.etag='"fixture-v2"';self.now=31
        self.assertEqual(self.cache.snapshot(self.registry)['records'][0]['wait_minutes'],2)
        self.assertEqual(len(self.store.calls),4)

    def test_unchanged_content_key_reuses_immutable_bytes(self):
        self.cache.snapshot(self.registry);self.store.etag='"same-content-new-etag"';self.now=31
        self.cache.snapshot(self.registry)
        self.assertEqual(len(self.store.calls),3)

    def test_outage_fails_closed_and_honors_cooldown_then_recovers(self):
        self.cache.snapshot(self.registry);self.now=31;self.store.failure=r.StorageUnavailable(120)
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(self.registry)
        calls=len(self.store.calls);self.now=100
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(self.registry)
        self.assertEqual(len(self.store.calls),calls)
        self.now=152;self.store.failure=None
        self.assertTrue(self.cache.snapshot(self.registry)['records'])

    def test_bad_checksum_never_replaces_cache_or_becomes_public(self):
        self.cache.snapshot(self.registry);new=dataset();new['generated_at']='2026-11-01T08:00:00Z'
        store=s.package(new,allow_real=True);manifest=store.get('manifest.json');key=s.decode(manifest)['key']
        self.store.memory=s.MemorySnapshotStore({'manifest.json':manifest,key:b'CORRUPT'});self.store.etag='"bad"';self.now=31
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(self.registry)
        self.assertEqual(s.decode(self.cache._raw)['generated_at'],'2026-11-01T07:00:00Z')

    def test_malformed_manifest_is_rejected_before_object_fetch(self):
        self.store.memory=s.MemorySnapshotStore({'manifest.json':b'{"key":"../../secret"}'})
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(self.registry)
        self.assertEqual(len(self.store.calls),1)

    def test_manifest_limit_and_initial_unsolicited_304(self):
        class Bad:
            def fetch(self,*_,**__):return r.ObjectResult(304,b'')
        with self.assertRaises(r.StorageUnavailable):r.ValidatedSnapshotCache(Bad()).snapshot(self.registry)
        self.store.memory=s.MemorySnapshotStore({'manifest.json':b' '*(r.MANIFEST_LIMIT+1)})
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(self.registry)

    def test_generation_rollback_and_future_are_rejected(self):
        self.cache.snapshot(self.registry);old=dataset();old['generated_at']='2026-11-01T06:59:59Z'
        self.store.memory=s.package(old,allow_real=True);self.store.etag='"older"';self.now=31
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(self.registry)
        future=dataset();future['generated_at']='2026-12-01T00:00:00Z'
        with self.assertRaises(r.StorageUnavailable):r.ValidatedSnapshotCache(Store(future),wall_clock=lambda:NOW).snapshot(self.registry)

    def test_production_rejects_fixture_kind_unless_explicit_test_seam(self):
        with self.assertRaises(r.StorageUnavailable):r.ValidatedSnapshotCache(FixtureObjectStore()).snapshot(self.registry)
        self.assertEqual(r.ValidatedSnapshotCache(FixtureObjectStore(),allow_fixtures=True).snapshot(self.registry)['dataset_kind'],'synthetic_test_fixture')

    def test_concurrent_cold_requests_fetch_only_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            values=list(pool.map(lambda _:self.cache.snapshot(self.registry),range(4)))
        self.assertEqual(len(values),4);self.assertEqual(len(self.store.calls),2)

    def test_registry_changes_revalidate_even_when_cache_hot(self):
        self.cache.snapshot(self.registry);registry=deepcopy(self.registry);registry.pop(next(iter(registry)))
        with self.assertRaises(r.StorageUnavailable):self.cache.snapshot(registry)


class ProductionHistoryWebTests(unittest.TestCase):
    def make(self,store=None):
        self.store=store or Store()
        cache=r.ValidatedSnapshotCache(self.store,wall_clock=lambda:NOW)
        with patch('app.load_new_model',return_value=object()):
            return create_app({'PUBLIC_DEPLOYMENT':True,'HISTORY_PRODUCTION_ENABLED':True,'HISTORY_SNAPSHOT_PROVIDER':cache})

    def test_production_fixture_shape_renders_21_charts_without_database_or_network(self):
        with patch('sqlite3.connect',side_effect=AssertionError('No database')),patch('socket.socket',side_effect=AssertionError('No network')):
            client=self.make().test_client()
            home=client.get('/').get_data(as_text=True)
            self.assertIn('ARCHIVED HISTORICAL POSTED WAITS',home)
            self.assertEqual(home.count('<option value="6">'),1)
            for ident in s.public_registry():
                for hours in ('6','24','168'):
                    response=client.get('/history/chart',query_string={'attraction_id':ident,'hours':hours})
                    html=response.get_data(as_text=True)
                    self.assertEqual(response.status_code,200)
                    self.assertIn('Archived historical window',html)
                    self.assertIn('Coverage begins',html)
                    self.assertIn('Snapshot generated',html)
                    self.assertIn('Incomplete history',html)
                    self.assertIn('Powered by ThemeParks.wiki',html)
                    self.assertNotIn('PRIVATE ARCHIVED',html)
                    self.assertNotIn('"records"',html)
            self.assertEqual(client.get('/api/history?attraction_id=anything').status_code,503)
            self.assertEqual(client.get('/api/current-waits').status_code,503)

    def test_storage_outage_sanitized_and_other_features_available(self):
        store=Store();store.failure=RuntimeError('SECRET_TOKEN /private/path r2.cloudflarestorage.com')
        client=self.make(store).test_client()
        response=client.get('/history/chart',query_string={'attraction_id':next(iter(s.public_registry()))})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.headers['Retry-After'],'30')
        for forbidden in ('SECRET','private/path','cloudflarestorage'):self.assertNotIn(forbidden,response.get_data(as_text=True))
        self.assertEqual(client.get('/healthz').status_code,200)
        self.assertEqual(client.get('/api/map-attractions').status_code,200)
        self.assertEqual(client.get('/').status_code,200)
        with patch('app.predict_wait_time',return_value=25):self.assertEqual(client.post('/api/predict',json={'ride':'Hulk Coaster','day':'Sat','hour':12}).status_code,200)

    def test_invalid_requests_rate_limit_and_privacy_headers(self):
        client=self.make().test_client();ident=next(iter(s.public_registry()))
        for query in ('attraction_id=bad','attraction_id='+ident+'&hours=8','attraction_id='+ident+'&format=json','attraction_id='+ident+'&hours=6&hours=24'):
            self.assertEqual(client.get('/history/chart?'+query).status_code,400)
        self.assertFalse(self.store.calls)
        response=client.get('/history/chart?attraction_id='+ident)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        for path in ('/private/manifest.json','/static/manifest.json','/history/manifest.json'):self.assertEqual(client.get(path).status_code,404)
        with patch('app.time.monotonic',return_value=100):
            limited=self.make().test_client()
            for _ in range(30):self.assertEqual(limited.get('/history/chart?attraction_id=bad').status_code,400)
            self.assertEqual(limited.get('/history/chart?attraction_id='+ident).status_code,429)

    def test_offline_demo_has_only_explicit_fixture_labels(self):
        with patch('socket.socket',side_effect=AssertionError('No network')),patch('sqlite3.connect',side_effect=AssertionError('No database')),patch('app.load_new_model',return_value=object()):
            client=demo_app().test_client()
            self.assertIn('SYNTHETIC TEST FIXTURES ONLY',client.get('/').get_data(as_text=True))
            self.assertIn('SYNTHETIC TEST FIXTURES',client.get('/history/chart?attraction_id='+next(iter(s.public_registry()))).get_data(as_text=True))

    def test_activation_incompatible_with_local_store_or_private_mode(self):
        for config in ({'PUBLIC_DEPLOYMENT':False},{'PUBLIC_DEPLOYMENT':True,'HISTORY_PRIVATE_LOCAL':True},{'PUBLIC_DEPLOYMENT':True,'HISTORY_SNAPSHOT_STORE':s.fixture_store()}):
            with self.assertRaises(ValueError):create_app({**config,'HISTORY_PRODUCTION_ENABLED':True,'HISTORY_SNAPSHOT_PROVIDER':r.ValidatedSnapshotCache(Store())})
        with self.assertRaises(ValueError):create_app({'HISTORY_SNAPSHOT_PROVIDER':r.ValidatedSnapshotCache(Store())})


class LaunchSafetyTests(unittest.TestCase):
    def test_production_rejects_publisher_environment(self):
        for name in ('QUEUE_ATLAS_R2_PUBLISH_ACCESS_KEY_ID', 'QUEUE_ATLAS_R2_PUBLISH_SECRET_ACCESS_KEY'):
            with self.assertRaisesRegex(ValueError, 'publisher credentials'):
                production_config({'QUEUE_ATLAS_HISTORY_ENABLED':'1',name:'never-disclose'})

    def test_public_archive_only_without_changing_fixture_controls(self):
        client=ProductionHistoryWebTests().make().test_client()
        home=client.get('/').get_data(as_text=True)
        self.assertIn('id="history-anchor" value="archive"',home)
        self.assertNotIn('value="rolling"',home)
        for ident in s.public_registry():self.assertIn(ident,home)
        self.assertEqual(client.get('/history/chart',query_string={'attraction_id':next(iter(s.public_registry())),'mode':'rolling'}).status_code,400)
        with patch('app.load_new_model',return_value=object()):
            self.assertIn('value="rolling"',demo_app().test_client().get('/').get_data(as_text=True))
