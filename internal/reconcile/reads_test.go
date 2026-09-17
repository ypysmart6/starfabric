package reconcile

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestDeviceReadsOverlapWithinBoundAndFinishBeforeReturning(t *testing.T) {
	registry := adapter.NewRegistry()
	names := []string{"a", "b", "c", "d", "e"}
	for _, name := range names {
		registry.Add(adapter.NewMemoryDevice(name))
	}
	r := &Reconciler{config: Config{BatchSize: 2}, registry: registry}
	entered, release, done := make(chan string, len(names)), make(chan struct{}), make(chan []error, 1)
	var active, peak atomic.Int32
	go func() {
		done <- r.readDevices(names, func(device adapter.DeviceAdapter) error {
			count := active.Add(1)
			for old := peak.Load(); count > old && !peak.CompareAndSwap(old, count); old = peak.Load() {
			}
			entered <- device.Name()
			<-release
			active.Add(-1)
			return nil
		})
	}()
	for range 2 {
		select {
		case <-entered:
		case <-time.After(2 * time.Second):
			close(release)
			t.Fatal("independent device reads did not overlap")
		}
	}
	select {
	case <-entered:
		close(release)
		t.Fatal("device read concurrency exceeded batch size")
	case <-time.After(20 * time.Millisecond):
	}
	close(release)
	results := <-done
	if active.Load() != 0 || peak.Load() != 2 || len(results) != len(names) {
		t.Fatalf("unfinished or unbounded reads: active=%d peak=%d results=%v", active.Load(), peak.Load(), results)
	}
}

func TestParallelReadFailuresPreserveWholeTransactionRollback(t *testing.T) {
	for _, fault := range []adapter.FaultMode{adapter.FaultHealth, adapter.FaultPrepare, adapter.FaultCommit, adapter.FaultState} {
		t.Run(string(fault), func(t *testing.T) {
			snapshot := testutil.Diamond()
			plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{testutil.Intent()}, time.Now())
			if err != nil {
				t.Fatal(err)
			}
			registry := adapter.NewRegistry()
			devices := map[string]*adapter.MemoryDevice{}
			for _, node := range snapshot.Nodes {
				devices[node.ID] = adapter.NewMemoryDevice(node.ID)
				registry.Add(devices[node.ID])
			}
			devices["b"].SetFault(fault)
			r, err := New(Config{BatchSize: 3}, func() model.TopologySnapshot { return snapshot }, registry, nil, nil)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := r.Apply(context.Background(), plan); err == nil {
				t.Fatal("injected failure was ignored")
			}
			for name, device := range devices {
				device.SetFault(adapter.FaultNone)
				state, err := device.State(context.Background())
				if err != nil || len(state.Routes) != 0 || state.AppliedPlanID != "" {
					t.Fatalf("device %s retained a partial transaction: %#v, %v", name, state, err)
				}
			}
		})
	}
}
