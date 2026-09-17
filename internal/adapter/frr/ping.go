package frr

import (
	"context"
	"errors"
	"fmt"
	"net/netip"
	"sync"

	"github.com/starfabric/starfabric/internal/model"
)

// PingProbe verifies real packet delivery from source router containers. Node
// labels must define frr_container; destination nodes define probe_target.
type PingProbe struct {
	nodes map[string]model.Node
	agent *Agent
}

func NewAgentPingProbe(nodes []model.Node, agent *Agent) *PingProbe {
	p := NewPingProbe(nodes)
	p.agent = agent
	return p
}

func NewPingProbe(nodes []model.Node) *PingProbe {
	index := make(map[string]model.Node, len(nodes))
	for _, node := range nodes {
		index[node.ID] = node
	}
	return &PingProbe{nodes: index}
}

func (p *PingProbe) Verify(ctx context.Context, plan model.RoutePlan) error {
	type probe struct {
		intentID, node, container, target string
		ipv6                              bool
	}
	probes := make([]probe, 0, len(plan.Intents))
	seen := make(map[string]bool)
	// Validate before launching any subprocess. Multiple services between the
	// same routers share this gateway reachability check; their own payload
	// and encapsulation checks remain separate from PingProbe.
	for _, intent := range plan.Intents {
		source, sourceOK := p.nodes[intent.Source]
		destination, destinationOK := p.nodes[intent.Destination]
		if !sourceOK || !destinationOK || source.Labels["frr_container"] == "" || destination.Labels["probe_target"] == "" {
			return fmt.Errorf("intent %s requires source frr_container and destination probe_target labels", intent.ID)
		}
		target := destination.Labels["probe_target"]
		address, err := netip.ParseAddr(target)
		if err != nil {
			return fmt.Errorf("intent %s probe_target: %w", intent.ID, err)
		}
		key := source.ID + "\x00" + target
		if !seen[key] {
			seen[key] = true
			probes = append(probes, probe{intent.ID, source.ID, source.Labels["frr_container"], target, address.Is6()})
		}
	}
	errs := make([]error, len(probes))
	// Docker exec is expensive: unbounded fan-out can starve the routing
	// daemons and Kubernetes on the shared SIL host.
	const parallel = 8
	for start := 0; start < len(probes); start += parallel {
		if err := ctx.Err(); err != nil {
			return errors.Join(append(errs, err)...)
		}
		var wait sync.WaitGroup
		for index := start; index < min(start+parallel, len(probes)); index++ {
			wait.Add(1)
			go func(index int) {
				defer wait.Done()
				item := probes[index]
				args := []string{"-n", "-c", "1", "-W", "1"}
				if item.ipv6 {
					args = append(args, "-6")
				}
				args = append(args, item.target)
				var runner Runner = NewExecRunner("docker", "exec", item.container, "ping")
				if p.agent != nil {
					runner = p.agent.Runner(item.node, "ping")
				}
				output, err := runner.Run(ctx, args...)
				if err != nil {
					errs[index] = fmt.Errorf("intent %s packet probe failed: %w: %s", item.intentID, err, output)
				}
			}(index)
		}
		wait.Wait()
	}
	return errors.Join(errs...)
}
