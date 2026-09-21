"""Restricted iBGP EVPN Type-2/3/5 / RFC 9252 codec for the SIL gateways.

IPv4 host prefixes, zero ESI/tag/gateway, full SID (no transposition), one RT.
This is deliberately not a general-purpose BGP implementation. Unsupported
or malformed service encodings are rejected, never interpreted as VXLAN.
"""
import ipaddress
import struct

AF = b'\x00\x19\x46'  # AFI L2VPN=25, SAFI EVPN=70
RT = struct.pack('!BBHI', 0, 2, 65000, 100)
DX4 = 0x0011
DX2 = 0x0015


def tlv(kind, value):
    return struct.pack('!BH', kind, len(value)) + value


def tlvs(data):
    while data:
        if len(data) < 3: raise ValueError('truncated TLV header')
        kind, size = struct.unpack('!BH', data[:3])
        if len(data) < size+3: raise ValueError('truncated TLV value')
        yield kind, data[3:size+3]
        data = data[size+3:]


def message(kind, data=b''):
    if len(data) > 4077: raise ValueError('BGP message exceeds 4096 octets')
    return b'\xff'*16 + struct.pack('!HB', 19+len(data), kind) + data


def open_message(router_id):
    capability = b'\x01\x04\x00\x19\x00\x46'
    optional = bytes([2, len(capability)]) + capability
    return message(1, struct.pack('!BHH4sB', 4, 65000, 30, ipaddress.IPv4Address(router_id).packed, len(optional)) + optional)


def read_open(data):
    if len(data) < 10: raise ValueError('truncated OPEN')
    version, asn, hold, rid, size = struct.unpack('!BHH4sB', data[:10])
    if version != 4 or asn != 65000 or hold < 3 or len(data) != 10+size:
        raise ValueError('unsupported BGP version/AS/hold/options')
    options, evpn = data[10:], False
    while options:
        if len(options) < 2 or len(options) < 2+options[1]: raise ValueError('truncated OPEN option')
        kind, size = options[:2]; value = options[2:2+size]; options = options[2+size:]
        if kind != 2: raise ValueError('unsupported OPEN option')
        while value:
            if len(value) < 2 or len(value) < 2+value[1]: raise ValueError('truncated capability')
            code, size = value[:2]
            evpn |= code == 1 and value[2:2+size] == b'\x00\x19\x00\x46'
            value = value[2+size:]
    if not evpn: raise ValueError('peer did not negotiate EVPN')
    return min(30, hold)


def nlri(router_id, prefix):
    net = ipaddress.IPv4Network(prefix)
    rd = struct.pack('!H4sH', 1, ipaddress.IPv4Address(router_id).packed, 100)
    value = rd + bytes(14) + bytes([net.prefixlen]) + net.network_address.packed + bytes(4) + b'\x00\x00\x30'
    return bytes([5, len(value)]) + value


def prefixes(data):
    result = []
    while data:
        if len(data) < 2 or len(data) < 2+data[1]: raise ValueError('truncated EVPN NLRI')
        kind, size = data[:2]; v = data[2:2+size]; data = data[2+size:]
        if kind != 5: continue
        if size != 34 or v[8:22] != bytes(14) or v[22] != 32 or v[27:31] != bytes(4) or v[31:] != b'\x00\x00\x30':
            raise ValueError('unsupported Type-5 encoding (only IPv4 /32, full SID)')
        result.append(str(ipaddress.IPv4Address(v[23:27]))+'/32')
    return result


def l2_nlri(router_id, evi, mac=None, originator=None):
    rd = struct.pack('!H4sH', 1, ipaddress.IPv4Address(router_id).packed, evi)
    if mac is not None:
        value = rd + bytes(14) + b'\x30' + bytes.fromhex(mac.replace(':','')) + b'\x00\x00\x00\x30'
        kind = 2
    else:
        value = rd + bytes(4) + b'\x80' + ipaddress.IPv6Address(originator).packed
        kind = 3
    return bytes([kind, len(value)]) + value


def records(data):
    result = []
    while data:
        if len(data) < 2 or len(data) < 2+data[1]: raise ValueError('truncated EVPN NLRI')
        kind, size = data[:2]; raw = data[:2+size]; v = data[2:2+size]; data = data[2+size:]
        if kind == 5:
            result.extend({'key':p, 'prefix':p, 'route_type':5, 'evi':100} for p in prefixes(raw))
        elif kind in (2,3):
            if len(v) < 8 or v[:2] != b'\x00\x01': raise ValueError('unsupported L2 RD')
            evi = int.from_bytes(v[6:8], 'big')
            route = {'route_type':kind, 'evi':evi}
            if kind == 2:
                if size != 33 or v[8:22] != bytes(14) or v[22] != 48 or v[29:] != b'\x00\x00\x00\x30':
                    raise ValueError('unsupported MAC-only Type-2')
                route['mac'] = ':'.join(f'{x:02x}' for x in v[23:29])
                route['key'] = f"mac:{evi}:{route['mac']}"
            else:
                if size != 29 or v[8:13] != bytes(4)+b'\x80': raise ValueError('unsupported Type-3')
                route['originator'] = str(ipaddress.IPv6Address(v[13:29]))
                route['key'] = f"imet:{evi}:{route['originator']}"
            result.append(route)
    return result


