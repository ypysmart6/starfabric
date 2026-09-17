package planner_test

import (
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/validation"
)

func TestPhysicalNextHopBindingIsPlannedAndValidated(t *testing.T) {
	now := time.Now().UTC()
	snapshot := model.TopologySnapshot{Version: 1, GeneratedAt: now, ValidFrom: now,
		Nodes: []model.Node{
			{ID: "a", Loopback: "10.255.0.1", Enabled: true, Labels: map[string]string{"next_hop:b": "10.128.0.6"}},
			{ID: "b", Loopback: "10.255.0.2", Enabled: true},
		},
		Links: []model.Link{{ID: "ab", Source: "a", Target: "b", AdminUp: true, OperationalUp: true, LatencyUS: 1000, CapacityBPS: 1000000}},
	}
	intent := model.RouteIntent{ID: "traffic", Source: "a", Destination: "b", DestinationPrefix: "203.0.113.1/32"}
	p := planner.New(planner.Config{PlanTTL: time.Minute})
	plan, err := p.Build(snapshot, []model.RouteIntent{intent}, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Routes) != 1 || plan.Routes[0].NextHop != "10.128.0.6" {
		t.Fatalf("wrong physical next hop: %+v", plan.Routes)
	}
	if err := validation.Plan(plan, snapshot, now); err != nil {
		t.Fatal(err)
	}
	for _, wrong := range []string{"10.255.0.2", "10.128.0.99"} {
		changed := plan.Clone()
		changed.Routes[0].NextHop = wrong
		if validation.Plan(changed, snapshot, now) == nil {
			t.Fatalf("accepted unbound next hop %s", wrong)
		}
	}
	snapshot.Links[0].OperationalUp = false
	if validation.Plan(plan, snapshot, now) == nil {
		t.Fatal("accepted withdrawn physical edge")
	}
	snapshot.Links[0].OperationalUp = true
	for _, bad := range []string{"", "invalid-address"} {
		snapshot.Nodes[0].Labels["next_hop:b"] = bad
		if _, err := p.Build(snapshot, []model.RouteIntent{intent}, now); err == nil {
			t.Fatalf("accepted invalid binding %q", bad)
		}
	}
	delete(snapshot.Nodes[0].Labels, "next_hop:b")
	legacy, err := p.Build(snapshot, []model.RouteIntent{intent}, now)
	if err != nil {
		t.Fatal(err)
	}
	if legacy.Routes[0].NextHop != "10.255.0.2" {
		t.Fatalf("legacy loopback changed: %+v", legacy.Routes)
	}
	if err := validation.Plan(legacy, snapshot, now); err != nil {
		t.Fatal(err)
	}
}

func TestThreeLinkDisjointCandidatesAndECMP(t *testing.T) {
	now := time.Now().UTC()
	snapshot := model.TopologySnapshot{Version: 1, GeneratedAt: now, ValidFrom: now,
		Nodes: []model.Node{{ID: "a", Loopback: "10.0.0.1", Enabled: true}, {ID: "b", Loopback: "10.0.0.2", Enabled: true}, {ID: "c", Loopback: "10.0.0.3", Enabled: true}, {ID: "d", Loopback: "10.0.0.4", Enabled: true}, {ID: "z", Loopback: "10.0.0.5", Enabled: true}},
	}
	for _, pair := range [][2]string{{"a", "b"}, {"b", "z"}, {"a", "c"}, {"c", "z"}, {"a", "d"}, {"d", "z"}} {
		snapshot.Links = append(snapshot.Links, model.Link{ID: pair[0] + pair[1], Source: pair[0], Target: pair[1], AdminUp: true, OperationalUp: true, LatencyUS: 1000, CapacityBPS: 1_000_000})
	}
	intent := model.RouteIntent{ID: "ecmp", Source: "a", Destination: "z", DestinationPrefix: "203.0.113.0/24", Policy: "ecmp", Redundancy: 2}
	plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{intent}, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Paths[intent.ID]) != 3 {
		t.Fatalf("candidate count=%d, want 3", len(plan.Paths[intent.ID]))
	}
	for _, route := range plan.Routes {
		if route.PathRole == "backup" || route.Metric != 100 {
			t.Fatalf("equal-cost route not marked ECMP: %#v", route)
		}
	}
}

func TestPriorityOrderedCapacityReservation(t *testing.T) {
	now := time.Now().UTC()
	snapshot := model.TopologySnapshot{
		Version: 1, GeneratedAt: now, ValidFrom: now,
		Nodes: []model.Node{
			{ID: "a", Loopback: "10.0.0.1", Enabled: true}, {ID: "b", Loopback: "10.0.0.2", Enabled: true},
			{ID: "c", Loopback: "10.0.0.3", Enabled: true}, {ID: "z", Loopback: "10.0.0.4", Enabled: true},
		},
		Links: []model.Link{
			{ID: "ab", Source: "a", Target: "b", AdminUp: true, OperationalUp: true, LatencyUS: 1, CapacityBPS: 100},
			{ID: "bz", Source: "b", Target: "z", AdminUp: true, OperationalUp: true, LatencyUS: 1, CapacityBPS: 100},
			{ID: "ac", Source: "a", Target: "c", AdminUp: true, OperationalUp: true, LatencyUS: 10, CapacityBPS: 100},
			{ID: "cz", Source: "c", Target: "z", AdminUp: true, OperationalUp: true, LatencyUS: 10, CapacityBPS: 100},
		},
	}
	low := model.RouteIntent{ID: "low", Source: "a", Destination: "z", DestinationPrefix: "198.51.100.0/24", DemandBPS: 60, Priority: 1}
	high := model.RouteIntent{ID: "high", Source: "a", Destination: "z", DestinationPrefix: "203.0.113.0/24", DemandBPS: 60, Priority: 100}
	plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{low, high}, now)
	if err != nil {
		t.Fatal(err)
	}
	if got := plan.Paths["high"][0].Nodes[1]; got != "b" {
		t.Fatalf("high-priority intent used %q, want scarce low-latency path via b", got)
	}
	if got := plan.Paths["low"][0].Nodes[1]; got != "c" {
		t.Fatalf("low-priority intent used %q, want residual path via c", got)
	}
}
