package app

import (
	"context"
	"io"
	"path/filepath"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/predictive"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestDesiredIntentsSurviveRestart(t *testing.T) {
	directory := t.TempDir()
	config := Config{
		TopologyStatePath:  filepath.Join(directory, "topology.json"),
		ReconcileStatePath: filepath.Join(directory, "reconciler.json"),
		IntentsStatePath:   filepath.Join(directory, "intents.json"),
	}
	first, err := Load(config, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	replacement := testutil.Intent()
	replacement.ID = "persisted"
	replacement.GatewayCandidates = []string{"d"}
	replacement.Destination = ""
	replacement.Labels = map[string]string{"owner": "mission"}
	if err := first.SetIntents([]model.RouteIntent{replacement}); err != nil {
		t.Fatal(err)
	}
	second, err := Load(config, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	got := second.Intents()
	if len(got) != 1 || got[0].ID != "persisted" || got[0].Labels["owner"] != "mission" {
		t.Fatalf("restarted intents = %#v", got)
	}
	got[0].Labels["owner"] = "mutated"
	if second.Intents()[0].Labels["owner"] != "mission" {
		t.Fatal("Intents returned mutable internal label state")
	}
}

func TestPredictiveSchedulePreprogramsFuturePath(t *testing.T) {
	directory := t.TempDir()
	topology := testutil.Diamond()
	intent := testutil.Intent()
	intent.Redundancy = 0
	application, err := New(Config{
		TopologyStatePath: filepath.Join(directory, "topology.json"), ReconcileStatePath: filepath.Join(directory, "reconciler.json"),
		IntentsStatePath: filepath.Join(directory, "intents.json"), PredictiveStatePath: filepath.Join(directory, "predictive.json"),
		PlanTTL: time.Second, OperationTimeout: time.Second,
	}, topology, []model.RouteIntent{intent}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	defer application.Close()
	initial, _, err := application.Reconcile(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got := initial.Paths[intent.ID][0].Nodes; len(got) < 2 || got[1] != "b" {
		t.Fatalf("initial path = %v, want path through b", got)
	}
	now := time.Now().UTC()
	contact := topology.Links[0]
	schedule, err := application.CreatePredictiveSchedule([]predictive.ContactWindow{{
		Link: contact, Start: now.Add(-time.Second), End: now.Add(120 * time.Millisecond),
	}}, time.Second, 60*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		status, _ := application.PredictiveSchedule(schedule.ID)
		if status.State == "failed" {
			t.Fatalf("schedule failed: %s", status.Error)
		}
		if status.State == "completed" {
			committed := application.CommittedPlan()
			if committed == nil {
				t.Fatal("no committed predicted plan")
			}
			got := committed.Paths[intent.ID][0].Nodes
			if len(got) < 2 || got[1] != "c" {
				t.Fatalf("predicted path = %v, want path through c", got)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("predictive schedule did not complete")
}

func TestPredictiveScheduleAppliesChangedCurrentBoundary(t *testing.T) {
	topology := testutil.Diamond()
	intent := testutil.Intent()
	intent.Redundancy = 0
	application, err := New(Config{PlanTTL: time.Minute, OperationTimeout: time.Second}, topology, []model.RouteIntent{intent}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	defer application.Close()
	initial, _, err := application.Reconcile(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if initial.Paths[intent.ID][0].Nodes[1] != "b" {
		t.Fatal("fixture must initially forward through b")
	}
	// New contact information already favors c at the current boundary, so
	// the forecast has only one distinct plan. That plan must not be skipped.
	contact := topology.Links[0]
	contact.LatencyUS = 1_000_000
	now := time.Now().UTC()
	schedule, err := application.CreatePredictiveSchedule([]predictive.ContactWindow{{
		Link: contact, Start: now.Add(-time.Second), End: now.Add(time.Second),
	}}, 500*time.Millisecond, 100*time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if len(schedule.Activations) != 1 {
		t.Fatalf("want one distinct forecast, got %d", len(schedule.Activations))
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		status, _ := application.PredictiveSchedule(schedule.ID)
		if status.State == "failed" {
			t.Fatal(status.Error)
		}
		if status.State == "completed" {
			committed := application.CommittedPlan()
			if committed.ID != schedule.Activations[0].Plan.ID || committed.Paths[intent.ID][0].Nodes[1] != "c" {
				t.Fatal("completion must commit the changed current forecast")
			}
			if application.Topology().Version <= topology.Version {
				t.Fatal("changed contact topology must receive a new version")
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("current-boundary activation did not complete")
}
