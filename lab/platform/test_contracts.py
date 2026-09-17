"""Execution boundary and on-wire PCEP checks; live gates prove forwarding."""
import struct
import ipaddress
import unittest
import tempfile
from pathlib import Path

from frr_agent import validate
from pce import ero, objects, pcc_sid_depth
from packets import gtpu_packets, proof


class Contracts(unittest.TestCase):
    def test_negotiated_pcc_depth(self):
        opening = bytes.fromhex('01100024201e78000010000400000001002200100000000101000000001a000400000004')
        self.assertEqual(pcc_sid_depth(opening), 4)
        self.assertEqual(pcc_sid_depth(opening[:-1]+bytes([32])), 32)
        self.assertEqual(pcc_sid_depth(opening[:-2]+bytes([1,0])), 32)
        with self.assertRaises(ValueError): pcc_sid_depth(opening[:-1]+b'\x00')

    def test_packet_outside_phase_cannot_prove_recovery(self):
        gtp = bytes.fromhex('30ff000401020304') + b'data'
        udp = struct.pack('!HHHH',2152,2152,8+len(gtp),0)+gtp
        ip = bytearray(20);ip[0]=0x45;ip[9]=17
        ip[12:16]=ipaddress.ip_address('172.22.0.23').packed
        ip[16:20]=ipaddress.ip_address('172.22.0.8').packed
        packet = bytes(12)+bytes.fromhex('0800')+ip+udp
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'phase.pcap'
            path.write_bytes(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1)+struct.pack('<IIII',100,250000,len(packet),len(packet))+packet)
            args=(path,'native','172.22.0.23','172.22.0.8',False)
            self.assertEqual(proof(*args,window=(100.2,100.3))['uplink'],1)
            self.assertEqual(proof(*args,window=(101,102))['uplink'],0)

    def test_gtpu_must_be_inside_actual_mpls_packet(self):
        gtp = bytes.fromhex("30ff000401020304") + b"data"
        udp = struct.pack("!HHHH", 2152, 2152, 8 + len(gtp), 0) + gtp
        ipv4 = bytearray(20)
        ipv4[0], ipv4[9] = 0x45, 17
        ipv4[12:16] = ipaddress.ip_address("172.22.0.23").packed
        ipv4[16:20] = ipaddress.ip_address("172.22.0.8").packed
        inner = bytes(ipv4) + udp
        raw = struct.pack("!I", (16004 << 12) | 0x140) + inner
        result = gtpu_packets(raw, 0x8847)
        self.assertEqual(result[0]["teid"], 0x01020304)
        self.assertEqual(result[0]["layers"], ["mpls"])
        self.assertEqual(result[0]["labels"], [16004])
        self.assertEqual(gtpu_packets(inner, 0x0800)[0]["layers"], [])
        self.assertEqual(gtpu_packets(raw[:-5], 0x8847), [])

    def test_execution_boundary(self):
        validate("vtysh", ["-c", "configure terminal", "-c", "ip route 172.22.0.8/32 10.231.12.3 10", "-c", "end"])
        validate("ping", ["-n", "-c", "1", "-W", "1", "-6", "2001:db8::1"])
        for program, args in (("sh", ["-c", "id"]), ("vtysh", ["-c", "start-shell"]),
                              ("vtysh", ["-f", "/tmp/config"]), ("vtysh", ["-c", "ip route 0/0 1.1.1.1;sh"]),
                              ("ping", ["-f", "127.0.0.1"]), ("ping", ["-n", "-c", "1", "-W", "1", "example.com"])):
            with self.assertRaises(ValueError):
                validate(program, args)

    def test_sr_ero_wire_format(self):
        raw = ero([16002, 16004])
        cls, typ, obj = next(objects(raw))
        self.assertEqual((cls, typ, len(obj)), (7, 1, 20))
        self.assertEqual(struct.unpack("!BBBBI", obj[4:12]), (36, 8, 0, 9, 16002 << 12))
        self.assertEqual(struct.unpack("!BBBBI", obj[12:20]), (36, 8, 0, 9, 16004 << 12))
        for broken in (b"\x07", b"\x07\x12\x00\x00", b"\x07\x12\x00\x10"):
            with self.assertRaises(ValueError):
                list(objects(broken))


if __name__ == "__main__":
    unittest.main()
