"""Explicit live connectivity probe: synthetic bytes and a new UUID prefix only."""
from datetime import datetime, timezone
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from uuid import uuid4
import xml.etree.ElementTree as ET
from .history_r2 import R2Config, R2ReadOnlyStore, signed_get

BUCKET = "queue-atlas-history"
PREFIX = "connectivity-tests/phase8m/"
CLEANUP_RUN_ID = "5572205a51074e0fb76948a2afc608b4"
SECRET_NAMES = ("QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID", "QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY",
                "QUEUE_ATLAS_R2_PUBLISH_ACCESS_KEY_ID", "QUEUE_ATLAS_R2_PUBLISH_SECRET_ACCESS_KEY")
SAFE_CODES = {"AccessDenied", "SignatureDoesNotMatch", "InvalidAccessKeyId", "RequestTimeTooSkewed", "PreconditionFailed", "NoSuchBucket", "NoSuchKey", "InvalidArgument", "InvalidRequest", "AuthorizationHeaderMalformed", "MissingAuthenticationToken"}


class ProbeFailure(Exception):
    pass


def sign_probe(method, host, path, config, body=b"", extra=None, now=None):
    if method not in {"GET", "PUT", "DELETE"}:
        raise ProbeFailure()
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ"); date = stamp[:8]
    body_hash = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-date": stamp, "x-amz-content-sha256": body_hash, **(extra or {})}
    names = ";".join(sorted(headers))
    canonical_headers = "".join(k+":"+" ".join(headers[k].split())+"\n" for k in sorted(headers))
    canonical = "\n".join((method, path, "", canonical_headers, names, body_hash))
    scope = date+"/auto/s3/aws4_request"
    to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = ("AWS4"+config.secret_access_key).encode()
    for part in (date, "auto", "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={config.access_key_id}/{scope},SignedHeaders={names},Signature={signature}"
    return headers


def safe_code(body):
    try:
        value = ET.fromstring(body).findtext("Code")
        return value if value in SAFE_CODES else "redacted"
    except ET.ParseError:
        return "redacted"


def anonymous_auth_denied(status, body):
    if status == 403 and safe_code(body) == "AccessDenied":return True
    if status not in {400,401,403}:return False
    try:
        root=ET.fromstring(body)
        message=(root.findtext("Message") or "").lower()
        return safe_code(body) in {"InvalidArgument","InvalidRequest","AuthorizationHeaderMalformed","MissingAuthenticationToken"} and any(word in message for word in ("authorization","authentication","signature","access key","security token"))
    except ET.ParseError:return False


def https_request(method, host, path, headers, body):
    connection = http.client.HTTPSConnection(host, timeout=8)
    try:
        connection.request(method, path, body=body if method=="PUT" else None, headers=headers)
        response = connection.getresponse()
        raw = response.read(4097)
        if len(raw)>4096:
            raise ProbeFailure()
        return response.status, raw
    finally:
        connection.close()


class Probe:
    def __init__(self, publisher, reader, *, run_id=None, transport=None, reader_transport=None):
        if publisher.bucket != BUCKET or reader.bucket != BUCKET or publisher.account_id != reader.account_id:
            raise ProbeFailure()
        self.run_id = run_id or uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}",self.run_id):raise ProbeFailure()
        self.prefix = PREFIX+self.run_id+"/"
        self.keys = {self.prefix+"probe.json",self.prefix+"write-denial.json"}
        self.object_key = self.prefix+"probe.json"
        self.body = json.dumps({"queue_atlas_connectivity_test":True,"contains_real_observations":False,"run_id":self.run_id},separators=(",", ":")).encode()
        self.publisher,self.reader = publisher,reader
        self._transport = transport or https_request
        self._reader_transport = reader_transport
        self.checks = []
        self.objects_may_exist = []

    def request(self, method, key, config=None, body=b""):
        if key not in self.keys or method not in {"GET","PUT","DELETE"}:raise ProbeFailure()
        host = self.reader.account_id+".r2.cloudflarestorage.com"
        path = "/"+BUCKET+"/"+quote(key,safe="/")
        extra = {"if-none-match":"*"} if method=="PUT" else None
        headers = sign_probe(method,host,path,config,body,extra) if config is not None else {}
        return self._transport(method,host,path,headers,body)

    def reader_get(self):
        # Exercise the existing adapter's authenticated GET transport without expanding
        # its production manifest/object key allowlist to a general bucket proxy.
        host = self.reader.account_id+".r2.cloudflarestorage.com"
        path = "/"+BUCKET+"/"+self.object_key
        headers = signed_get(host,path,self.reader.access_key_id,self.reader.secret_access_key,datetime.now(timezone.utc))
        if self._reader_transport:
            return self._reader_transport(host,path,headers,4096)
        result = R2ReadOnlyStore(self.reader)._https_get(host,path,headers,4096)
        return result.status,result.body

    def check(self,name,status,passed,body=b""):
        item = {"check":name,"http_status":status,"passed":passed}
        if not passed or status==403:item["error_code"]=safe_code(body)
        self.checks.append(item)
        if not passed:raise ProbeFailure()

    def recheck_anonymous(self):
        try:
            status,body=self.request("GET",self.object_key)
            self.check("anonymous_s3_get_authentication_required",status,anonymous_auth_denied(status,body),body)
            status,body=self.reader_get()
            self.check("fixture_still_intact",status,status==200 and body==self.body,body)
            passed=True
        except (Exception,KeyboardInterrupt):
            passed=False
        return {"passed":passed,"test_prefix":self.prefix,"checks":self.checks,
                "read_only_followup":True,"real_observations_accessed":False,"credentials_logged":False,
                "public_access_scope":"Anonymous S3 object GET only; r2.dev/custom domains not independently audited."}

    def cleanup_phase8m(self):
        if self.run_id != CLEANUP_RUN_ID:
            raise ProbeFailure()
        deleted=False
        try:
            status,body=self.request("GET",self.object_key,self.reader)
            if status==404 and safe_code(body)=="NoSuchKey":
                self.check("authorized_fixture_already_absent",status,True,body)
            else:
                self.check("authorized_fixture_identity",status,status==200 and body==self.body,body)
                status,body=self.request("DELETE",self.object_key,self.publisher)
                self.check("publisher_deleted_only_authorized_fixture",status,status in {200,204},body)
                deleted=True
                status,body=self.request("GET",self.object_key,self.reader)
                self.check("authorized_fixture_absence_verified",status,status==404 and safe_code(body)=="NoSuchKey",body)
            passed=True
        except (Exception,KeyboardInterrupt):
            passed=False
        return {"passed":passed,"cleanup_object_key":self.object_key,"deleted_in_this_run":deleted,"checks":self.checks,
                "real_observations_accessed":False,"credentials_logged":False}

    def run(self):
        try:
            status,body = self.request("PUT",self.object_key,self.publisher,self.body)
            if status in {200,201}:self.objects_may_exist.append(self.object_key)
            self.check("publisher_put_synthetic_object",status,status in {200,201},body)
            status,body = self.reader_get()
            self.check("readonly_get_exact_fixture",status,status==200 and body==self.body,body)
            deny_key=self.prefix+"write-denial.json"
            status,body=self.request("PUT",deny_key,self.reader,self.body)
            if status in {200,201}:self.objects_may_exist.append(deny_key)
            self.check("readonly_put_denied",status,status==403 and safe_code(body)=="AccessDenied",body)
            status,body=self.request("DELETE",self.object_key,self.reader)
            if status in {200,202,204} and self.object_key in self.objects_may_exist:self.objects_may_exist.remove(self.object_key)
            self.check("readonly_delete_denied",status,status==403 and safe_code(body)=="AccessDenied",body)
            status,body=self.request("GET",self.object_key)
            self.check("anonymous_s3_get_denied",status,anonymous_auth_denied(status,body),body)
            status,body=self.reader_get()
            self.check("fixture_still_intact",status,status==200 and body==self.body,body)
            passed=True
        except (Exception,KeyboardInterrupt):
            passed=False
            if not self.checks or self.checks[-1]["passed"]:
                self.checks.append({"check":"transport_or_configuration","passed":False,"message":"Probe interrupted or failed; details redacted."})
        return {"passed":passed,"test_prefix":self.prefix,"checks":self.checks,
                "test_objects_may_remain":self.objects_may_exist,
                "public_access_scope":"Anonymous S3 object GET only; r2.dev/custom domains not independently audited.",
                "real_observations_accessed":False,"credentials_logged":False}


