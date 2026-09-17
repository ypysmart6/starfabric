package topology_test

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/testutil"
	"github.com/starfabric/starfabric/internal/topology"
)

func TestLiveStreamBoundsEventCacheAndPreservesSequenceGuard(t *testing.T) {
	initial := testutil.Diamond()
	path := filepath.Join(t.TempDir(), "topology.json")
	store, err := topology.New(initial, path)
	if err != nil {
		t.Fatal(err)
	}
	link := initial.Links[0]
	old := model.TopologyEvent{EventID: "old", Subject: link.ID, Sequence: 1, Type: model.EventLinkUpdate, Link: &link}
	if _, err := store.ApplyBatch([]model.TopologyEvent{old}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var state map[string]json.RawMessage
	if err := json.Unmarshal(raw, &state); err != nil {
		t.Fatal(err)
	}
	state["seen_events"], _ = json.Marshal(map[string]time.Time{"old": time.Now().Add(-10 * time.Minute)})
	raw, _ = json.Marshal(state)
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	store, err = topology.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	next := old
	next.EventID, next.Sequence = "current", 2
	if _, err := store.ApplyBatch([]model.TopologyEvent{next}); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(path)
	if err := json.Unmarshal(raw, &state); err != nil {
		t.Fatal(err)
	}
	var seen map[string]time.Time
	if err := json.Unmarshal(state["seen_events"], &seen); err != nil {
		t.Fatal(err)
	}
	if _, exists := seen["old"]; exists {
		t.Fatal("expired deduplication entry retained")
	}
	if _, err := store.ApplyBatch([]model.TopologyEvent{old}); !errors.Is(err, topology.ErrStaleEvent) {
		t.Fatalf("expired event bypassed durable sequence guard: %v", err)
	}
}

func TestPhysicalSamplingBatchIsAtomicAndDurable(t *testing.T) {
	initial := testutil.Diamond()
	path := filepath.Join(t.TempDir(), "topology.json")
	store, err := topology.New(initial, path)
	if err != nil {
		t.Fatal(err)
	}
	first, second := initial.Links[0], initial.Links[1]
	first.LatencyUS, second.LatencyUS = 12345, 54321
	events := []model.TopologyEvent{
		{EventID: "physical-1", Subject: first.ID, Sequence: 1, Type: model.EventLinkUpdate, Link: &first},
		{EventID: "physical-2", Subject: second.ID, Sequence: 1, Type: model.EventLinkUpdate, Link: &second},
	}
	broken := append([]model.TopologyEvent(nil), events...)
	broken[1].Sequence = 0
	if _, err := store.ApplyBatch(broken); err == nil {
		t.Fatal("invalid batch accepted")
	}
	if store.Snapshot().Version != initial.Version || store.Snapshot().Links[0].LatencyUS == first.LatencyUS {
		t.Fatal("part of rejected batch became visible")
	}
	snapshot, err := store.ApplyBatch(events)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Version != initial.Version+1 || snapshot.Links[0].LatencyUS != first.LatencyUS || snapshot.Links[1].LatencyUS != second.LatencyUS {
		t.Fatal("sampling instant was not published as one version")
	}
	loaded, err := topology.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loaded.ApplyBatch(events); !errors.Is(err, topology.ErrDuplicateEvent) {
		t.Fatalf("replayed batch: %v", err)
	}
	if loaded.Snapshot().Version != snapshot.Version {
		t.Fatal("duplicate batch changed version")
	}
}

func TestBatchPersistenceFailureDoesNotConsumeSequences(t *testing.T) {
	initial := testutil.Diamond()
	store, err := topology.New(initial, t.TempDir()) // writing over a directory must fail
	if err != nil {
		t.Fatal(err)
	}
	link := initial.Links[0]
	event := model.TopologyEvent{EventID: "sample", Subject: link.ID, Sequence: 1, Type: model.EventLinkUpdate, Link: &link}
	for attempt := 0; attempt < 2; attempt++ {
		_, err = store.ApplyBatch([]model.TopologyEvent{event})
		if err == nil || errors.Is(err, topology.ErrDuplicateEvent) || errors.Is(err, topology.ErrStaleEvent) {
			t.Fatalf("bad retry after persistence failure: %v", err)
		}
	}
	if store.Snapshot().Version != initial.Version {
		t.Fatal("failed persistence changed topology")
	}
}
