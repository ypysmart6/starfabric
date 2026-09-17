// Minimal CO-RE friendly XDP packet counter. The program never redirects or
// drops traffic, so it can validate visibility without changing forwarding.
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
} packets SEC(".maps");

SEC("xdp")
int starfabric_count(struct xdp_md *ctx)
{
    __u32 key = 0;
    __u64 *count = bpf_map_lookup_elem(&packets, &key);

    if (count)
        __sync_fetch_and_add(count, 1);
    return XDP_PASS;
}

char LICENSE[] SEC("license") = "GPL";
