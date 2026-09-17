package constellation_test

import (
	"context"
	"io"
	"reflect"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/constellation"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/scenario"
)

func TestConfiguredConstellationsRecoverThroughProductionController(t *testing.T) {
	for _, size := range []struct{ satellites, gateways, planes int }{{120, 4, 12}, {360, 8, 18}, {17, 3, 0}} {
		config := constellation.Defaults()
		config.Satellites, config.Gateways, config.Planes = size.satellites, size.gateways, size.planes
		definition, summary, err := constellation.Generate(config)
		if err != nil {
			t.Fatal(err)
		}
		t.Run(definition.ID, func(t *testing.T) {
			if len(definition.Topology.Nodes) != size.satellites+size.gateways || summary.RealPacketDataPlane || summary.Unified5GIntegration {
				t.Fatal("incorrect topology count or evidence scope", summary)
			}
			// Confirm the advertised failed feeder was actually selected, and
			// both production-planned paths exclude the failed physical link.
			engine := planner.New(planner.Config{PlanTTL: time.Minute})
			initial, err := engine.Build(definition.Topology, definition.Intents, time.Now())
			if err != nil {
				t.Fatal(err)
			}
			fault := definition.Topology.Clone()
			for i := range fault.Links {
				if fault.Links[i].ID == summary.FaultLinkIDs[0] || fault.Links[i].ID == summary.FaultLinkIDs[1] {
					fault.Links[i].OperationalUp = false
				}
			}
			changed, err := engine.Build(fault, definition.Intents, time.Now())
			if err != nil {
				t.Fatal(err)
			}
			intentID := definition.Intents[0].ID
			if reflect.DeepEqual(initial.Paths[intentID][0].Links, changed.Paths[intentID][0].Links) {
				t.Fatal("fault did not change the primary path")
			}
			for _, paths := range changed.Paths {
				if len(paths) != 2 {
					t.Fatal("primary and backup must remain available after failure")
				}
				for _, path := range paths {
					for _, id := range path.Links {
						if id == summary.FaultLinkIDs[0] || id == summary.FaultLinkIDs[1] {
							t.Fatal("failed link remains in selected path")
						}
					}
				}
			}
			runner := scenario.Runner{Config: app.Config{PlanTTL: time.Minute}, Log: io.Discard}
			report, err := runner.Run(context.Background(), definition)
			if err != nil {
				t.Fatal(err)
			}
			if !report.Success || report.FailureCount != 0 || report.RollbackCount != 1 || len(report.Steps) != 10 {
				t.Fatalf("recovery failed: success=%v failures=%d rollbacks=%d steps=%+v", report.Success, report.FailureCount, report.RollbackCount, report.Steps)
			}
			if report.Steps[6].Status == nil || report.Steps[6].Status.Phase != model.PhaseRolledBack || !report.Steps[6].ExpectedFailure {
				t.Fatal("commit failure must trigger rollback, not merely a planning error")
			}
			if report.FinalPlan == nil || !reflect.DeepEqual(initial.Paths, report.FinalPlan.Paths) {
				t.Fatal("restoring topology did not restore the original paths")
			}
			if len(report.DeviceStates) != summary.TotalNodes {
				t.Fatal("experiment did not instantiate every memory device")
			}
		})
	}
}

func TestGenerationRejectsInvalidAndInfeasibleInputs(t *testing.T) {
	for _, change := range []func(*constellation.Config){
		func(c *constellation.Config) { c.Satellites = -1 },
		func(c *constellation.Config) { c.Gateways = 1 },
		func(c *constellation.Config) { c.Planes = 7 },
		func(c *constellation.Config) { c.GatewayUplinks = 2 },
		func(c *constellation.Config) { c.GatewayUplinks = 121 },
		func(c *constellation.Config) { c.Flows = -1 },
		func(c *constellation.Config) { c.ISLLatencyUS = -1 },
		func(c *constellation.Config) { c.DemandBPS = c.CapacityBPS + 1 },
		func(c *constellation.Config) { c.DemandBPS = c.CapacityBPS; c.Flows = 100 },
	} {
		config := constellation.Defaults()
		change(&config)
		if _, _, err := constellation.Generate(config); err == nil {
			t.Fatalf("accepted invalid or infeasible config: %+v", config)
		}
	}
}

func TestTopologyIsReproducibleAndDuplex(t *testing.T) {
	a, _, err := constellation.Generate(constellation.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	b, _, err := constellation.Generate(constellation.Defaults())
	if err != nil || !reflect.DeepEqual(a, b) {
		t.Fatal("same configuration must reproduce the same scenario", err)
	}
	edges := make(map[[2]string]model.Link)
	for _, link := range a.Topology.Links {
		key := [2]string{link.Source, link.Target}
		if _, exists := edges[key]; exists || link.Source == link.Target {
			t.Fatal("duplicate or self link", link)
		}
		edges[key] = link
	}
	for key, link := range edges {
		reverse, exists := edges[[2]string{key[1], key[0]}]
		if !exists || reverse.LatencyUS != link.LatencyUS || reverse.CapacityBPS != link.CapacityBPS {
			t.Fatal("physical link is not represented in both directions", link)
		}
	}
}
