package reconcile_test

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/reconcile"
	"github.com/starfabric/starfabric/internal/testutil"
	"github.com/starfabric/starfabric/internal/verification"
)

func TestProfilesIncludeNoopFailureRetriesAndRollback(t *testing.T) {
	for _, fail := range []bool{false, true} {
		snapshot := testutil.Diamond()
		plan, err := planner.New(planner.Config{PlanTTL: time.Minute}).Build(snapshot, []model.RouteIntent{testutil.Intent()}, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		registry := adapter.NewRegistry()
		for _, node := range snapshot.Nodes {
			device := adapter.NewMemoryDevice(node.ID)
			if fail && node.ID == "b" {
				device.SetFault(adapter.FaultCommit)
			}
			registry.Add(device)
		}
		var logs bytes.Buffer
		r, err := reconcile.New(reconcile.Config{RetryCount: 1, RetryBackoff: time.Microsecond, BatchSize: 2,
			Verifier: verification.NewRouteProbe(registry, func() model.TopologySnapshot { return snapshot })},
			func() model.TopologySnapshot { return snapshot }, registry, nil, observability.NewLogger(&logs))
		if err != nil {
			t.Fatal(err)
		}
		ctx := observability.WithFields(context.Background(), map[string]string{"request_id": "request", "frame_id": "123"})
		_, err = r.Apply(ctx, plan)
		if (err != nil) != fail {
			t.Fatalf("fail=%v err=%v", fail, err)
		}
		if !fail {
			if _, err := r.Apply(ctx, plan); err != nil {
				t.Fatal(err)
			}
		}
		var profiles []struct {
			Message     string            `json:"message"`
			Outcome     string            `json:"outcome"`
			Correlation map[string]string `json:"correlation"`
			Profile     struct {
				Phases map[string]observability.Timing `json:"phases"`
				Counts map[string]int64                `json:"counts"`
			} `json:"profile"`
		}
		for _, line := range bytes.Split(bytes.TrimSpace(logs.Bytes()), []byte{'\n'}) {
			var entry struct {
				Message     string            `json:"message"`
				Outcome     string            `json:"outcome"`
				Correlation map[string]string `json:"correlation"`
				Profile     struct {
					Phases map[string]observability.Timing `json:"phases"`
					Counts map[string]int64                `json:"counts"`
				} `json:"profile"`
			}
			if err := json.Unmarshal(line, &entry); err != nil {
				t.Fatal(err)
			}
			if entry.Message == "reconciliation profile" {
				profiles = append(profiles, entry)
			}
		}
		if len(profiles) == 0 {
			t.Fatal("missing profile")
		}
		first := profiles[0]
		if first.Correlation["request_id"] != "request" || first.Correlation["plan_id"] != plan.ID {
			t.Fatal(first.Correlation)
		}
		for _, phase := range []string{"precheck", "prepare", "commit", "reconciler_persist"} {
			if first.Profile.Phases[phase].Calls == 0 {
				t.Fatalf("missing %s", phase)
			}
		}
		if fail {
			if first.Outcome != "failed" || first.Profile.Counts["retries"] == 0 || first.Profile.Phases["rollback"].Calls == 0 {
				t.Fatal(first)
			}
		} else {
			if first.Profile.Phases["route_probe"].Calls != 1 || len(profiles) != 2 || profiles[1].Outcome != "noop" || profiles[1].Profile.Phases["noop_verify"].Calls != 1 {
				t.Fatal(profiles)
			}
		}
	}
}
