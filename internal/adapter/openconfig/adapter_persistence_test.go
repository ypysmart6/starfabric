package openconfig

import (
	"context"
	"sync"
	"testing"

	"github.com/starfabric/starfabric/internal/model"
)

type memoryProgrammer struct {
	mu     sync.Mutex
	routes []model.RouteOperation
}

func (p *memoryProgrammer) Apply(_ context.Context, _, desired []model.RouteOperation) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.routes = append([]model.RouteOperation(nil), desired...)
	return nil
}

func (p *memoryProgrammer) Read(context.Context) ([]model.RouteOperation, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]model.RouteOperation(nil), p.routes...), nil
}

func (*memoryProgrammer) Close() error { return nil }

func TestAdapterJournalSurvivesRestart(t *testing.T) {
	t.Parallel()
	programmer := &memoryProgrammer{}
	statePath := t.TempDir() + "/openconfig.json"
	config := DeviceConfig{
		Name: "r1", Management: Endpoint{Address: "127.0.0.1:1", Insecure: true},
		ExternalProgrammer: programmer, StatePath: statePath,
	}
	device, err := NewDevice(config)
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
	_ = device.Close()
	restarted, err := NewDevice(config)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
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
