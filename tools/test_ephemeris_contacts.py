import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.ephemeris_contacts import ground_teme, read_oem


class EphemerisContactsTest(unittest.TestCase):
    def test_greenwich_station_is_rotated_into_teme_at_j2000(self):
        at = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
        position, velocity, up = ground_teme(
            {"latitude_deg": 0, "longitude_deg": 0, "altitude_m": 0}, at
        )
        angle = math.degrees(math.atan2(position[1], position[0])) % 360
        self.assertAlmostEqual(angle, 280.46061837, places=6)
        self.assertAlmostEqual(math.hypot(position[0], position[1]), 6378.137, places=6)
        self.assertGreater(math.hypot(velocity[0], velocity[1]), 0.46)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in up)), 1.0, places=12)

    def test_oem_frame_mismatch_is_rejected(self):
        content = """CCSDS_OEM_VERS = 2.0
META_START
CENTER_NAME = EARTH
REF_FRAME = EME2000
TIME_SYSTEM = UTC
META_STOP
2026-01-01T00:00:00Z 7000 0 0 0 7 0
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.oem"
            path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "REF_FRAME = TEME"):
                read_oem(path)


if __name__ == "__main__":
    unittest.main()
