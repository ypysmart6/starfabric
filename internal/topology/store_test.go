package topology_test

import (
	"errors"
	"path/filepath"
	"testing"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/testutil"
	"github.com/starfabric/starfabric/internal/topology"
)

func TestStoreRejectsDuplicateAndOutOfOrderEvents(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "topology.json")
	store, err := topology.New(testutil.Diamond(), path)
	if err != nil {
		t.Fatal(err)
	}
	link := store.Snapshot().Links[0]
	event := model.TopologyEvent{EventID: "e-1", Subject: link.ID, Sequence: 2, Type: model.EventLinkDown, Link: &link}
	snapshot, err := store.Apply(event)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Version != 2 || snapshot.Links[0].OperationalUp {
		t.Fatalf("event was not applied: %#v", snapshot)
	}
	if _, err = store.Apply(event); !errors.Is(err, topology.ErrDuplicateEvent) {
		t.Fatalf("expected duplicate error, got %v", err)
	}
	event.EventID = "e-2"
	event.Sequence = 1
	if _, err = store.Apply(event); !errors.Is(err, topology.ErrStaleEvent) {
		t.Fatalf("expected stale error, got %v", err)
	}
	reloaded, err := topology.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Snapshot().Version != 2 {
		t.Fatalf("persisted version = %d", reloaded.Snapshot().Version)
	}
}
