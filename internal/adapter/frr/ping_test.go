package frr

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

func TestPingProbeBoundsFanoutAndDeduplicatesGatewayChecks(t *testing.T) {
	var active, peak, calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := active.Add(1)
		defer active.Add(-1)
		for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
		}
		calls.Add(1)
		var request struct {
			Program string
			Args    []string
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
		}
		if request.Program != "ping" || len(request.Args) != 6 {
			t.Errorf("invalid probe: %+v", request)
		}
		time.Sleep(5 * time.Millisecond)
		_, _ = w.Write([]byte(`{"exit_code":0}`))
	}))
	defer s.Close()
	agent, _ := NewAgent(s.URL, "secret", true)
	nodes := []model.Node{{ID: "source", Labels: map[string]string{"frr_container": "source"}}}
	plan := model.RoutePlan{}
	for i := 1; i <= 24; i++ {
		id := fmt.Sprintf("gw-%d", i)
		nodes = append(nodes, model.Node{ID: id, Labels: map[string]string{"probe_target": fmt.Sprintf("192.0.2.%d", i)}})
		for j := 0; j < 16; j++ {
			plan.Intents = append(plan.Intents, model.RouteIntent{ID: fmt.Sprintf("%s-%d", id, j), Source: "source", Destination: id})
		}
	}
	probe := NewAgentPingProbe(nodes, agent)
	if err := probe.Verify(context.Background(), plan); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 24 || peak.Load() > 8 || peak.Load() < 2 {
		t.Fatalf("calls=%d peak=%d", calls.Load(), peak.Load())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := probe.Verify(ctx, plan); err == nil || calls.Load() != 24 {
		t.Fatal("cancelled probe launched more work or succeeded")
	}
	plan.Intents = append(plan.Intents, model.RouteIntent{ID: "invalid", Source: "missing", Destination: "gw-1"})
	if err := probe.Verify(context.Background(), plan); err == nil || calls.Load() != 24 {
		t.Fatal("invalid plan launched partial probes or succeeded")
	}
}
