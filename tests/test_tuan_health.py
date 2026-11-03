import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from taskmaster import morning_report,remote_context,timefmt,tuan_health

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
try:
    ZoneInfo('America/New_York');HAS_SOURCE_ZONE=True
except ZoneInfoNotFoundError:
    HAS_SOURCE_ZONE=False
NOW='2026-11-03T12:00:00Z'


class Done:
    def __init__(self,stdout='',returncode=0):self.stdout,self.returncode=stdout,returncode


def doctor(**extra):
    report={'attention':[],'definite_misconfiguration':[],'install':{'tuan_version':'0.15.1'},
            'collection':{'tools':{'codex':{'present':True,'gaps':0,'oldest_gap_age_s':None},
                                   'claude':{'present':True,'gaps':0,'oldest_gap_age_s':None}}}}
    report.update(extra)
    return lambda *a,**k:Done(json.dumps(report))


def projection(health,status='current'):
    return {'schema':tuan_health.PROJECTION,'evaluated_at':NOW,'status':status,'health':health}


class ObserveTests(unittest.TestCase):
    def observe(self,run,which=lambda name:'tuan'):
        with patch.object(tuan_health.timefmt,'now',return_value=NOW):
            return tuan_health.observe('store',run=run,which=which)

    def test_healthy_summary_keeps_counts_only(self):
        value=self.observe(doctor())
        self.assertEqual((value['status'],value['version'],value['attention']),('current','0.15.1',[]))
        self.assertEqual(value['tools']['codex'],{'gaps':0,'oldest_gap_age_s':None})

    def test_gap_summary(self):
        gaps={'tools':{'claude':{'present':True,'gaps':2,'oldest_gap_age_s':7300,'gap_sessions':[{'generation':'x'}]}}}
        value=self.observe(doctor(attention=['collection'],collection=gaps))
        self.assertEqual(value['tools'],{'claude':{'gaps':2,'oldest_gap_age_s':7300}})
        self.assertNotIn('x',json.dumps(value))

    def test_every_failure_is_a_typed_error(self):
        def timeout(*a,**k):raise subprocess.TimeoutExpired('tuan',10)
        def missing(*a,**k):raise FileNotFoundError('tuan')
        cases=[(doctor(),lambda name:None,'tuan_missing'),(timeout,None,'doctor_timeout'),(missing,None,'doctor_failed'),
               (lambda *a,**k:Done('',2),None,'doctor_failed'),(lambda *a,**k:Done('nope'),None,'doctor_invalid'),
               (lambda *a,**k:Done(json.dumps({'store':{}})),None,'doctor_invalid')]
        for run,which,code in cases:
            value=self.observe(run,which or (lambda name:'tuan'))
            self.assertEqual((value['status'],value['error']),('error',code))
            tuan_health.validate(value)

    def test_missing_pointer(self):
        with tempfile.TemporaryDirectory() as t, patch.object(tuan_health,'_pointer',return_value=Path(t)/'none'):
            self.assertEqual(tuan_health.observe(None,run=doctor(),which=lambda name:'tuan')['error'],'store_pointer_missing')


