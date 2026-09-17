package predictive

import (
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestForecastGatewaySwitchAndExpiry(t *testing.T) {
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	base := model.TopologySnapshot{Version: 10, GeneratedAt: now, ValidFrom: now,
		Nodes: []model.Node{{ID: "sat", Loopback: "10.0.0.1", Enabled: true}, {ID: "gw-a", Loopback: "10.0.0.2", Enabled: true}, {ID: "gw-b", Loopback: "10.0.0.3", Enabled: true}},
	}
	windows := []ContactWindow{
		{Link: model.Link{ID: "a", Source: "sat", Target: "gw-a", CapacityBPS: 1_000_000, LatencyUS: 100, AdminUp: true}, Start: now, End: now.Add(10 * time.Second)},
		{Link: model.Link{ID: "b", Source: "sat", Target: "gw-b", CapacityBPS: 1_000_000, LatencyUS: 50, AdminUp: true}, Start: now.Add(10 * time.Second), End: now.Add(30 * time.Second)},
	}
	intent := model.RouteIntent{ID: "internet", Source: "sat", GatewayCandidates: []string{"gw-a", "gw-b"}, DestinationPrefix: "203.0.113.0/24", Policy: "latency"}
	engine := Engine{Planner: planner.New(planner.Config{PlanTTL: time.Minute}), Lead: 2 * time.Second}
	activations, err := engine.Schedule(base, windows, []model.RouteIntent{intent}, now, 20*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(activations) != 2 {
		t.Fatalf("activations = %d, want 2", len(activations))
	}
	if got := activations[0].Plan.Metadata["selected_gateways"].(map[string]string)["internet"]; got != "gw-a" {
		t.Fatalf("first gateway = %q", got)
	}
	if !activations[0].Plan.ExpiresAt.Equal(now.Add(10 * time.Second)) {
		t.Fatalf("plan expiry = %s", activations[0].Plan.ExpiresAt)
	}
	if !activations[1].ActivateAt.Equal(now.Add(8 * time.Second)) {
		t.Fatalf("activation = %s", activations[1].ActivateAt)
	}
}

func TestUnchangedForwardingStillAdvancesExpiredContactSnapshot(t *testing.T) {
	now := time.Now().UTC()
	base := testutil.Diamond()
	intent := testutil.Intent()
	intent.Redundancy = 0
	contact := base.Links[0]
	contact.LatencyUS = 1_000_000 // Both forecasts use the other branch.
	engine := Engine{Planner: planner.New(planner.Config{PlanTTL: time.Minute}), Lead: time.Second}
	activations, err := engine.Schedule(base, []ContactWindow{{Link: contact, Start: now.Add(-time.Second), End: now.Add(5 * time.Second)}}, []model.RouteIntent{intent}, now, 10*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(activations) != 2 || len(activations[1].ChangedPrefixes) != 0 {
		t.Fatalf("unchanged forwarding must retain the contact boundary: %+v", activations)
	}
	if !activations[1].Topology.ValidUntil.IsZero() || activations[1].Topology.Links[0].OperationalUp {
		t.Fatal("last snapshot must reflect expired contact without expiring the whole topology")
	}
}
