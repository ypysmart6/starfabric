#include <core.p4>
#include <v1model.p4>

const bit<16> TYPE_IPV4 = 0x0800;
const bit<16> TYPE_IPV6 = 0x86dd;

header ethernet_t { bit<48> dst_addr; bit<48> src_addr; bit<16> ether_type; }
header ipv4_t {
    bit<4> version; bit<4> ihl; bit<8> diffserv; bit<16> total_len;
    bit<16> identification; bit<3> flags; bit<13> frag_offset; bit<8> ttl;
    bit<8> protocol; bit<16> checksum; bit<32> src_addr; bit<32> dst_addr;
}
header ipv6_t {
    bit<4> version; bit<8> traffic_class; bit<20> flow_label; bit<16> payload_len;
    bit<8> next_header; bit<8> hop_limit; bit<128> src_addr; bit<128> dst_addr;
}
struct headers_t { ethernet_t ethernet; ipv4_t ipv4; ipv6_t ipv6; }
struct metadata_t { bit<3> traffic_class; bit<2> meter_color; }

parser ParserImpl(packet_in packet, out headers_t hdr, inout metadata_t meta,
                  inout standard_metadata_t standard_metadata) {
    state start { packet.extract(hdr.ethernet); transition select(hdr.ethernet.ether_type) { TYPE_IPV4: parse_ipv4; TYPE_IPV6: parse_ipv6; default: accept; } }
    state parse_ipv4 { packet.extract(hdr.ipv4); transition accept; }
    state parse_ipv6 { packet.extract(hdr.ipv6); transition accept; }
}

control VerifyChecksumImpl(inout headers_t hdr, inout metadata_t meta) {
    apply { verify_checksum(hdr.ipv4.isValid(), {hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
        hdr.ipv4.total_len, hdr.ipv4.identification, hdr.ipv4.flags, hdr.ipv4.frag_offset,
        hdr.ipv4.ttl, hdr.ipv4.protocol, hdr.ipv4.src_addr, hdr.ipv4.dst_addr}, hdr.ipv4.checksum, HashAlgorithm.csum16); }
}

control IngressImpl(inout headers_t hdr, inout metadata_t meta, inout standard_metadata_t standard_metadata) {
    meter(1024, MeterType.packets) service_meter;
    direct_counter(CounterType.packets_and_bytes) ipv4_route_counter;
    direct_counter(CounterType.packets_and_bytes) ipv6_route_counter;

    action drop() { mark_to_drop(standard_metadata); }
    action set_nexthop(bit<48> dst_mac, bit<9> port, bit<32> meter_index) {
        service_meter.execute_meter(meter_index, meta.meter_color);
        if (meta.meter_color == V1MODEL_METER_COLOR_RED) {
            mark_to_drop(standard_metadata);
        } else {
            hdr.ethernet.src_addr = hdr.ethernet.dst_addr;
            hdr.ethernet.dst_addr = dst_mac;
            standard_metadata.egress_spec = port;
            if (hdr.ipv4.isValid()) { hdr.ipv4.ttl = hdr.ipv4.ttl - 1; }
            if (hdr.ipv6.isValid()) { hdr.ipv6.hop_limit = hdr.ipv6.hop_limit - 1; }
        }
    }
    action classify(bit<3> traffic_class) { meta.traffic_class = traffic_class; }
    table qos_classification {
        key = { hdr.ipv4.diffserv: exact; }
        actions = { classify; NoAction; }
        size = 64;
        default_action = NoAction();
    }
    table ipv6_qos_classification {
        key = { hdr.ipv6.traffic_class: exact; }
        actions = { classify; NoAction; }
        size = 64;
        default_action = NoAction();
    }
    table ipv4_lpm {
        key = { hdr.ipv4.dst_addr: lpm; meta.traffic_class: ternary; }
        actions = { set_nexthop; drop; }
        size = 4096;
        counters = ipv4_route_counter;
        default_action = drop();
    }
    table ipv6_lpm {
        key = { hdr.ipv6.dst_addr: lpm; meta.traffic_class: ternary; }
        actions = { set_nexthop; drop; }
        size = 4096;
        counters = ipv6_route_counter;
        default_action = drop();
    }
    apply {
        if (hdr.ipv4.isValid()) {
            if (hdr.ipv4.ttl <= 1) { drop(); }
            else { qos_classification.apply(); ipv4_lpm.apply(); }
        }
        else if (hdr.ipv6.isValid()) {
            if (hdr.ipv6.hop_limit <= 1) { drop(); }
            else { ipv6_qos_classification.apply(); ipv6_lpm.apply(); }
        }
        else { drop(); }
    }
}

control EgressImpl(inout headers_t hdr, inout metadata_t meta, inout standard_metadata_t standard_metadata) { apply { } }
control ComputeChecksumImpl(inout headers_t hdr, inout metadata_t meta) {
    apply { update_checksum(hdr.ipv4.isValid(), {hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv,
        hdr.ipv4.total_len, hdr.ipv4.identification, hdr.ipv4.flags, hdr.ipv4.frag_offset,
        hdr.ipv4.ttl, hdr.ipv4.protocol, hdr.ipv4.src_addr, hdr.ipv4.dst_addr}, hdr.ipv4.checksum, HashAlgorithm.csum16); }
}
control DeparserImpl(packet_out packet, in headers_t hdr) { apply { packet.emit(hdr.ethernet); packet.emit(hdr.ipv4); packet.emit(hdr.ipv6); } }

V1Switch(ParserImpl(), VerifyChecksumImpl(), IngressImpl(), EgressImpl(), ComputeChecksumImpl(), DeparserImpl()) main;
