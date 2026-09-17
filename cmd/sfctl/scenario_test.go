package main

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/scenario"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestScenarioPathPreflightChecksBackupFeasibility(t *testing.T) {
	path := filepath.Join(t.TempDir(), "scenario.json")
	value := scenario.Scenario{ID: "physical-preflight", Topology: testutil.Diamond(), Intents: []model.RouteIntent{testutil.Intent()}}
	if err := writeJSONFile(path, value); err != nil {
		t.Fatal(err)
	}
	if err := scenarioCommand([]string{"validate", "--file", path, "--check-paths"}); err != nil {
		t.Fatal(err)
	}
	for i := range value.Topology.Links {
		value.Topology.Links[i].RiskGroups = []string{"common-failure"}
	}
	if err := writeJSONFile(path, value); err != nil {
		t.Fatal(err)
	}
	err := scenarioCommand([]string{"validate", "--file", path, "--check-paths"})
	if err == nil || !strings.Contains(err.Error(), "backup") {
		t.Fatalf("infeasible backup accepted: %v", err)
	}
}
