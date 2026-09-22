import json
from pathlib import Path
import tempfile
import unittest

from lab.live.summarize_performance import summarize


class SummaryWindowTests(unittest.TestCase):
    def test_report_freezes_frames_and_excludes_late_completion(self):
        completed = {'index':100001, 'model_at':'2026-01-01T00:00:05+00:00',
                     'scheduled_at':'2026-01-01T00:00:05+00:00',
                     'scheduled_to_completion_seconds':3, 'apply_seconds':2}
        late = {**completed, 'index':100002, 'model_at':'2026-01-01T00:00:15+00:00',
                'scheduled_at':'2026-01-01T00:00:15+00:00',
                'scheduled_to_completion_seconds':10, 'apply_seconds':9}
        unobserved = {**completed, 'index':100003}
        with tempfile.TemporaryDirectory() as folder:
            artifacts = Path(folder)
            (artifacts/'performance-frames.jsonl').write_text(''.join(
                json.dumps(frame)+'\n' for frame in (completed, late, unobserved)))
            report = {'measurement_started_at':1767225600, 'observed_at':1767225620,
                      'frames':[completed, late]}
            summary = summarize(artifacts, report)
            self.assertEqual(summary['frame_indices'], [100001])
            self.assertEqual(summary['frame_seconds']['apply_seconds']['mean'], 2)
            self.assertEqual(summarize(artifacts)['frame_count'], 3)


if __name__ == '__main__':
    unittest.main()
