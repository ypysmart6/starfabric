import copy
import unittest

from lab.live.capacity import verified


class CapacityEvidenceTests(unittest.TestCase):
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
