package testutil

import (
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

func Diamond() model.TopologySnapshot {
	return model.TopologySnapshot{
		Version: 1, GeneratedAt: time.Now().UTC(), ValidFrom: time.Now().UTC(),
		Nodes: []model.Node{
			{ID: "a", Loopback: "2001:db8::1", Enabled: true},
			{ID: "b", Loopback: "2001:db8::2", Enabled: true},
			{ID: "c", Loopback: "2001:db8::3", Enabled: true},
			{ID: "d", Loopback: "2001:db8::4", Enabled: true},
		},
		Links: []model.Link{
			link("ab", "a", "b", 10), link("ba", "b", "a", 10),
			link("bd", "b", "d", 10), link("db", "d", "b", 10),
			link("ac", "a", "c", 20), link("ca", "c", "a", 20),
			link("cd", "c", "d", 20), link("dc", "d", "c", 20),
		},
	}
}

func Intent() model.RouteIntent {
	return model.RouteIntent{ID: "a-d", Source: "a", Destination: "d", DestinationPrefix: "2001:db8:100::/64", Policy: "latency", Redundancy: 1, MinCapacityBPS: 1_000_000}
}

func link(id, source, target string, latency int64) model.Link {
	return model.Link{ID: id, Source: source, Target: target, AdminUp: true, OperationalUp: true, LatencyUS: latency, CapacityBPS: 1_000_000_000, ReliabilityPPM: 999_900}
}
