"""Synthetic-only permission probe tests; no real credentials or network."""
from datetime import datetime, timezone
import unittest
from src.r2_connectivity_probe import Probe, ProbeFailure, sign_probe, safe_code, BUCKET, PREFIX
from src.history_r2 import R2Config, signed_get

PUB=R2Config('a'*32,BUCKET,'fixture-publisher-key','fixture-publisher-secret')
READ=R2Config('a'*32,BUCKET,'fixture-reader-key','fixture-reader-secret')
DENIED=b'<Error><Code>AccessDenied</Code><Message>PRIVATE ERROR</Message></Error>'


class ProbeTests(unittest.TestCase):
    def fixture(self,write_status=403,delete_status=403,anonymous_status=403):
        calls=[];probe=None
        def request(method,host,path,headers,body):
            calls.append((method,path))
            self.assertIn('/'+BUCKET+'/'+PREFIX,path)
            if method=='PUT' and path.endswith('probe.json'):
                self.assertEqual(headers['if-none-match'],'*')
                return 200,b''
            if method=='PUT':return write_status,DENIED
            if method=='DELETE':return delete_status,DENIED
            self.assertEqual(headers,{})
            return anonymous_status,DENIED if anonymous_status==403 else probe.body
        def get(host,path,headers,limit):
            calls.append(('SIGNED_GET',path));return 200,probe.body
        probe=Probe(PUB,READ,run_id='0'*32,transport=request,reader_transport=get)
        return probe,calls

    def test_all_requested_checks_and_no_real_prefixes(self):
        probe,calls=self.fixture();report=probe.run()
        self.assertTrue(report['passed'])
        self.assertEqual(len(report['checks']),6)
        self.assertEqual(len(calls),6)
        self.assertTrue(all('/history/' not in path for _,path in calls))
        self.assertEqual(len(report['test_objects_may_remain']),1)
        self.assertFalse(report['real_observations_accessed'])
        self.assertFalse(report['credentials_logged'])
        self.assertNotIn('PRIVATE ERROR',str(report))

    def test_overprivileged_write_stops_immediately(self):
        probe,calls=self.fixture(write_status=200);report=probe.run()
        self.assertFalse(report['passed'])
        self.assertEqual(len(calls),3)
        self.assertFalse(report['checks'][-1]['passed'])
        self.assertEqual(len(report['test_objects_may_remain']),2)

    def test_overprivileged_delete_stops_before_anonymous_checks(self):
        probe,calls=self.fixture(delete_status=204);report=probe.run()
        self.assertFalse(report['passed'])
        self.assertEqual(len(calls),4)
        self.assertEqual(report['test_objects_may_remain'],[])

    def test_anonymous_access_success_is_failure(self):
        probe,calls=self.fixture(anonymous_status=200);report=probe.run()
        self.assertFalse(report['passed'])
        self.assertEqual(len(calls),5)

    def test_key_scope_and_bucket_cannot_be_changed(self):
        probe,_=self.fixture()
        for key in ('manifest.json','history/v2/private.json','../real-data'):
            with self.assertRaises(ProbeFailure):probe.request('DELETE',key,READ)
        with self.assertRaises(ProbeFailure):Probe(PUB,R2Config('b'*32,BUCKET,'fixture-reader','fixture-secret'))
        with self.assertRaises(ProbeFailure):Probe(R2Config('a'*32,'other-bucket','fixture-reader','fixture-secret'),READ)

    def test_write_signer_matches_existing_get_adapter(self):
        now=datetime(2026,11,10,tzinfo=timezone.utc)
        self.assertEqual(sign_probe('GET','fixed.test','/path',READ,now=now),signed_get('fixed.test','/path',READ.access_key_id,READ.secret_access_key,now))
        self.assertNotEqual(sign_probe('PUT','fixed.test','/path',READ,b'fixture',now=now)['x-amz-content-sha256'],sign_probe('GET','fixed.test','/path',READ,now=now)['x-amz-content-sha256'])

    def test_error_code_parser_redacts_unknown_content(self):
        self.assertEqual(safe_code(DENIED),'AccessDenied')
        self.assertEqual(safe_code(b'<Error><Code>SECRET_KEY</Code></Error>'),'redacted')
        self.assertEqual(safe_code(b'not XML'),'redacted')

    def test_transport_errors_never_print_secrets(self):
        probe,_=self.fixture()
        probe._transport=lambda *_:(_ for _ in ()).throw(RuntimeError('SECRET_VALUE'))
        report=probe.run()
        self.assertFalse(report['passed']);self.assertNotIn('SECRET_VALUE',str(report))


