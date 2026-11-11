"""Invented fixtures and memory stores only. No SQLite/provider/cloud access."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from app import create_app
from src import history_snapshots as s
from src.history_chart import svg_chart, display

NOW=datetime(2026,11,1,8,tzinfo=timezone.utc)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())
        self.ident=self.data['records'][0]['attraction_id']

    def view(self, **params):
        return s.window({'attraction_id':self.ident,**params},self.data,now=NOW)

    def test_roundtrip_integrity_and_immutable_name(self):
        store=s.package(self.data)
        self.assertEqual(s.load(store),self.data)
        first=s.decode(store.get('manifest.json'))
        self.assertEqual(first['key'],'history/v1/'+hashlib.sha256(s.canonical(self.data)).hexdigest()+'.json')
        changed=deepcopy(self.data);changed['records'][0]['wait_minutes']=1
        self.assertNotEqual(s.decode(s.package(changed).get('manifest.json'))['key'],first['key'])
        self.assertEqual(s.decode(s.package(deepcopy(self.data)).get('manifest.json')),first)
        with self.assertRaises(TypeError):store._objects[first['key']]=b'changed'

    def test_checksum_tampering_rejected(self):
        store=s.package(self.data);manifest=store.get('manifest.json');key=s.decode(manifest)['key']
        with self.assertRaises(s.SnapshotError):s.load(s.MemorySnapshotStore({'manifest.json':manifest,key:store.get(key).replace(b'OPERATING',b'OPERATINX',1)}))

    def test_manifest_rejects_paths_version_size_and_hash(self):
        original=s.decode(s.package(self.data).get('manifest.json'))
        for field,value in [('key','../../private/pilot.sqlite3'),('schema_version',2),('schema_version',True),('sha256','x'*64),('bytes',True),('bytes',s.MAX_BYTES+1),('dataset_kind','real')]:
            manifest={**original,field:value}
            with self.subTest(field=field,value=value),self.assertRaises(s.SnapshotError):
                s.load(s.MemorySnapshotStore({'manifest.json':s.canonical(manifest)}))

    def test_invalid_snapshot_schema_and_provenance(self):
        for field,value in [('schema_version',2),('schema_version',True),('dataset_kind','observations'),('source','Unknown'),('generated_at','2026-11-01T00:00:00'),('coverage_end','2026-11-03T00:00:00Z'),('records',{})]:
            data={**self.data,field:value}
            with self.subTest(field=field),self.assertRaises(s.SnapshotError):s.validate(data)
        with self.assertRaises(s.SnapshotError):s.validate({**self.data,'raw_body':'not allowed'})

    def test_invalid_record_fields(self):
        cases=[('attraction_id','unknown'),('status','invented'),('status',[]),('wait_minutes',True),('wait_minutes',-1),('wait_minutes',1.5),('wait_minutes',1441),('present',1),('quality_flags',['secret']),('quality_flags',['missing_wait','missing_wait']),('observed_at','2026-11-01T01:50:00-04:00'),('observed_at','2026-11-01T25:50:00Z'),('provider_last_changed_at','2026-11-02T00:00:00Z')]
        for key,value in cases:
            data=deepcopy(self.data);data['records'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(s.SnapshotError):s.validate(data)

    def test_duplicate_observations_rejected(self):
        self.data['records'].append(deepcopy(self.data['records'][0]))
        with self.assertRaises(s.SnapshotError):s.validate(self.data)

    def test_non_json_duplicate_fields_and_size_rejected(self):
        for raw in (b'not json',b'NaN',b'{"a":1,"a":2}',b'\xff',b' '* (s.MAX_BYTES+1)):
            with self.assertRaises(s.SnapshotError):s.decode(raw)

    def test_zero_closure_missing_and_gap_remain_distinct(self):
        view=self.view()
        self.assertEqual(view['points'][0]['wait'],0)
        self.assertIsNone(view['points'][3]['wait'])
        self.assertEqual(view['points'][3]['event'],'Closed')
        self.assertIsNone(view['points'][5]['wait'])
        self.assertEqual(view['points'][5]['event'],'Wait unavailable')
        self.assertTrue(view['points'][7]['gap'])
        self.assertFalse(view['points'][1]['gap'])
        self.assertEqual(len(view['points']),9) # no invented gap samples

    def test_closed_numeric_wait_and_stale_flag_not_plotted(self):
        self.data['records'][0]['status']='CLOSED'
        self.data['records'][1]['quality_flags']=['stale_provider']
        view=self.view()
        self.assertIsNone(view['points'][0]['wait'])
        self.assertIsNone(view['points'][1]['wait'])

    def test_gap_threshold(self):
        self.data['records']=[r for r in self.data['records'] if r['attraction_id']==self.ident][:2]
        self.data['records'][1]['observed_at']='2026-11-01T05:57:30Z'
        self.assertFalse(self.view()['points'][1]['gap'])
        self.data['records'][1]['observed_at']='2026-11-01T05:57:31Z'
        self.assertTrue(self.view()['points'][1]['gap'])

    def test_dst_fallback_uses_distinct_utc_order_and_labels(self):
        points=self.view()['points']
        self.assertIn('01:55 AM EDT',display(points[1]['when']))
        self.assertIn('01:00 AM EST',display(points[2]['when']))
        self.assertEqual((points[2]['when']-points[1]['when']).total_seconds(),300)
        self.assertFalse(points[2]['gap'])
        spring=datetime(2026,3,8,7,tzinfo=timezone.utc)
        self.assertIn('03:00 AM EDT',display(spring))

    def test_all_windows_and_explicit_archive_anchor(self):
        for hours in ('6','24','168'):
            v=self.view(hours=hours)
            self.assertEqual((v['end']-v['start']).total_seconds(),int(hours)*3600)
            self.assertEqual(v['end'],s.utc(self.data['coverage_end']))
            self.assertTrue(v['stale'])
        v=s.window({'attraction_id':self.ident,'mode':'rolling','hours':'6'},self.data,now=datetime(2026,11,10,tzinfo=timezone.utc))
        self.assertFalse(v['points'])
        self.assertFalse(v['meaningful'])

    def test_invalid_request_inputs(self):
        for params in ({'hours':'8'},{'mode':'auto'},{'attraction_id':'unknown'},{'start':'anything'}):
            with self.subTest(params=params),self.assertRaises(s.SnapshotInputError):self.view(**params)

    def test_future_snapshot_rejected(self):
        with self.assertRaises(s.SnapshotError):s.window({'attraction_id':self.ident},self.data,now=datetime(2026,10,1,tzinfo=timezone.utc))

    def test_empty_and_single_numeric_record_insufficient(self):
        self.data['records']=[]
        self.assertEqual(s.load(s.package(self.data)),self.data)
        self.assertFalse(self.view()['meaningful'])
        self.data['records']=[{'attraction_id':self.ident,'observed_at':self.data['coverage_end'],'provider_last_changed_at':None,'status':'OPERATING','wait_minutes':0,'present':True,'quality_flags':[]}]
        self.assertFalse(self.view()['meaningful'])
        self.assertIsNone(svg_chart(self.view()))

    def test_renderer_breaks_and_zero(self):
        svg=ET.fromstring(svg_chart(self.view()))
        path=svg.find('{http://www.w3.org/2000/svg}path').attrib['d']
        self.assertEqual(path.count('M'),4)
        self.assertIn('255.000',path)
        self.assertIn('SYNTHETIC TEST FIXTURE',svg.attrib['aria-label'])

    def test_registry_can_expand_without_snapshot_schema_change(self):
        registry={**s.public_registry(),'00000000-0000-4000-8000-000000000001':{'name':'Reviewed future fixture','park':'Test park'}}
        self.data['records'][0]['attraction_id']='00000000-0000-4000-8000-000000000001'
        self.assertEqual(s.load(s.package(self.data,registry),registry),self.data)


class SnapshotWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ident=next(iter(s.public_registry()))

    def make(self,store=None,**config):
        with patch('app.load_new_model',return_value=object()):
            return create_app({'PUBLIC_DEPLOYMENT':True,'HISTORY_SNAPSHOT_STORE':s.fixture_store() if store is None else store,**config})

    def url(self,extra=''):
        return '/history/chart?attraction_id='+self.ident+extra

    def test_fixture_demo_cannot_access_database_provider_or_network(self):
        with patch('sqlite3.connect',side_effect=AssertionError('No database access')),patch('src.themeparks_provider.ThemeParksProvider.live',side_effect=AssertionError('No provider call'),create=True),patch('socket.socket',side_effect=AssertionError('No network')):
            client=self.make().test_client()
            home=client.get('/')
            self.assertEqual(home.status_code,200)
            chart=client.get(self.url())
            self.assertEqual(chart.status_code,200)
            self.assertIn('SYNTHETIC TEST FIXTURES',chart.get_data(as_text=True))
            self.assertEqual(client.get('/api/history?attraction_id='+self.ident).status_code,503)

    def test_selector_independent_of_current_waits(self):
        html=self.make().test_client().get('/').get_data(as_text=True)
        from html.parser import HTMLParser
        class Selects(HTMLParser):
            def __init__(self):super().__init__();self.inside=False;self.options=[];self.disabled=False
            def handle_starttag(self,tag,attrs):
                a=dict(attrs)
                if tag=='select':self.inside=a.get('id')=='history-ride';self.disabled=self.disabled or self.inside and 'disabled' in a
                if tag=='option' and self.inside and a.get('value'):self.options.append(a['value'])
            def handle_endtag(self,tag):
                if tag=='select':self.inside=False
        parser=Selects();parser.feed(html)
        self.assertEqual(set(parser.options),set(s.public_registry()))
        self.assertFalse(parser.disabled)
        self.assertIn('history-anchor',html)

    def test_chart_html_has_no_json_observation_feed(self):
        response=self.make().test_client().get(self.url())
        html=response.get_data(as_text=True)
        self.assertIn('text/html',response.content_type)
        self.assertIsNone(response.get_json(silent=True))
        self.assertNotIn('"records"',html)
        self.assertNotIn('provider_last_changed_at',html)
        self.assertNotIn(self.ident,html)
        self.assertNotIn('<script',html)
        self.assertIn('Powered by ThemeParks.wiki',html)
        self.assertIn('Archived fixture window',html)
        self.assertIn('Historical snapshot',html)
        self.assertIn('<svg',html)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])

    def test_invalid_queries(self):
        client=self.make().test_client()
        for url in ('/history/chart',self.url('&hours=8'),self.url('&mode=auto'),self.url('&hours=6&hours=24'),self.url('&format=json'),self.url('&start=2026-01-01'),self.url('&x='+'a'*600),'/history/chart?attraction_id=../../private/pilot.sqlite3'):
            self.assertEqual(client.get(url).status_code,400,url)

    def test_outage_and_malformed_manifest_sanitized(self):
        class Outage:
            def get(self,key):raise RuntimeError('SECRET credential C:/private/pilot.sqlite3')
        for store in (Outage(),s.MemorySnapshotStore({'manifest.json':b'not json'})):
            r=self.make(store).test_client().get(self.url())
            self.assertEqual(r.status_code,503)
            self.assertNotIn('SECRET',r.get_data(as_text=True))
            self.assertNotIn('sqlite',r.get_data(as_text=True))
            self.assertNotIn('Traceback',r.get_data(as_text=True))

    def test_rate_limit_and_recovery(self):
        client=self.make().test_client()
        with patch('app.time.monotonic',return_value=100):
            for _ in range(30):self.assertEqual(client.get(self.url()).status_code,200)
            r=client.get(self.url())
            self.assertEqual(r.status_code,429)
            self.assertEqual(r.headers['Retry-After'],'60')
        with patch('app.time.monotonic',return_value=161):
            self.assertEqual(client.get(self.url()).status_code,200)

    def test_empty_and_rolling_outside_coverage_messages(self):
        data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes());data['records']=[]
        r=self.make(s.package(data)).test_client().get(self.url())
        self.assertIn('No fixture observations',r.get_data(as_text=True))
        self.assertNotIn('<svg',r.get_data(as_text=True))
        r=self.make().test_client().get(self.url('&mode=rolling&hours=6'))
        self.assertIn('Rolling fixture window',r.get_data(as_text=True))
        self.assertNotIn('<svg',r.get_data(as_text=True))

    def test_fixture_mode_disables_private_json_even_in_local_config(self):
        app=self.make(PUBLIC_DEPLOYMENT=False,OBSERVED_DATA_ENABLED=True)
        self.assertFalse(app.config['OBSERVED_DATA_ENABLED'])
        self.assertEqual(app.test_client().get('/api/history?attraction_id='+self.ident).status_code,503)

    def test_default_production_is_unchanged_and_no_fixture_static_route(self):
        with patch('app.load_new_model',return_value=object()):
            client=create_app({'PUBLIC_DEPLOYMENT':True}).test_client()
        self.assertEqual(client.get(self.url()).status_code,503)
        self.assertEqual(client.get('/api/history?attraction_id='+self.ident).status_code,503)
        self.assertEqual(client.get('/static/fixtures/history_snapshot_v1.json').status_code,404)
        self.assertEqual(client.get('/tests/fixtures/history_snapshot_v1.json').status_code,404)
        self.assertNotIn('data-snapshot="true"',client.get('/').get_data(as_text=True))

    def test_metadata_is_escaped_not_executable(self):
        registry=s.public_registry();registry[self.ident]['name']='<script>alert(1)</script>'
        with patch('app.public_registry',return_value=registry):
            html=self.make().test_client().get(self.url()).get_data(as_text=True)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)


class SnapshotUUIDTests(unittest.TestCase):
    def test_registry_membership_does_not_bypass_uuid_validation(self):
        data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())
        data['records'][0]['attraction_id']='not-a-uuid'
        registry={**s.public_registry(),'not-a-uuid':{'name':'Invalid fixture','park':'Test park'}}
        with self.assertRaises(s.SnapshotError):s.validate(data,registry)
