package reconcile_test

import (
	"context"
	"io"
	"path/filepath"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/reconcile"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestPartialCommitRollsBackEveryDevice(t *testing.T) {
	t.Parallel()
	snapshot := testutil.Diamond()
	plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{testutil.Intent()}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	devices := map[string]*adapter.MemoryDevice{}
	registry := adapter.NewRegistry()
	for _, node := range snapshot.Nodes {
		device := adapter.NewMemoryDevice(node.ID)
		devices[node.ID] = device
		registry.Add(device)
	}
	devices["b"].SetFault(adapter.FaultCommit)
	r, err := reconcile.New(reconcile.Config{StatePath: filepath.Join(t.TempDir(), "state.json"), RetryCount: 0, BatchSize: 1}, func() model.TopologySnapshot { return snapshot }, registry, observability.NewMetrics(), observability.NewLogger(io.Discard))
	if err != nil {
		t.Fatal(err)
	}
	status, err := r.Apply(context.Background(), plan)
	if err == nil || status.Phase != model.PhaseRolledBack {
		t.Fatalf("expected rollback, status=%#v err=%v", status, err)
	}
	for name, device := range devices {
		state, stateErr := device.State(context.Background())
		if stateErr != nil {
			t.Fatal(stateErr)
		}
		if len(state.Routes) != 0 {
			t.Fatalf("device %s retained routes after rollback: %#v", name, state.Routes)
		}
	}
}
