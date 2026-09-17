package policy_test

import (
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/policy"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestShortestAndDisjointBackup(t *testing.T) {
	t.Parallel()
	snapshot := testutil.Diamond()
	intent := testutil.Intent()
	primary, err := policy.Shortest(snapshot, intent, intent.Policy, policy.Options{At: time.Now()})
	if err != nil {
		t.Fatal(err)
	}
	if got := primary.Nodes; len(got) != 3 || got[1] != "b" {
		t.Fatalf("unexpected primary: %v", got)
	}
	backup, err := policy.DisjointBackup(snapshot, intent, primary, true, policy.Options{At: time.Now()})
	if err != nil {
		t.Fatal(err)
	}
	if got := backup.Nodes; len(got) != 3 || got[1] != "c" {
		t.Fatalf("unexpected backup: %v", got)
	}
	if primary.LatencyUS >= backup.LatencyUS {
		t.Fatalf("expected primary latency below backup: %d >= %d", primary.LatencyUS, backup.LatencyUS)
	}
}

func TestCapacityConstraintRejectsAllLinks(t *testing.T) {
	t.Parallel()
	snapshot := testutil.Diamond()
	intent := testutil.Intent()
	intent.MinCapacityBPS = 2_000_000_000
	if _, err := policy.Shortest(snapshot, intent, intent.Policy, policy.Options{At: time.Now()}); err == nil {
		t.Fatal("expected no path")
	}
}

func TestBackupExcludesSharedRiskGroup(t *testing.T) {
	now := time.Now().UTC()
	snapshot := model.TopologySnapshot{
		Version: 1, GeneratedAt: now, ValidFrom: now,
		Nodes: []model.Node{
			{ID: "a", Enabled: true}, {ID: "b", Enabled: true}, {ID: "c", Enabled: true},
			{ID: "d", Enabled: true}, {ID: "z", Enabled: true},
		},
		Links: []model.Link{
			{ID: "ab", Source: "a", Target: "b", AdminUp: true, OperationalUp: true, LatencyUS: 1, CapacityBPS: 100, RiskGroups: []string{"terminal-a"}},
			{ID: "bz", Source: "b", Target: "z", AdminUp: true, OperationalUp: true, LatencyUS: 1, CapacityBPS: 100},
			{ID: "ac", Source: "a", Target: "c", AdminUp: true, OperationalUp: true, LatencyUS: 2, CapacityBPS: 100, RiskGroups: []string{"terminal-a"}},
			{ID: "cz", Source: "c", Target: "z", AdminUp: true, OperationalUp: true, LatencyUS: 2, CapacityBPS: 100},
			{ID: "ad", Source: "a", Target: "d", AdminUp: true, OperationalUp: true, LatencyUS: 3, CapacityBPS: 100, RiskGroups: []string{"terminal-b"}},
			{ID: "dz", Source: "d", Target: "z", AdminUp: true, OperationalUp: true, LatencyUS: 3, CapacityBPS: 100},
		},
	}
	intent := model.RouteIntent{ID: "risk", Source: "a", Destination: "z", DestinationPrefix: "192.0.2.0/24"}
	primary, err := policy.Shortest(snapshot, intent, "latency", policy.Options{At: now})
	if err != nil {
		t.Fatal(err)
	}
	backup, err := policy.DisjointBackup(snapshot, intent, primary, false, policy.Options{At: now})
	if err != nil {
		t.Fatal(err)
	}
	if got := backup.Nodes; len(got) != 3 || got[1] != "d" {
		t.Fatalf("backup reused primary SRLG: %v", got)
	}
}
