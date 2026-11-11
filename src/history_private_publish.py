"""One explicitly approved private publication. Fixed keys, no SQLite/deletes/activation."""
from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from . import history_snapshots as snapshots
from .history_r2 import R2Config
from .history_real_snapshot import PrivateLocalStore, ROOT, BUNDLE_NAME
from .history_extraction import quality_summary
from .r2_connectivity_probe import sign_probe, safe_code, SECRET_NAMES

BUCKET = "queue-atlas-history"
DIGEST = "931fc9fffabdae743eb10871e8e75babea4b023a7859d4a4f311f78c26c29de7"
SNAPSHOT_KEY = "history/v2/"+DIGEST+".json"
MANIFEST_KEY = "manifest.json"
SIZE = 176_510
RECORDS = 602


class PublicationError(Exception):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    etag: str | None = None


def transport(method,host,path,headers,body,limit):
    connection=http.client.HTTPSConnection(host,timeout=10)
    try:
        connection.request(method,path,body=body if method=='PUT' else None,headers=headers)
        response=connection.getresponse()
        length=response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length)>limit):raise PublicationError()
        raw=response.read(limit+1)
        if len(raw)>limit or length is not None and len(raw)!=int(length):raise PublicationError()
        etag=response.getheader('ETag')
        if etag is not None and (len(etag)>256 or any(ord(c)<32 or ord(c)>126 for c in etag)):raise PublicationError()
        return Response(response.status,raw,etag)
    finally:
        connection.close()