def l2_update(router_id, evi, nexthop, sid, mac=None, sequence=0, withdraw=False):
    route = l2_nlri(router_id, evi, mac, nexthop)
    if withdraw:
        attributes = attr(15, AF+route)
    else:
        info = b'\x00'+ipaddress.IPv6Address(sid).packed+struct.pack('!BHB',0,DX2,0)
        communities = struct.pack('!BBHI',0,2,65000,evi)
        if mac: communities += b'\x06\x00\x00\x00'+struct.pack('!I',sequence)
        attributes = (attr(1,b'\x00',0x40)+attr(2,b'',0x40)+attr(5,struct.pack('!I',100),0x40)
            +attr(16,communities,0xc0)+attr(40,tlv(6,b'\x00'+tlv(1,info)),0xc0)
            +attr(14,AF+b'\x10'+ipaddress.IPv6Address(nexthop).packed+b'\x00'+route))
        if mac is None:
            attributes += attr(22,b'\x00\x06\x00\x00\x30'+ipaddress.IPv6Address(nexthop).packed,0xc0)
    return message(2,b'\x00\x00'+struct.pack('!H',len(attributes))+attributes)


def attr(kind, value, flags=0x80):
    return struct.pack('!BBH', flags | 0x10, kind, len(value)) + value


def update(router_id, prefix, nexthop, sid, withdraw=False):
    route = nlri(router_id, prefix)
    if withdraw:
        attributes = attr(15, AF + route)
    else:
        info = b'\x00' + ipaddress.IPv6Address(sid).packed + struct.pack('!BHB', 0, DX4, 0)
        attributes = (attr(1, b'\x00', 0x40) + attr(2, b'', 0x40) + attr(5, struct.pack('!I', 100), 0x40)
                      + attr(16, RT, 0xc0) + attr(40, tlv(5, b'\x00'+tlv(1, info)), 0xc0)
                      + attr(14, AF + b'\x10' + ipaddress.IPv6Address(nexthop).packed + b'\x00' + route))
    return message(2, b'\x00\x00' + struct.pack('!H', len(attributes)) + attributes)


def read_update(data):
    if len(data) < 4 or data[:2] != b'\x00\x00': raise ValueError('invalid EVPN UPDATE')
    size = int.from_bytes(data[2:4], 'big')
    if len(data) != size+4: raise ValueError('invalid path attributes length')
    data, attributes = data[4:], {}
    while data:
        if len(data) < 3: raise ValueError('truncated attribute')
        flags, kind = data[:2]; n = 4 if flags & 0x10 else 3
        if len(data) < n: raise ValueError('truncated attribute length')
        length = int.from_bytes(data[2:n], 'big')
        if len(data) < n+length or kind in attributes: raise ValueError('invalid or duplicate attribute')
        attributes[kind] = data[n:n+length]; data = data[n+length:]
    withdrawn = []
    if 15 in attributes:
        if attributes[15][:3] != AF: raise ValueError('wrong MP_UNREACH family')
        withdrawn = [r['key'] for r in records(attributes[15][3:])]
    if 14 not in attributes: return [], withdrawn
    reach = attributes[14]
    if len(reach) < 21 or reach[:4] != AF+b'\x10': raise ValueError('EVPN SRv6 needs an IPv6 next hop')
    announced = records(reach[21:])
    if not announced: return [], withdrawn
    # Treat incomplete service attributes as withdrawals (RFC 9252 §7).
    try:
        if attributes.get(1) != b'\x00' or attributes.get(2) != b'' or attributes.get(5) != struct.pack('!I',100):
            raise ValueError('not an internal originated route')
        communities = attributes.get(16,b'')
        if len(communities)%8: raise ValueError('invalid extended community')
        communities = [communities[i:i+8] for i in range(0,len(communities),8)]
        evi = announced[0]['evi']
        if any(r['evi'] != evi or r['route_type'] != announced[0]['route_type'] for r in announced):
            raise ValueError('mixed service UPDATE unsupported')
        if struct.pack('!BBHI',0,2,65000,evi) not in communities: raise ValueError('route target mismatch')
        l2 = announced[0]['route_type'] in (2,3)
        services = list(tlvs(attributes.get(40,b'')))
        service = next(v for t,v in services if t == (6 if l2 else 5))
        if not service: raise ValueError('missing service reserved byte')
        information = list(tlvs(service[1:]))
        info = next(v for t,v in information if t == 1)
        if len(info) != 21 or int.from_bytes(info[18:20], 'big') != (DX2 if l2 else DX4):
            raise ValueError('unsupported SID structure or behavior')
        sid = ipaddress.IPv6Address(info[1:17])
        if sid.is_multicast or sid.is_unspecified: raise ValueError('invalid service SID')
    except (ValueError, StopIteration):
        return [], withdrawn+[r['key'] for r in announced]
    nh = str(ipaddress.IPv6Address(reach[4:20]))
    if announced[0]['route_type'] == 3 and (attributes.get(22) != b'\x00\x06\x00\x00\x30'+ipaddress.IPv6Address(nh).packed
                                           or any(r['originator'] != nh for r in announced)):
        return [], withdrawn+[r['key'] for r in announced]
    sequence = max([int.from_bytes(c[4:],'big') for c in communities if c[:4] == b'\x06\x00\x00\x00'] or [0])
    return [{**r, 'sid':str(sid), 'nexthop':nh, 'behavior':'End.DX2' if l2 else 'End.DX4',
             'route_target':f'65000:{evi}', 'sequence':sequence} for r in announced], withdrawn