def audit_source_credentials(credentials):
    root=Path(__file__).resolve().parents[1]
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root).decode().split('\0')
    checked=0
    extensions={'.py','.ps1','.md','.html','.css','.js','.json','.yaml','.yml','.toml','.txt'}
    for name in set(names)-{''}:
        relative=Path(name)
        if relative.parts[0] in {'private','models','venv','.venv','.git'}:continue
        path=root/relative
        if not path.is_file() or path.suffix.lower() not in extensions:continue
        if root not in path.resolve().parents:raise ProbeFailure()
        raw=path.read_bytes()
        if any(secret.encode() in raw for secret in credentials):raise ProbeFailure()
        checked+=1
    return checked


def main():
    parser=argparse.ArgumentParser(description="Authorized synthetic-only R2 credential probe")
    parser.add_argument("--report",required=True)
    parser.add_argument("--recheck-run-id")
    parser.add_argument("--audit-only",action="store_true")
    parser.add_argument("--cleanup-phase8m",action="store_true")
    args=parser.parse_args()
    report_path=Path(args.report).resolve()
    expected=Path(os.environ['LOCALAPPDATA']).resolve()/"QueueAtlas/credentials"
    if report_path.parent!=expected or not re.fullmatch(r"r2-connectivity-[0-9a-f]{32}\.json",report_path.name):
        print('{"passed":false,"message":"Unsafe report location."}');return 1
    credentials=[]
    try:
        reader=R2Config.from_env(os.environ)
        publisher=reader if args.recheck_run_id else R2Config(reader.account_id,reader.bucket,os.environ[SECRET_NAMES[2]],os.environ[SECRET_NAMES[3]])
        credentials=[reader.access_key_id,reader.secret_access_key,publisher.access_key_id,publisher.secret_access_key]
        for name in SECRET_NAMES:os.environ.pop(name,None)
        if args.audit_only:
            checked=audit_source_credentials(credentials)
            result={"passed":True,"credential_audit_passed":True,"first_party_text_files_checked":checked,"network_requests":0,"real_observations_accessed":False,"credentials_logged":False}
        elif args.cleanup_phase8m:
            result=Probe(publisher,reader,run_id=CLEANUP_RUN_ID).cleanup_phase8m()
        else:
            probe=Probe(publisher,reader,run_id=args.recheck_run_id)
            result=probe.recheck_anonymous() if args.recheck_run_id else probe.run()
        raw=json.dumps(result,indent=2)
        if any(value in raw for value in credentials):raise ProbeFailure()
        with report_path.open('x',encoding='utf-8') as stream:stream.write(raw)
        print(raw)
        return 0 if result['passed'] else 1
    except (Exception,KeyboardInterrupt):
        print('{"passed":false,"message":"Probe did not complete; configuration or transport details redacted."}')
        return 1
    finally:
        for name in SECRET_NAMES:os.environ.pop(name,None)


if __name__=='__main__':
    raise SystemExit(main())