class PrivatePublisher:
    def __init__(self,publisher,reader,*,request=None):
        if (publisher.bucket!=BUCKET or reader.bucket!=BUCKET or publisher.account_id!=reader.account_id
                or publisher.access_key_id==reader.access_key_id):
            raise PublicationError('Distinct verified roles and one exact account/bucket are required')
        self.publisher,self.reader=publisher,reader
        self._transport=request or transport
        self.events=[]
        self.snapshot_written=False
        self.manifest_written=False

    def call(self,method,key,config,body=b'',condition=None):
        if method not in {'GET','PUT'} or key not in {SNAPSHOT_KEY,MANIFEST_KEY}:raise PublicationError()
        if method=='PUT':
            if key==SNAPSHOT_KEY and (len(body)!=SIZE or hashlib.sha256(body).hexdigest()!=DIGEST):raise PublicationError()
            if config is not self.publisher:raise PublicationError()
            if condition is None or set(condition) not in ({'if-none-match'},{'if-match'}):raise PublicationError()
            if key==SNAPSHOT_KEY and condition!={'if-none-match':'*'}:raise PublicationError()
        host=self.reader.account_id+'.r2.cloudflarestorage.com'
        path='/'+BUCKET+'/'+quote(key,safe='/')
        extra={**(condition or {}),**({'content-type':'application/json'} if method=='PUT' else {})}
        headers=sign_probe(method,host,path,config,body,extra)
        response=self._transport(method,host,path,headers,body,snapshots.MAX_BYTES if key==SNAPSHOT_KEY else 16_384)
        self.events.append({'method':method,'key':key,'http_status':response.status})
        return response

    def publish(self,store,folder):
        # Stable in-memory pair, validated before credential-bearing network requests.
        manifest_raw=store.get(MANIFEST_KEY)
        raw=store.get(SNAPSHOT_KEY)
        pair=snapshots.MemorySnapshotStore({MANIFEST_KEY:manifest_raw,SNAPSHOT_KEY:raw})
        data=snapshots.load(pair,allow_real=True)
        if (len(raw)!=SIZE or len(data['records'])!=RECORDS or hashlib.sha256(raw).hexdigest()!=DIGEST
                or data['schema_version']!=2 or data['dataset_kind']!='collected_observations'
                or snapshots.decode(manifest_raw)['key']!=SNAPSHOT_KEY):raise PublicationError('Unapproved source')
        folder=Path(folder)
        folder.mkdir(exist_ok=False)
        previous=self.call('GET',MANIFEST_KEY,self.reader)
        if previous.status==200:
            if not previous.etag:raise PublicationError('Cannot conditionally replace a manifest without its ETag')
            old=snapshots.decode(previous.body)
            if (not isinstance(old,dict) or set(old)!={'schema_version','dataset_kind','key','sha256','bytes'}
                    or type(old['schema_version']) is not int or old['schema_version'] not in {1,2}
                    or old['dataset_kind'] not in {'synthetic_test_fixture','collected_observations'}
                    or not isinstance(old['sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',old['sha256'])
                    or old['key']!=f"history/v{old['schema_version']}/{old['sha256']}.json"
                    or type(old['bytes']) is not int or not 0<old['bytes']<=snapshots.MAX_BYTES):
                raise PublicationError('Existing manifest needs review; nothing will be replaced')
            # Exact manifest metadata only; never copy/download its referenced observations.
            with (folder/'manifest-before.json').open('xb') as f:f.write(previous.body)
            condition={'if-match':previous.etag}
        elif previous.status==404 and safe_code(previous.body)=='NoSuchKey':
            condition={'if-none-match':'*'}
        else:raise PublicationError('Manifest preflight failed')
        with (folder/'preflight.json').open('x',encoding='utf-8') as f:
            json.dump({'bucket':BUCKET,'account_sha256':hashlib.sha256(self.reader.account_id.encode()).hexdigest(),
                       'previous_manifest_present':previous.status==200,'previous_manifest_etag':previous.etag,
                       'manifest_condition':condition},f,indent=2)
        existing=self.call('GET',SNAPSHOT_KEY,self.reader)
        if existing.status==200:raise PublicationError('Immutable destination already exists; no writes attempted')
        if existing.status!=404 or safe_code(existing.body)!='NoSuchKey':raise PublicationError('Snapshot preflight failed')
        uploaded=self.call('PUT',SNAPSHOT_KEY,self.publisher,raw,{'if-none-match':'*'})
        if uploaded.status not in {200,201}:raise PublicationError('Immutable snapshot conditional write failed')
        self.snapshot_written=True
        fetched=self.call('GET',SNAPSHOT_KEY,self.reader)
        if fetched.status!=200 or fetched.body!=raw or hashlib.sha256(fetched.body).hexdigest()!=DIGEST:
            raise PublicationError('Read-only snapshot readback verification failed')
        snapshots.load(snapshots.MemorySnapshotStore({MANIFEST_KEY:manifest_raw,SNAPSHOT_KEY:fetched.body}),allow_real=True)
        result=self.call('PUT',MANIFEST_KEY,self.publisher,manifest_raw,condition)
        if result.status not in {200,201}:raise PublicationError('Manifest conditional publication failed; no retries or rollback performed')
        self.manifest_written=True
        final=self.call('GET',MANIFEST_KEY,self.reader)
        if final.status!=200 or final.body!=manifest_raw:raise PublicationError('Manifest readback verification failed')
        verified=snapshots.load(snapshots.MemorySnapshotStore({MANIFEST_KEY:final.body,SNAPSHOT_KEY:fetched.body}),allow_real=True)
        if len(verified['records'])!=RECORDS:raise PublicationError('Unexpected record count')
        if store.get(MANIFEST_KEY)!=manifest_raw or store.get(SNAPSHOT_KEY)!=raw:raise PublicationError('Local source changed')
        return {'passed':True,'bucket':BUCKET,'account_sha256':hashlib.sha256(self.reader.account_id.encode()).hexdigest(),
                'snapshot_key':SNAPSHOT_KEY,'manifest_key':MANIFEST_KEY,'sha256':DIGEST,'bytes':SIZE,'records':RECORDS,
                'read_only_byte_for_byte_verified':True,'manifest_readback_verified':True,
                'previous_manifest_preserved':previous.status==200,'previous_manifest_etag':previous.etag,
                'public_access_basis':'Owner verified r2.dev disabled, no custom domains, Basin Catalog disabled, Standard storage',
                'bucket_settings_changed':False,'public_charts_activated':False,'collector_database_accessed':False,
                'aggregate':quality_summary(data,allow_real=True),'events':self.events}


def main():
    parser=argparse.ArgumentParser(description='One approved private snapshot + manifest publication only')
    parser.add_argument('--publish-approved-snapshot',action='store_true',required=True)
    parser.add_argument('--owner-verified-private-settings',action='store_true',required=True)
    parser.add_argument('--report-directory',required=True)
    args=parser.parse_args()
    folder=Path(args.report_directory).resolve()
    base=Path(os.environ['LOCALAPPDATA']).resolve()/'QueueAtlas/credentials'
    if folder.parent!=base or not re.fullmatch(r'r2-publication-[0-9a-f]{32}',folder.name):
        print('{"passed":false,"error":"Unsafe private report directory"}');return 1
    publisher=None;values=[]
    try:
        reader=R2Config.from_env(os.environ)
        writer=R2Config(reader.account_id,reader.bucket,os.environ[SECRET_NAMES[2]],os.environ[SECRET_NAMES[3]])
        values=[reader.access_key_id,reader.secret_access_key,writer.access_key_id,writer.secret_access_key]
        for name in SECRET_NAMES:os.environ.pop(name,None)
        publisher=PrivatePublisher(writer,reader)
        report=publisher.publish(PrivateLocalStore(ROOT/'private/themeparks/snapshots'/BUNDLE_NAME),folder)
        code=0
    except (Exception,KeyboardInterrupt):
        report={'passed':False,'message':'Private publication stopped; no automatic retry, deletion or rollback performed.',
                'snapshot_put_confirmed':bool(publisher and publisher.snapshot_written),
                'manifest_put_confirmed':bool(publisher and publisher.manifest_written),
                'events':publisher.events if publisher else []}
        code=1
    finally:
        for name in SECRET_NAMES:os.environ.pop(name,None)
    raw=json.dumps(report,indent=2)
    if any(value in raw for value in values):
        print('{"passed":false,"message":"Report suppressed by credential-redaction guard"}');return 1
    if folder.is_dir():
        with (folder/'publication-report.json').open('x',encoding='utf-8') as stream:stream.write(raw)
    print(raw)
    return code


if __name__=='__main__':raise SystemExit(main())
