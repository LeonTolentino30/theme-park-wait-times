"""Publisher behavior tested solely against invented in-memory objects and fake keys."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src import history_snapshots as s
from src import history_private_publish as p
from src.history_r2 import R2Config

PUB=R2Config('a'*32,p.BUCKET,'fixture-publisher','fixture-publisher-secret')
READ=R2Config('a'*32,p.BUCKET,'fixture-reader','fixture-reader-secret')
MISSING=p.Response(404,b'<Error><Code>NoSuchKey</Code></Error>')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        data=s.decode(Path('tests/fixtures/history_snapshot_v1.json').read_bytes())
        data['schema_version']=2;data['dataset_kind']='collected_observations'
        registry=s.public_registry()
        for row in data['records']:row['park_id']=registry[row['attraction_id']]['park_id']
        self.store=s.package(data,allow_real=True)
        self.manifest=self.store.get('manifest.json');meta=s.decode(self.manifest)
        self.key=meta['key'];self.raw=self.store.get(self.key)
        for name,value in (('DIGEST',meta['sha256']),('SNAPSHOT_KEY',self.key),('SIZE',len(self.raw)),('RECORDS',len(data['records']))):
            patcher=patch.object(p,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.objects={};self.calls=[];self.race=False;self.corrupt=False

    def request(self,method,host,path,headers,body,limit):
        key=path.split('/',2)[2];self.calls.append((method,key,headers.copy()))
        self.assertIn(key,{self.key,'manifest.json'})
        if method=='GET':
            if key not in self.objects:return MISSING
            if self.corrupt and key==self.key:return p.Response(200,b'corrupt')
            return p.Response(200,self.objects[key],'"existing-etag"')
        self.assertEqual(method,'PUT')
        if key=='manifest.json' and self.race:return p.Response(412,b'')
        self.objects[key]=body
        return p.Response(200,b'','"new-etag"')

    def publish(self):
        self.publisher=p.PrivatePublisher(PUB,READ,request=self.request)
        return self.publisher.publish(self.store,Path(self.tmp.name)/'report')

    def test_order_byte_integrity_and_manifest_last(self):
        result=self.publish()
        self.assertTrue(result['passed'])
        self.assertEqual([(m,k) for m,k,_ in self.calls],[('GET','manifest.json'),('GET',self.key),('PUT',self.key),('GET',self.key),('PUT','manifest.json'),('GET','manifest.json')])
        self.assertEqual(self.objects[self.key],self.raw)
        self.assertEqual(self.objects['manifest.json'],self.manifest)
        self.assertEqual(self.calls[2][2]['if-none-match'],'*')
        self.assertEqual(self.calls[4][2]['if-none-match'],'*')
        self.assertFalse(result['public_charts_activated'])

    def test_existing_immutable_refused_without_any_put(self):
        self.objects[self.key]=self.raw
        with self.assertRaises(p.PublicationError):self.publish()
        self.assertTrue(all(method=='GET' for method,_,_ in self.calls))

    def test_previous_manifest_etag_preserved_and_conditionally_replaced(self):
        self.objects['manifest.json']=self.manifest
        result=self.publish()
        self.assertTrue(result['previous_manifest_preserved'])
        self.assertEqual((Path(self.tmp.name)/'report/manifest-before.json').read_bytes(),self.manifest)
        self.assertEqual(self.calls[4][2]['if-match'],'"existing-etag"')

    def test_manifest_race_stops_without_retry_or_delete(self):
        self.race=True
        with self.assertRaises(p.PublicationError):self.publish()
        self.assertTrue(self.publisher.snapshot_written)
        self.assertFalse(self.publisher.manifest_written)
        self.assertEqual(len(self.calls),5)
        self.assertNotIn('manifest.json',self.objects)

    def test_readback_failure_does_not_publish_manifest(self):
        self.corrupt=True
        with self.assertRaises(p.PublicationError):self.publish()
        self.assertNotIn(('PUT','manifest.json'),[(m,k) for m,k,_ in self.calls])

    def test_bad_source_fails_before_network_or_report_directory(self):
        with patch.object(p,'SIZE',p.SIZE+1):
            with self.assertRaises(p.PublicationError):self.publish()
        self.assertFalse(self.calls)
        self.assertFalse((Path(self.tmp.name)/'report').exists())

    def test_bad_prior_manifest_left_unchanged(self):
        self.objects['manifest.json']=b'{"records":["not a manifest"]}'
        with self.assertRaises(p.PublicationError):self.publish()
        self.assertEqual(self.objects['manifest.json'],b'{"records":["not a manifest"]}')
        self.assertTrue(all(m=='GET' for m,_,_ in self.calls))

    def test_only_same_account_distinct_roles_and_approved_keys(self):
        with self.assertRaises(p.PublicationError):p.PrivatePublisher(PUB,PUB)
        with self.assertRaises(p.PublicationError):p.PrivatePublisher(PUB,R2Config('b'*32,p.BUCKET,'fixture-reader','fixture-reader-secret'))
        publisher=p.PrivatePublisher(PUB,READ,request=self.request)
        for method,key in (('DELETE',self.key),('PUT','other-key'),('GET','private.sqlite3')):
            with self.assertRaises(p.PublicationError):publisher.call(method,key,PUB)
        with self.assertRaises(p.PublicationError):publisher.call('PUT',self.key,READ,self.raw,{'if-none-match':'*'})
        self.assertFalse(self.calls)
