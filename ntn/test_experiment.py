import unittest

from ntn import experiment


class ExperimentTest(unittest.TestCase):
    def test_percentiles(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(experiment.percentile(values, 50), 3.0)
        self.assertAlmostEqual(experiment.percentile(values, 95), 4.8)
        self.assertEqual(experiment.percentile([], 99), 0.0)

    def test_manifest_rejects_unsafe_flow(self):
        manifest = {
            "id": "bad",
            "duration_s": 1,
            "sample_interval_ms": 100,
            "flows": [{"name": "flow", "destination": "not-an-address"}],
            "assertions": {"max_loss_percent": 1, "max_outage_ms": 1},
        }
        with self.assertRaises(ValueError):
            experiment.validate(manifest)

    def test_tcp_and_udp_require_ports(self):
        for protocol in ("tcp", "udp"):
            manifest = {
                "id": protocol,
                "duration_s": 1,
                "sample_interval_ms": 100,
                "flows": [{"name": protocol, "destination": "192.0.2.1", "protocol": protocol}],
                "assertions": {"max_loss_percent": 1, "max_outage_ms": 1},
            }
            with self.assertRaises(ValueError):
                experiment.validate(manifest)

    def test_dscp_is_six_bits_and_encoded_as_ip_traffic_class(self):
        self.assertEqual(experiment.dscp_traffic_class(46), 184)
        self.assertEqual(experiment.dscp_traffic_class(26), 104)
        for value in (-1, 64, 255):
            with self.assertRaises(ValueError):
                experiment.dscp_traffic_class(value)

    def test_manifest_rejects_traffic_class_masquerading_as_dscp(self):
        manifest = {
            "id": "bad-dscp",
            "duration_s": 1,
            "sample_interval_ms": 100,
            "flows": [{"name": "flow", "destination": "192.0.2.1", "dscp": 184}],
            "assertions": {"max_loss_percent": 1, "max_outage_ms": 1},
        }
        with self.assertRaises(ValueError):
            experiment.validate(manifest)

    def test_manifest_rejects_ambiguous_execution_scope(self):
        manifest = {
            "id": "ambiguous-scope",
            "duration_s": 1,
            "sample_interval_ms": 100,
            "ue_namespace": "ue1",
            "ue_container": "ue1",
            "flows": [{"name": "flow", "destination": "192.0.2.1"}],
            "assertions": {"max_loss_percent": 1, "max_outage_ms": 1},
        }
        with self.assertRaises(ValueError):
            experiment.validate(manifest)

    def test_scoped_command_supports_namespace_or_container(self):
        self.assertEqual(
            experiment.scoped_command("ue1", "", ["ping", "192.0.2.1"]),
            ["ip", "netns", "exec", "ue1", "ping", "192.0.2.1"],
        )
        self.assertEqual(
            experiment.scoped_command("", "nr_ue", ["ping", "192.0.2.1"]),
            ["docker", "exec", "nr_ue", "ping", "192.0.2.1"],
        )

    def test_manifest_rejects_unsafe_source_interface(self):
        manifest = {
            "id": "unsafe-interface",
            "duration_s": 1,
            "sample_interval_ms": 100,
            "flows": [{"name": "flow", "destination": "192.0.2.1", "source_interface": "tun;bad"}],
            "assertions": {"max_loss_percent": 1, "max_outage_ms": 1},
        }
        with self.assertRaises(ValueError):
            experiment.validate(manifest)


if __name__ == "__main__":
    unittest.main()
