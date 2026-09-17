package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/starfabric/starfabric/internal/constellation"
	"github.com/starfabric/starfabric/internal/scenario"
)

func TestConstellationConfigOverridesAndScenarioCompatibility(t *testing.T) {
	directory := t.TempDir()
	config, output, summary := filepath.Join(directory, "config.json"), filepath.Join(directory, "scenario.json"), filepath.Join(directory, "summary.json")
	if err := os.WriteFile(config, []byte(`{"satellites":120,"gateways":4,"planes":12,"flows":8}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := run([]string{"constellation", "generate", "--config", config, "--satellites", "17", "--gateways", "3", "--planes", "0", "--flows", "0", "--output", output, "--summary", summary}); err != nil {
		t.Fatal(err)
	}
	s, err := scenario.Load(output)
	if err != nil || len(s.Topology.Nodes) != 20 || len(s.Intents) != 6 {
		t.Fatal("explicit overrides did not produce a usable scenario", err)
	}
	data, err := os.ReadFile(summary)
	if err != nil {
		t.Fatal(err)
	}
	var counts constellation.Summary
	if err := json.Unmarshal(data, &counts); err != nil {
		t.Fatal(err)
	}
	if counts.Config.Planes != 1 || counts.TotalNodes != 20 || counts.Config.Flows != 6 {
		t.Fatal("auto defaults were not recalculated", counts)
	}
}

func TestConstellationRejectsBadConfigBeforeWriting(t *testing.T) {
	for _, content := range []string{`{"satellitez":360}`, `null`, `[]`, `{} {}`, `{"satellites":-2}`} {
		directory := t.TempDir()
		config, output := filepath.Join(directory, "config.json"), filepath.Join(directory, "scenario.json")
		if err := os.WriteFile(config, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
		if err := constellationCommand([]string{"generate", "--config", config, "--output", output}); err == nil {
			t.Fatal("invalid config accepted", content)
		}
		if _, err := os.Stat(output); !os.IsNotExist(err) {
			t.Fatal("bad config must not produce a scenario")
		}
	}
	path := filepath.Join(t.TempDir(), "scenario.json")
	if err := constellationCommand([]string{"generate", "--output", path, "--summary", path}); err == nil {
		t.Fatal("summary must not overwrite scenario")
	}
}
