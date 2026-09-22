import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab.live.capacity import main, monitor, verified, acceptance, AckGaps
from lab.live.performance import next_slot, realtime_assessment
from lab.live.workload_traffic import Counters


class CapacityEvidenceTests(unittest.TestCase):
    def test_functional_pass_cannot_hide_realtime_failure(self):
        result = acceptance([], {'passed':False})
        self.assertTrue(result['functional_passed'])
        self.assertFalse(result['overall_passed'])
        with patch('lab.live.capacity.monitor', return_value=result):
            args = ['--run-id','owned','--streams','24','--output','unused']
            self.assertEqual(main(args),1)
            self.assertEqual(main(args+['--functional-only']),0)
        self.assertTrue(acceptance(['delivery failed'], {'passed':True})['realtime_passed'])

    def test_due_slots_include_skips_and_snapshot_overruns(self):
        self.assertEqual(next_slot(0, 13, 10), (2,1))
        self.assertEqual(next_slot(2, 41, 10), (5,2))
        records = [{'slot':i, 'scheduled_to_forwarding_seconds':8,
                    'scheduled_to_completion_seconds':9, 'forwarding_verified':True} for i in range(5)]
        schedule = {'base_epoch':100,'step_seconds':10}
        result = realtime_assessment(records,schedule,100,150)
        self.assertTrue(result['passed'])
        records.pop(1)
        records[0]['scheduled_to_completion_seconds'] = 11  # snapshot overran
        result = realtime_assessment(records,schedule,100,150)
        self.assertEqual(result['expected_slots'],5)
        self.assertEqual(result['skipped_or_unfinished_slots'],1)
        self.assertEqual(result['on_time_slots'],3)
        self.assertFalse(result['passed'])
        self.assertFalse(realtime_assessment(records,{},100,150)['available'])
        self.assertEqual(realtime_assessment(records,schedule,105,149)['expected_slots'],3)

    def test_receive_events_survive_slow_publication_without_false_poll_gap(self):
        clock = [100.0]
        with patch('lab.live.workload_traffic.time.monotonic', side_effect=lambda:clock[0]), \
             patch('lab.live.workload_traffic.time.time', side_effect=lambda:clock[0]+1000):
            counter = Counters()
            counter.sent(1,256)
            clock[0] += .01
            counter.acknowledged(1)
            baseline = counter.snapshot()
            tracker = AckGaps(baseline,1100.01)
            for sequence in range(2,252):
                clock[0] += .02
                counter.sent(sequence,256)
                counter.acknowledged(sequence)
            sample = counter.snapshot()
            self.assertLess(tracker.update(sample,1105.1),.1)
            self.assertLess(tracker.update(sample,1108.1),.1)  # same 5s publication
            # A 1.2s gap between monitor polls is retained after traffic resumes.
            clock[0] += 1.2
            counter.sent(252,256)
            counter.acknowledged(252)
            counter.acknowledged(252)  # duplicate cannot create another event
            counter.acknowledged(999)  # unknown/expired ACK cannot reset liveness
            clock[0] += .02
            counter.sent(253,256)
            counter.acknowledged(253)
            sample = counter.snapshot()
            self.assertAlmostEqual(tracker.update(sample,1106.3),1.2)
            self.assertEqual(sample['ack_gap_sequence'],1)
            with self.assertRaisesRegex(ValueError,'restarted'):
                tracker.update(dict(sample,counter_id='restarted'),1106.3)
            with self.assertRaisesRegex(ValueError,'stale'):
                tracker.update(sample,1200)

    def test_monitor_dates_measurement_after_collecting_newly_published_counters(self):
        clock = [100.0]
        metrics = {'counter_id':'worker', 'ack_snapshot_at':100.002,
                   'ack_snapshot_mono':10.002, 'ack_gap_sequence':0,
                   'ack_gap_events':[], 'last_ack_mono':10.001,
                   'goodput_bps':1000, 'tx_packets':1, 'acked_packets':1, 'acked_bytes':100}
        flow = {'id':'management-03', 'fresh':True, 'evidence_fresh':True,
                'metrics':metrics, 'rate_scale':.25, 'rate_bps':4000,
                'evidence_at':99, 'evidence':{'forward':1,'reverse':1},
                'directions':{'forward':{'status':'ready'},'reverse':{'status':'ready'}}}
        frame = {'index':100001, 'model_at':'1970-01-01T00:01:30+00:00',
                 'application_started_at':'1970-01-01T00:01:31+00:00', 'apply_seconds':1}
        def collect(base, folder):
            clock[0] = 100.02  # Publication happened during file collection.
            return {'flows':[flow], 'summary':{'gateways':24}}
        def read_file(path):
            return {'run_id':'owned', 'records':[frame]} if path.name == 'snapshot.json' else {}
        with tempfile.TemporaryDirectory() as directory, \
             patch('lab.live.capacity.ROOT', Path(directory)), \
             patch('lab.live.capacity.Service') as service, \
             patch('lab.live.capacity.source_manifest', return_value={'source_sha256':{}}), \
             patch('lab.live.capacity.read', side_effect=read_file), \
             patch('lab.live.capacity.observe', side_effect=collect), \
             patch('lab.live.capacity.time.time', side_effect=lambda:clock[0]), \
             contextlib.redirect_stdout(io.StringIO()):
            service.return_value.status.return_value = {'run_id':'owned','phase':'running','active':True}
            report = monitor('owned',1,0,60,60,Path(directory)/'output')
            self.assertEqual(report['measurement_started_at'],100.02)
            self.assertFalse(any('ACK telemetry' in failure for failure in report['failures']))
            observation = json.loads((Path(directory)/'output/observations.jsonl').read_text())
            self.assertGreaterEqual(observation['at'], metrics['ack_snapshot_at'])

    def test_ack_gaps_clip_to_measurement_and_accept_repeated_publications(self):
        baseline = {'counter_id':'worker','ack_gap_sequence':0}
        metrics = {**baseline, 'ack_snapshot_at':1110, 'ack_snapshot_mono':110,
                   'ack_gap_sequence':1, 'last_ack_mono':105,
                   'ack_gap_events':[{'id':1,'start_mono':90,'end_mono':105}]}
        tracker = AckGaps(baseline,1100)
        self.assertEqual(tracker.update(metrics,1110),5)
        self.assertEqual(tracker.update(metrics,1113),5)
        self.assertEqual(tracker.update(metrics,1130),5)  # Freshness boundary.
        for now in (1109.999,1130.001):
            with self.assertRaisesRegex(ValueError,'stale'):
                tracker.update(metrics,now)
        no_ack = {**baseline, 'ack_snapshot_at':1110, 'ack_snapshot_mono':110,
                  'first_sent_mono':90, 'last_ack_mono':None}
        self.assertEqual(AckGaps(baseline,1100).update(no_ack,1110),10)

    def test_ack_gap_history_requires_every_event_in_order(self):
        baseline = {'counter_id':'worker','ack_gap_sequence':0}
        for ids in ([1,3], [1,2,2,3], [1,3,2,3], [1,2]):
            metrics = {**baseline, 'ack_snapshot_at':1110, 'ack_snapshot_mono':110,
                       'ack_gap_sequence':3, 'last_ack_mono':110,
                       'ack_gap_events':[{'id':i,'start_mono':100+i,'end_mono':101+i} for i in ids]}
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError,'events missing|out of order'):
                AckGaps(baseline,1100).update(metrics,1110)

    def test_ack_gap_buffer_overflow_fails_instead_of_underreporting(self):
        clock = [100.0]
        with patch('lab.live.workload_traffic.time.monotonic', side_effect=lambda:clock[0]), \
             patch('lab.live.workload_traffic.time.time', side_effect=lambda:clock[0]+1000):
            counter = Counters()
            counter.sent(1,256)
            counter.acknowledged(1)
            tracker = AckGaps(counter.snapshot(),1100)
            for sequence in range(2,259):
                clock[0] += .2
                counter.sent(sequence,256)
                counter.acknowledged(sequence)
            sample = counter.snapshot()
            self.assertEqual(len(sample['ack_gap_events']),256)
            self.assertEqual(sample['ack_gap_sequence'],257)
            with self.assertRaisesRegex(ValueError,'overflowed'):
                tracker.update(sample,clock[0]+1000)

    def test_cli_propagates_failed_measurement(self):
        with patch('lab.live.capacity.monitor', return_value={'result':'failed'}):
            self.assertEqual(main(['--run-id','owned','--streams','24','--output','unused']),1)

    def test_startup_capture_does_not_certify_a_later_transition(self):
        flow = {'fresh':True, 'evidence_fresh':True, 'metrics':{'goodput_bps':1000},
                'evidence_at':100, 'evidence':{'forward':2,'reverse':2},
                'directions':{'forward':{'status':'ready'},'reverse':{'status':'ready'}}}
        self.assertTrue(verified(flow))
        self.assertFalse(verified(flow, since=101))
        for change in ({'fresh':False}, {'evidence_fresh':False},
                       {'metrics':{'goodput_bps':0}}, {'evidence':{'forward':2,'reverse':0}},
                       {'directions':{'forward':{'status':'ready'},'reverse':{'status':'pending'}}}):
            changed = copy.deepcopy(flow)
            changed.update(change)
            self.assertFalse(verified(changed))


if __name__ == '__main__':
    unittest.main()
