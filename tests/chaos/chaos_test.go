package chaos_test

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/reconcile"
	"github.com/starfabric/starfabric/internal/testutil"
	"github.com/starfabric/starfabric/internal/topology"
)

func TestControllerRestartRecoversTopologyAndCommittedPlan(t *testing.T) {
	dir := t.TempDir()
	config := app.Config{TopologyStatePath: filepath.Join(dir, "topology.json"), ReconcileStatePath: filepath.Join(dir, "reconcile.json"), PlanTTL: time.Minute}
	first, err := app.Load(config, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := first.Reconcile(context.Background()); err != nil {
		t.Fatal(err)
	}
	link := first.Topology().Links[0]
	if _, err := first.ApplyEvent(model.TopologyEvent{EventID: "restart-link", Subject: link.ID, Sequence: 1, Type: model.EventLinkDown, ObservedAt: time.Now(), EffectiveAt: time.Now(), Link: &link}); err != nil {
		t.Fatal(err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}

	second, err := app.Load(config, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	if second.Topology().Version != 2 {
		t.Fatalf("topology version was not recovered: %d", second.Topology().Version)
	}
	if second.CommittedPlan() == nil {
		t.Fatal("committed plan was not recovered")
	}
}

func TestDelayedAndOutOfOrderTelemetryCannotOverwriteNewState(t *testing.T) {
	store, err := topology.New(testutil.Diamond(), "")
	if err != nil {
		t.Fatal(err)
	}
	link := testutil.Diamond().Links[0]
	now := time.Now()
	if _, err := store.Apply(model.TopologyEvent{EventID: "new", Subject: link.ID, Sequence: 2, Type: model.EventLinkDown, ObservedAt: now, EffectiveAt: now, Link: &link}); err != nil {
		t.Fatal(err)
	}
	_, err = store.Apply(model.TopologyEvent{EventID: "late", Subject: link.ID, Sequence: 1, Type: model.EventLinkUp, ObservedAt: now.Add(-time.Minute), EffectiveAt: now, Link: &link})
	if !errors.Is(err, topology.ErrStaleEvent) {
		t.Fatalf("expected stale event rejection, got %v", err)
	}
}

func TestNodeFailureReroutesAndRecoveryRestoresPreferredPath(t *testing.T) {
	intent := testutil.Intent()
	intent.Redundancy = 0
	application, err := app.New(app.Config{PlanTTL: time.Minute}, testutil.Diamond(), []model.RouteIntent{intent}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	defer application.Close()
	initial, _, err := application.Reconcile(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got := initial.Paths["a-d"][0].Nodes; len(got) != 3 || got[1] != "b" {
		t.Fatalf("unexpected preferred path: %v", got)
	}
	node := testutil.Diamond().Nodes[1]
	now := time.Now().UTC()
	if _, err := application.ApplyEvent(model.TopologyEvent{EventID: "node-b-down", Subject: node.ID, Sequence: 1, Type: model.EventNodeDown, ObservedAt: now, EffectiveAt: now, Node: &node}); err != nil {
		t.Fatal(err)
	}
	rerouted, rerouteStatus, err := application.Reconcile(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got := rerouted.Paths["a-d"][0].Nodes; len(got) != 3 || got[1] != "c" {
		t.Fatalf("node failure did not use backup path: %v", got)
	}
	if len(rerouteStatus.DeferredDevices) != 1 || rerouteStatus.DeferredDevices[0] != "b" {
		t.Fatalf("offline stale-route cleanup was not deferred: %#v", rerouteStatus.DeferredDevices)
	}
	if _, err := application.ApplyEvent(model.TopologyEvent{EventID: "node-b-up", Subject: node.ID, Sequence: 2, Type: model.EventNodeUp, ObservedAt: now.Add(time.Millisecond), EffectiveAt: time.Now().UTC(), Node: &node}); err != nil {
		t.Fatal(err)
	}
	restored, recoveryStatus, err := application.Reconcile(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got := restored.Paths["a-d"][0].Nodes; len(got) != 3 || got[1] != "b" {
		t.Fatalf("preferred path was not restored: %v", got)
	}
	if len(recoveryStatus.DeferredDevices) != 0 {
		t.Fatalf("recovered device remained deferred: %#v", recoveryStatus.DeferredDevices)
	}
}

func TestLinkFlapConvergesToLatestSequence(t *testing.T) {
	store, err := topology.New(testutil.Diamond(), "")
	if err != nil {
		t.Fatal(err)
	}
	link := testutil.Diamond().Links[0]
	now := time.Now().UTC()
	for sequence, eventType := range []model.EventType{model.EventLinkDown, model.EventLinkUp} {
		if _, err := store.Apply(model.TopologyEvent{EventID: fmt.Sprintf("flap-%d", sequence+1), Subject: link.ID, Sequence: uint64(sequence + 1), Type: eventType, ObservedAt: now.Add(time.Duration(sequence) * time.Millisecond), EffectiveAt: now, Link: &link}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := store.Apply(model.TopologyEvent{EventID: "flap-stale", Subject: link.ID, Sequence: 1, Type: model.EventLinkDown, ObservedAt: now, EffectiveAt: now, Link: &link}); !errors.Is(err, topology.ErrStaleEvent) {
		t.Fatalf("stale flap was accepted: %v", err)
	}
	for _, candidate := range store.Snapshot().Links {
		if candidate.ID == link.ID && !candidate.OperationalUp {
			t.Fatal("latest link-up state was not retained")
		}
	}
}

type rejectingProbe struct{}

func (rejectingProbe) Verify(context.Context, model.RoutePlan) error {
	return errors.New("injected OTG loss SLO failure")
}

func TestTrafficVerificationFailureRollsBack(t *testing.T) {
	snapshot := testutil.Diamond()
	registry := adapter.NewRegistry()
	devices := make(map[string]*adapter.MemoryDevice)
	for _, node := range snapshot.Nodes {
		device := adapter.NewMemoryDevice(node.ID)
		devices[node.ID] = device
		registry.Add(device)
	}
	plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{testutil.Intent()}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	r, err := reconcile.New(reconcile.Config{Verifier: rejectingProbe{}, BatchSize: 8}, func() model.TopologySnapshot { return snapshot }, registry, observability.NewMetrics(), observability.NewLogger(io.Discard))
	if err != nil {
		t.Fatal(err)
	}
	status, err := r.Apply(context.Background(), plan)
	if err == nil || status.Phase != model.PhaseRolledBack {
		t.Fatalf("expected rollback, status=%s err=%v", status.Phase, err)
	}
	for name, device := range devices {
		state, _ := device.State(context.Background())
		if len(state.Routes) != 0 {
			t.Fatalf("%s retained routes", name)
		}
	}
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) {
	return 0, errors.New("collector filesystem unavailable")
}

func TestObservabilityFailureDoesNotStopControl(t *testing.T) {
	application, err := app.New(app.Config{PlanTTL: time.Minute}, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, failingWriter{})
	if err != nil {
		t.Fatal(err)
	}
	defer application.Close()
	if _, status, err := application.Reconcile(context.Background()); err != nil || status.Phase != model.PhaseCommitted {
		t.Fatalf("control loop depended on log backend: status=%s err=%v", status.Phase, err)
	}
}

type timeoutDevice struct{ *adapter.MemoryDevice }

func (d timeoutDevice) Health(ctx context.Context) error { <-ctx.Done(); return ctx.Err() }

func TestUnresponsiveDeviceHonorsDeadline(t *testing.T) {
	snapshot := testutil.Diamond()
	plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{testutil.Intent()}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	registry := adapter.NewRegistry()
	for _, node := range snapshot.Nodes {
		base := adapter.NewMemoryDevice(node.ID)
		if node.ID == plan.Routes[0].Device {
			registry.Add(timeoutDevice{base})
		} else {
			registry.Add(base)
		}
	}
	r, err := reconcile.New(reconcile.Config{OperationTimeout: 10 * time.Millisecond, RetryCount: 1}, func() model.TopologySnapshot { return snapshot }, registry, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	_, err = r.Apply(context.Background(), plan)
	if err == nil {
		t.Fatal("expected device timeout")
	}
	if time.Since(started) > time.Second {
		t.Fatalf("deadline not enforced: %s", time.Since(started))
	}
}
