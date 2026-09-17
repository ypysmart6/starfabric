package model

import (
	"net/netip"
	"strconv"
	"testing"
)

func liveNode(index, count int) Node {
	return Node{ID: strconv.Itoa(index), Loopback: "10.255.0.1", Labels: map[string]string{
		"next_hop_scheme": "live-pair-v1", "next_hop_index": strconv.Itoa(index), "next_hop_nodes": strconv.Itoa(count),
	}}
}

func TestLivePairAddressesMatchSequentialWiringPool(t *testing.T) {
	for _, count := range []int{2, 124, 144, 255} {
		// The Python wiring pool reserves pair zero; its first /30 is .4/30.
		address := netip.MustParseAddr("10.128.0.4")
		for left := 0; left < count; left++ {
			for right := left + 1; right < count; right++ {
				a, b := liveNode(left, count), liveNode(right, count)
				address = address.Next()
				got, err := b.NextHopAddress(a)
				if err != nil || got != address.String() {
					t.Fatalf("reverse %d/%d: %s %v, want %s", left, right, got, err, address)
				}
				address = address.Next()
				got, err = a.NextHopAddress(b)
				if err != nil || got != address.String() {
					t.Fatalf("forward %d/%d: %s %v, want %s", left, right, got, err, address)
				}
				address = address.Next().Next()
			}
		}
	}
}

func TestLivePairBindingsRejectMismatchesAndHonorExplicitAddresses(t *testing.T) {
	for _, peer := range []Node{liveNode(0, 24), liveNode(24, 24), liveNode(1, 25), {ID: "missing"}} {
		if _, err := liveNode(0, 24).NextHopAddress(peer); err == nil {
			t.Fatalf("accepted invalid peer %+v", peer)
		}
	}
	for _, value := range []string{"", "x", "-1", "256"} {
		n := liveNode(0, 24)
		n.Labels["next_hop_index"] = value
		if _, err := n.NextHopAddress(liveNode(1, 24)); err == nil {
			t.Fatalf("accepted invalid index %q", value)
		}
	}
	n, peer := liveNode(0, 24), liveNode(1, 24)
	n.Labels["next_hop:"+peer.ID] = "192.0.2.2"
	if got, err := n.NextHopAddress(peer); err != nil || got != "192.0.2.2" {
		t.Fatalf("explicit binding: %s %v", got, err)
	}
}