@unittest.skipUnless(HAS_SOURCE_ZONE,'the collector validates the source zone; it runs on the Linux owner')
class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.cfg={'destination':str(self.root/'snapshot.json'),'dias_url':'http://127.0.0.1:4175','timezone':'America/New_York',
                  'notes':{},'command':['capture'],'tuan':{}}
        (self.root/'collector.json').write_text(json.dumps(self.cfg))
        self.calls=[]

    def fake_run(self,command,input,stdout,**kwargs):
        request=json.loads(input);self.calls.append(request)
        with patch.object(remote_context.urllib.request,'urlopen',side_effect=OSError('offline')), \
             patch.object(tuan_health,'observe',return_value=self.health):
            stdout.write(json.dumps(remote_context.capture(request)).encode())
        return Done()

    def collect(self):
        with patch.object(remote_context.subprocess,'run',side_effect=self.fake_run):
            remote_context.collect(self.root/'collector.json')
        return json.loads((self.root/'snapshot.json').read_text())

    def test_doctor_runs_on_cadence_and_is_carried_between(self):
        self.health=tuan_health._error('doctor_timeout')
        first=self.collect()
        self.assertEqual((first['schema'],first['tuan_health']['error']),(remote_context.V2,'doctor_timeout'))
        self.health=None  # a second doctor run would fail validation
        second=self.collect()
        self.assertNotIn('tuan',self.calls[1]);self.assertEqual(second['tuan_health'],first['tuan_health'])
        remote_context.validate(second,timefmt.now())

    def test_v1_envelope_and_capture_without_tuan_stay_valid(self):
        self.cfg.pop('tuan');(self.root/'collector.json').write_text(json.dumps(self.cfg));self.health=None
        snapshot=self.collect()
        self.assertEqual(snapshot['schema'],remote_context.V1);self.assertNotIn('tuan_health',snapshot)
        self.assertNotIn('tuan',self.calls[0])

    def test_old_capture_runtime_is_a_typed_error(self):
        def old(command,input,stdout,**kwargs):
            request=json.loads(input);request.pop('tuan',None)
            with patch.object(remote_context.urllib.request,'urlopen',side_effect=OSError('offline')):
                stdout.write(json.dumps(remote_context.capture(request)).encode())
            return Done()
        with patch.object(remote_context.subprocess,'run',side_effect=old):
            remote_context.collect(self.root/'collector.json')
        self.assertEqual(json.loads((self.root/'snapshot.json').read_text())['tuan_health']['error'],'capture_unsupported')

    def test_transport_failure_keeps_previous_observation(self):
        self.health=tuan_health._error('tuan_missing');first=self.collect()
        with patch.object(remote_context.subprocess,'run',return_value=Done(returncode=255)):
            remote_context.collect(self.root/'collector.json')
        snapshot=json.loads((self.root/'snapshot.json').read_text())
        self.assertEqual((snapshot['dias_status'],snapshot['tuan_health']),('unavailable',first['tuan_health']))

    def test_capture_rejects_unknown_request_keys(self):
        with self.assertRaises(ValueError):
            remote_context.capture({'dias_url':'http://127.0.0.1:4175','timezone':'UTC','notes':{},'other':1})
        with self.assertRaises(ValueError):
            remote_context.capture({'dias_url':'http://127.0.0.1:4175','timezone':'UTC','notes':{},'tuan':{'store':3}})


class RenderTests(unittest.TestCase):
    def health(self,**extra):
        value={'observed_at':'2026-11-03T11:00:00Z','status':'current','error':None,'version':'0.15.1',
               'attention':[],'definite':[],'tools':{'codex':{'gaps':0,'oldest_gap_age_s':None}}}
        value.update(extra);return value

    def test_lines(self):
        cases=[(projection(self.health()),'healthy at 2026-11-03T11:00:00Z (tuan 0.15.1)'),
               (projection(self.health(attention=['collection','hooks'],tools={'codex':{'gaps':3,'oldest_gap_age_s':9000}})),
                'needs attention: collection (codex 3 uncollected, oldest 2 h), hooks'),
               (projection(self.health(status='error',error='tuan_missing',version=None)),'check failed (tuan_missing)'),
               (projection(self.health(observed_at='2026-11-03T05:00:00Z')),'stale since 2026-11-03T05:00:00Z'),
               (projection(None),'no health observation yet'),
               (projection(None,'unavailable'),'unavailable (remote context not current)')]
        for value,expected in cases:
            self.assertIn(expected,tuan_health.render(value,NOW))

    def test_morning_report_appends_the_line(self):
        with tempfile.TemporaryDirectory() as t:
            export=Path(t)/'export.json'
            self.assertEqual(morning_report.tuan_context(export,NOW),b'')
            (Path(t)/'tuan-health.json').write_text(json.dumps(projection(self.health())))
            self.assertTrue(morning_report.tuan_context(export,NOW).startswith(b'Tuan collection: healthy'))
            (Path(t)/'tuan-health.json').write_text('{"schema": 1}')
            self.assertEqual(morning_report.tuan_context(export,NOW),b'Tuan collection: unavailable\n')

    def test_watcher_projection(self):
        with tempfile.TemporaryDirectory() as t:
            settings={'context_file':str(Path(t)/'none.json'),'export':str(Path(t)/'out/export.json')}
            (Path(t)/'out').mkdir()
            value=tuan_health.publish(settings,NOW)
            self.assertEqual((value['status'],value['health']),('unavailable',None))
            self.assertEqual(json.loads((Path(t)/'out/tuan-health.json').read_text()),value)


if __name__=='__main__':
    unittest.main()