class AnonymousDenialTests(unittest.TestCase):
    def test_missing_auth_400_is_denied_but_unrelated_400_is_inconclusive(self):
        from src.r2_connectivity_probe import anonymous_auth_denied
        self.assertTrue(anonymous_auth_denied(400,b'<Error><Code>InvalidArgument</Code><Message>Authorization is required</Message></Error>'))
        self.assertFalse(anonymous_auth_denied(400,b'<Error><Code>InvalidArgument</Code><Message>Unrelated invalid path</Message></Error>'))
        self.assertFalse(anonymous_auth_denied(200,b'fixture'))


class CredentialAuditTests(unittest.TestCase):
    def test_unique_nonsecret_fixture_value_has_no_source_match(self):
        from uuid import uuid4
        from src.r2_connectivity_probe import audit_source_credentials
        self.assertGreater(audit_source_credentials(['nonsecret-audit-'+uuid4().hex]),0)

    def test_deliberate_public_fixture_value_is_detected_without_echo(self):
        from src.r2_connectivity_probe import audit_source_credentials
        with self.assertRaises(ProbeFailure):audit_source_credentials(['fixture-reader-key'])


class AuthorizedCleanupTests(unittest.TestCase):
    def test_cleanup_touches_only_exact_authorized_key(self):
        from src.r2_connectivity_probe import CLEANUP_RUN_ID
        calls=[];probe=None
        def transport(method,host,path,headers,body):
            calls.append((method,path))
            if len(calls)==1:return 200,probe.body
            if method=='DELETE':return 204,b''
            return 404,b'<Error><Code>NoSuchKey</Code></Error>'
        probe=Probe(PUB,READ,run_id=CLEANUP_RUN_ID,transport=transport)
        report=probe.cleanup_phase8m()
        self.assertTrue(report['passed']);self.assertTrue(report['deleted_in_this_run'])
        self.assertEqual([method for method,_ in calls],['GET','DELETE','GET'])
        self.assertEqual(len({path for _,path in calls}),1)
        self.assertTrue(calls[0][1].endswith('/'+PREFIX+CLEANUP_RUN_ID+'/probe.json'))

    def test_replaced_object_is_not_deleted(self):
        from src.r2_connectivity_probe import CLEANUP_RUN_ID
        calls=[]
        def transport(*args):calls.append(args);return 200,b'not the synthetic fixture'
        report=Probe(PUB,READ,run_id=CLEANUP_RUN_ID,transport=transport).cleanup_phase8m()
        self.assertFalse(report['passed']);self.assertEqual(len(calls),1)

    def test_other_run_is_rejected_without_requests(self):
        calls=[]
        probe=Probe(PUB,READ,run_id='1'*32,transport=lambda *args:calls.append(args))
        with self.assertRaises(ProbeFailure):probe.cleanup_phase8m()
        self.assertFalse(calls)

    def test_already_absent_is_reported_without_delete(self):
        from src.r2_connectivity_probe import CLEANUP_RUN_ID
        calls=[]
        def transport(*args):calls.append(args);return 404,b'<Error><Code>NoSuchKey</Code></Error>'
        report=Probe(PUB,READ,run_id=CLEANUP_RUN_ID,transport=transport).cleanup_phase8m()
        self.assertTrue(report['passed']);self.assertFalse(report['deleted_in_this_run']);self.assertEqual(len(calls),1)
