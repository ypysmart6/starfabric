package scale_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
)

func constellation(size int) model.TopologySnapshot {
	now := time.Now().UTC()
	value := model.TopologySnapshot{Version: 1, GeneratedAt: now, ValidFrom: now}
	for i := 0; i < size; i++ {
		value.Nodes = append(value.Nodes, model.Node{ID: fmt.Sprintf("sat-%04d", i), Loopback: fmt.Sprintf("2001:db8::%x", i+1), Kind: "satellite", Enabled: true})
	}
	for i := 0; i < size; i++ {
		for _, target := range []int{(i + 1) % size, (i + 16) % size} {
			value.Links = append(value.Links, model.Link{ID: fmt.Sprintf("l-%04d-%04d", i, target), Source: fmt.Sprintf("sat-%04d", i), Target: fmt.Sprintf("sat-%04d", target), LinkType: "optical", AcquisitionState: "locked", AdminUp: true, OperationalUp: true, LatencyUS: 1000, CapacityBPS: 10_000_000_000})
		}
	}
	return value
}

func BenchmarkPlannerThousandSatellite(b *testing.B) {
	snapshot := constellation(1000)
	intent := model.RouteIntent{ID: "scale", Source: "sat-0000", Destination: "sat-0999", DestinationPrefix: "2001:db8:ffff::/64", Policy: "latency", Redundancy: 1}
	engine := planner.New(planner.Config{PlanTTL: time.Minute})
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := engine.Build(snapshot, []model.RouteIntent{intent}, time.Now()); err != nil {
			b.Fatal(err)
		}
	}
}

func TestPlannerThousandSatelliteSmoke(t *testing.T) {
	if testing.Short() {
		t.Skip("scale smoke disabled in short mode")
	}
	value, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(constellation(1000), []model.RouteIntent{{ID: "scale", Source: "sat-0000", Destination: "sat-0999", DestinationPrefix: "2001:db8:ffff::/64", Policy: "latency", Redundancy: 1}}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(value.Paths["scale"]) < 2 {
		t.Fatalf("expected primary and disjoint backup, got %d", len(value.Paths["scale"]))
	}
}
