package frr

import (
	"context"
	"strings"
	"sync"
	"testing"

	"github.com/starfabric/starfabric/internal/model"
)

type fakeRunner struct {
	mu       sync.Mutex
	commands []string
	routes   map[string]string
}

func (r *fakeRunner) Run(_ context.Context, args ...string) ([]byte, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.commands = append(r.commands, strings.Join(args, " "))
	for _, arg := range args {
		fields := strings.Fields(arg)
		if len(fields) >= 6 && fields[0] == "no" && fields[1] == "ipv6" && fields[2] == "route" {
			delete(r.routes, fields[3])
			continue
		}
		if len(fields) >= 5 && fields[0] == "ipv6" && fields[1] == "route" {
			r.routes[fields[2]] = fields[3]
		}
	}
	if len(args) == 2 && args[0] == "-c" && args[1] == "show ipv6 route static json" {
		parts := make([]string, 0, len(r.routes))
		for prefix, nextHop := range r.routes {
			parts = append(parts, `"`+prefix+`":[{"nexthops":[{"ip":"`+nextHop+`"}]}]`)
		}
		return []byte("{" + strings.Join(parts, ",") + "}"), nil
	}
	if len(args) == 2 && args[0] == "-c" && args[1] == "show ip route static json" {
		return []byte("{}"), nil
	}
	return []byte("ok"), nil
}

func TestPersistentDeviceCanRollbackAfterRestart(t *testing.T) {
	t.Parallel()
	runner := &fakeRunner{routes: make(map[string]string)}
	statePath := t.TempDir() + "/frr.json"
	device, err := NewPersistentDevice("r1", runner, statePath)
	if err != nil {
		t.Fatal(err)
	}
	plan := model.RoutePlan{ID: "p1", Routes: []model.RouteOperation{{Device: "r1", Prefix: "2001:db8:100::/64", NextHop: "2001:db8::2", Metric: 100}}}
	if err := device.Prepare(context.Background(), plan); err != nil {
		t.Fatal(err)
	}
	if err := device.Commit(context.Background(), plan.ID); err != nil {
		t.Fatal(err)
	}
	restarted, err := NewPersistentDevice("r1", runner, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := restarted.Rollback(context.Background(), plan.ID); err != nil {
		t.Fatal(err)
	}
	state, err := restarted.State(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if state.AppliedPlanID != "" || len(state.Routes) != 0 {
		t.Fatalf("rollback after restart left state %#v", state)
	}
}

func TestDeviceProgramsAndVerifiesFRR(t *testing.T) {
	t.Parallel()
	runner := &fakeRunner{routes: make(map[string]string)}
	device := NewDevice("r1", runner)
	plan := model.RoutePlan{ID: "p1", Routes: []model.RouteOperation{{Device: "r1", Prefix: "2001:db8:100::/64", NextHop: "2001:db8::2", NextHopNode: "r2", Metric: 100}}}
	if err := device.Prepare(context.Background(), plan); err != nil {
		t.Fatal(err)
	}
	if err := device.Commit(context.Background(), plan.ID); err != nil {
		t.Fatal(err)
	}
	state, err := device.State(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if state.AppliedPlanID != plan.ID || len(state.Routes) != 1 {
		t.Fatalf("unexpected actual state: %#v", state)
	}
}

func TestMissingRIBEntryRetainsOwnershipAcrossRestart(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	runner := &fakeRunner{routes: make(map[string]string)}
	statePath := t.TempDir() + "/frr.json"
	device, err := NewPersistentDevice("r1", runner, statePath)
	if err != nil {
		t.Fatal(err)
	}
	old := model.RouteOperation{Device: "r1", Prefix: "2001:db8:100::/64", NextHop: "2001:db8::2", Metric: 100}
	plan := model.RoutePlan{ID: "before", Routes: []model.RouteOperation{old}}
	if err := device.Prepare(ctx, plan); err != nil {
		t.Fatal(err)
	}
	if err := device.Commit(ctx, plan.ID); err != nil {
		t.Fatal(err)
	}
	// Simulate an unreachable next hop disappearing from the observed RIB.
	delete(runner.routes, old.Prefix)
	state, err := device.State(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if state.AppliedPlanID != "" || len(state.Routes) != 0 {
		t.Fatalf("drift hidden: %#v", state)
	}
	restarted, err := NewPersistentDevice("r1", runner, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if len(restarted.routes) != 1 {
		t.Fatal("inactive configured route lost its owner")
	}
	replacement := old
	replacement.NextHop = "2001:db8::3"
	next := model.RoutePlan{ID: "after", Routes: []model.RouteOperation{replacement}}
	if err := restarted.Prepare(ctx, next); err != nil {
		t.Fatal(err)
	}
	if err := restarted.Commit(ctx, next.ID); err != nil {
		t.Fatal(err)
	}
	last := runner.commands[len(runner.commands)-1]
	if !strings.Contains(last, "no "+render(old, false)) || !strings.Contains(last, render(replacement, false)) {
		t.Fatalf("must withdraw inactive old route and install replacement: %s", last)
	}
}

func TestObservedDeletionReinstallsUnchangedDesiredRoute(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	runner := &fakeRunner{routes: make(map[string]string)}
	device := NewDevice("r1", runner)
	route := model.RouteOperation{Device: "r1", Prefix: "2001:db8:100::/64", NextHop: "2001:db8::2", Metric: 100}
	plan := model.RoutePlan{ID: "same", Routes: []model.RouteOperation{route}}
	if err := device.Prepare(ctx, plan); err != nil {
		t.Fatal(err)
	}
	if err := device.Commit(ctx, plan.ID); err != nil {
		t.Fatal(err)
	}
	delete(runner.routes, route.Prefix)
	if _, err := device.State(ctx); err != nil {
		t.Fatal(err)
	}
	if err := device.Prepare(ctx, plan); err != nil {
		t.Fatal(err)
	}
	if err := device.Commit(ctx, plan.ID); err != nil {
		t.Fatal(err)
	}
	if runner.routes[route.Prefix] != route.NextHop {
		t.Fatal("unchanged desired route was not repaired")
	}
}
