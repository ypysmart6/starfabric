package frr

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/reconcile"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestNewTopologyAfterControllerRestartRestoresExternallyWithdrawnRoute(t *testing.T) {
	snapshot := testutil.Diamond()
	for index := range snapshot.Nodes {
		snapshot.Nodes[index].Loopback = fmt.Sprintf("2001:db8::%d", index+1)
	}
	intent := testutil.Intent()
	intent.DestinationPrefix, intent.Redundancy = "2001:db8:100::/64", 0
	computer := planner.New(planner.Config{PlanTTL: time.Minute})
	plan, err := computer.Build(snapshot, []model.RouteIntent{intent}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	directory := t.TempDir()
	runners := map[string]*fakeRunner{}
	for _, node := range snapshot.Nodes {
		runners[node.ID] = &fakeRunner{routes: make(map[string]string)}
	}
	restart := func() *reconcile.Reconciler {
		t.Helper()
		registry := adapter.NewRegistry()
		for name, runner := range runners {
			device, err := NewPersistentDevice(name, runner, filepath.Join(directory, name+".json"))
			if err != nil {
				t.Fatal(err)
			}
			registry.Add(device)
		}
		r, err := reconcile.New(reconcile.Config{BatchSize: 2, StatePath: filepath.Join(directory, "reconcile.json")},
			func() model.TopologySnapshot { return snapshot }, registry, nil, nil)
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	if _, err := restart().Apply(context.Background(), plan); err != nil {
		t.Fatal(err)
	}
	victim := plan.Routes[0].Device
	runners[victim].mu.Lock()
	delete(runners[victim].routes, intent.DestinationPrefix)
	runners[victim].mu.Unlock()
	snapshot.Version++ // fresh physical observations arrive after ground recovery
	next, err := computer.Build(snapshot, []model.RouteIntent{intent}, time.Now())
	if err != nil || next.ID == plan.ID {
		t.Fatalf("expected a new valid plan: %v", err)
	}
	if _, err := restart().Apply(context.Background(), next); err != nil {
		t.Fatalf("new topology must repair the observed FIB after restart: %v", err)
	}
	runners[victim].mu.Lock()
	defer runners[victim].mu.Unlock()
	if runners[victim].routes[intent.DestinationPrefix] == "" {
		t.Fatal("withdrawn route was not restored")
	}
}
