import copy
import json
import math
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tools.ephemeris_contacts import Sample, ground_teme, intersat_visible, norm
from tools.physical_constellation import ROOT, Contacts, Orbits, capacity, compile_scenario, load_config


def seed(count=120, planes=12, gateways=4):
    nodes = [{'id': f'sat-{i+1:04d}', 'kind': 'satellite', 'enabled': True,
              'labels': {'logical_plane': str(i//(count//planes)+1), 'logical_slot': str(i%(count//planes)+1)}} for i in range(count)]
    nodes += [{'id': f'gw-{i+1:03d}', 'kind': 'gateway', 'enabled': True} for i in range(gateways)]
    return {'scenario_id': 'synthetic-test', 'random_seed': 1, 'topology': {'nodes': nodes, 'links': []}, 'intents': [], 'timeline': []}


def config(scenario):
    nodes = scenario['topology']['nodes']
    return load_config(ROOT/'scenarios/constellations/physical-defaults.json',
        [n['id'] for n in nodes if n['kind']=='satellite'], [n['id'] for n in nodes if n['kind']=='gateway'])


class PhysicalConstellationTests(unittest.TestCase):
    def test_every_360_satellite_has_distinct_propagated_states_and_valid_contacts(self):
        source=seed(360,18,8); cfg=config(source); cfg['duration_seconds']=20
        result=compile_scenario(source,cfg); model=result['physical_model']
        self.assertEqual(len(model['satellite_orbits']),360)
        self.assertEqual(len({v['raan_deg'] for v in model['satellite_orbits'].values()}),18)
        self.assertEqual(len({tuple(v['position_km']) for v in model['frames'][0]['states'].values()}),360)
        for frame in model['frames']:
            counts=Counter()
            for link in frame['links']:
                counts[link['source'],link['link_type']]+=1
                self.assertGreater(link['latency_us'],cfg['links']['processing_delay_us'])
                self.assertGreater(link['capacity_bps'],0)
                if link['link_type']=='oisl':
                    a=frame['states'][link['source']]['position_km']; b=frame['states'][link['target']]['position_km']
                    self.assertTrue(intersat_visible(a,b,cfg['links']['earth_clearance_km']))
                    self.assertLessEqual(norm(tuple(x-y for x,y in zip(a,b))),cfg['links']['max_isl_range_km'])
            self.assertTrue(all(n<=4 for (_,kind),n in counts.items() if kind=='oisl'))
            self.assertTrue(all(n<=3 for (name,kind),n in counts.items() if name.startswith('gw-')))
            self.assertTrue(all(n<=2 for (name,kind),n in counts.items() if name.startswith('sat-') and kind=='feeder'))
        self.assertEqual(source['topology']['links'],[])
        self.assertNotEqual(model['frames'][0]['states'],model['frames'][-1]['states'])

    def test_earth_occultation_and_ground_position_prevent_links(self):
        source=seed(4,1,2);cfg=config(source)
        cfg['links']['max_isl_range_km']=100000
        cfg['links']['max_feeder_range_km']=100000
        cfg['ground_stations']=[{'id':'gw-001','latitude_deg':0,'longitude_deg':0,'altitude_m':0}]
        at=datetime(2026,9,15,tzinfo=timezone.utc)
        ground,_,_=ground_teme(cfg['ground_stations'][0],at)
        p=tuple(v*1.2 for v in ground)
        states={'sat-0001':Sample(at,p,(0,0,0)), 'sat-0002':Sample(at,tuple(-v for v in p),(0,0,0))}
        frame=Contacts(source['topology']['nodes'][:4],cfg).frame(states,0)
        pairs={(l['source'],l['target']) for l in frame['links']}
        self.assertIn(('sat-0001','gw-001'),pairs)
        self.assertNotIn(('sat-0002','gw-001'),pairs)
        self.assertNotIn(('sat-0001','sat-0002'),pairs)

    def test_capture_requires_elapsed_time_and_restarts_after_occlusion(self):
        source=seed(4,1,2);cfg=config(source);cfg['ground_stations']=[]
        cfg['links']['acquisition_seconds']=2
        at=datetime(2026,9,15,tzinfo=timezone.utc)
        near={'sat-0001':Sample(at,(7500,0,0),(0,7,0)), 'sat-0002':Sample(at,(7500,100,0),(0,7,0))}
        scheduler=Contacts(source['topology']['nodes'][:4],cfg)
        self.assertFalse(any(l['operational_up'] for l in scheduler.frame(near,0)['links']))
        self.assertTrue(all(l['operational_up'] for l in scheduler.frame(near,2)['links']))
        far=dict(near);far['sat-0002']=Sample(at,(-7500,0,0),(0,-7,0))
        self.assertEqual(scheduler.frame(far,3)['links'],[])
        self.assertFalse(any(l['operational_up'] for l in scheduler.frame(near,4)['links']))

    def test_reference_budget_and_range_limit_capacity(self):
        budget={'snr_at_reference_db':20,'reference_range_km':1000,'efficiency':0.7,'bandwidth_hz':1000000,'max_capacity_bps':100000000}
        self.assertEqual(capacity(1000,budget),int(.7*1000000*math.log2(101)))
        self.assertLess(capacity(2000,budget),capacity(1000,budget))
        budget['max_capacity_bps']=2000000
        self.assertEqual(capacity(1000,budget),2000000)

    def test_oem_uses_velocity_interpolation_and_refuses_extrapolation(self):
        source=seed(4,1,2);cfg=config(source)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'sat.oem'
            p.write_text('CCSDS_OEM_VERS = 2.0\nMETA_START\nCENTER_NAME = EARTH\nREF_FRAME = TEME\nTIME_SYSTEM = UTC\nMETA_STOP\n'
                '2026-09-14T23:59:50Z 7500 -75 0 0 7.5 0\n2026-09-15T00:00:00Z 7500 0 0 0 7.5 0\n2026-09-15T00:00:10Z 7500 75 0 0 7.5 0\n')
            cfg['orbit']={'source':'oem','records':[{'id':'sat-0001','path':str(p)}]}
            orbits=Orbits(source['topology']['nodes'][:1],cfg)
            state=orbits.states(5)['sat-0001']
            self.assertEqual(state.position,(7500,37.5,0))
            self.assertEqual(state.velocity,(0,7.5,0))
            with self.assertRaisesRegex(ValueError,'does not cover'):orbits.states(20)

    def test_tle_catalog_identity_is_preserved(self):
        source=seed(4,1,2);cfg=config(source)
        record=json.loads((ROOT/'lab/orbit-closed-loop/inputs/tle-catalog.json').read_text())[0]
        record=dict(record,id='sat-0001')
        cfg['orbit']={'source':'tle','records':[record]}
        orbits=Orbits(source['topology']['nodes'][:1],cfg)
        self.assertEqual(orbits.elements['sat-0001']['line1'],record['line1'])
        self.assertGreater(norm(orbits.states(0)['sat-0001'].position),6378)

    def test_orbit_motion_produces_contact_transitions_and_varied_delay(self):
        result=compile_scenario(seed(),config(seed()));frames=result['physical_model']['frames']
        initial={l['id'] for l in frames[0]['links'] if l['operational_up']}
        self.assertTrue(any({l['id'] for l in f['links'] if l['operational_up']}!=initial for f in frames[1:]))
        self.assertGreater(len({l['latency_us'] for f in frames for l in f['links']}),100)
        self.assertTrue(all(len(v['components'])==1 for v in result['physical_model']['connectivity']))

    def test_missing_gateway_coordinates_and_nonfinite_parameters_rejected(self):
        raw=json.loads((ROOT/'scenarios/constellations/physical-defaults.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'config.json';p.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'missing geographic'):
                load_config(p,['sat-0001'],['gw-999'])
            raw['links']['isl']['bandwidth_hz']=float('nan');p.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'finite'):
                load_config(p,['sat-0001'],['gw-001'])


if __name__=='__main__':unittest.main()
