package policy_test

import (
	"testing"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/policy"
	"github.com/starfabric/starfabric/internal/testutil"
)

type paperPolicy struct{}

func (paperPolicy) Name() string { return "paper-a" }
func (paperPolicy) Compute(snapshot model.TopologySnapshot, intent model.RouteIntent, options policy.Options) (model.Path, error) {
	// A real paper policy owns its calculation; this fixture delegates to the
	// mature latency baseline while proving the typed registration contract.
	return policy.Shortest(snapshot, intent, "latency", options)
}

func TestResearchPolicyPlugin(t *testing.T) {
	registry := policy.NewRegistry()
	if err := registry.Register(paperPolicy{}); err != nil {
		t.Fatal(err)
	}
	intent := testutil.Intent()
	intent.Policy = "paper-a"
	path, err := registry.Compute(intent.Policy, testutil.Diamond(), intent, policy.Options{})
	if err != nil {
		t.Fatal(err)
	}
	if len(path.Nodes) < 2 {
		t.Fatalf("invalid plugin path: %#v", path)
	}
	if err := registry.Register(paperPolicy{}); err == nil {
		t.Fatal("duplicate policy should fail")
	}
}
