package verification

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestParallelObservationsStillRejectActualBlackholesAndReadFailures(t *testing.T) {
	snapshot := testutil.Diamond()
	intent := testutil.Intent()
	intent.Redundancy = 0
	plan, err := planner.New(planner.Config{}).Build(snapshot, []model.RouteIntent{intent}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	registry := adapter.NewRegistry()
	devices := map[string]*adapter.MemoryDevice{}
	for _, node := range snapshot.Nodes {
		device := adapter.NewMemoryDevice(node.ID)
		local := plan.Clone()
		local.Routes = nil
		for _, route := range plan.Routes {
			if route.Device == node.ID {
				local.Routes = append(local.Routes, route)
			}
		}
		if err := device.Prepare(context.Background(), local); err != nil {
			t.Fatal(err)
		}
		if err := device.Commit(context.Background(), plan.ID); err != nil {
			t.Fatal(err)
		}
		registry.Add(device)
		devices[node.ID] = device
	}
	probe := NewRouteProbe(registry, func() model.TopologySnapshot { return snapshot })
	if err := probe.Verify(context.Background(), plan); err != nil {
		t.Fatal(err)
	}
	devices["c"].SetFault(adapter.FaultState)
	if err := probe.Verify(context.Background(), plan); err == nil || !strings.Contains(err.Error(), "read c") {
		t.Fatalf("failed device observation was ignored: %v", err)
	}
	devices["c"].SetFault(adapter.FaultNone)
	empty := plan.Clone()
	empty.ID, empty.Routes = "withdrawn", nil
	victim := devices[plan.Paths[intent.ID][0].Nodes[1]]
	if err := victim.Prepare(context.Background(), empty); err != nil {
		t.Fatal(err)
	}
	if err := victim.Commit(context.Background(), empty.ID); err != nil {
		t.Fatal(err)
	}
	if err := probe.Verify(context.Background(), plan); err == nil || !strings.Contains(err.Error(), "blackhole") {
		t.Fatalf("desired plan hid an actual forwarding blackhole: %v", err)
	}
}
