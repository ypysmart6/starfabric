package verification

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
)

// RouteProbe emulates packet forwarding from actual adapter state. Unlike plan
// validation, it follows the routes devices report after Commit.
type RouteProbe struct {
	registry *adapter.Registry
	topology func() model.TopologySnapshot
}

func NewRouteProbe(registry *adapter.Registry, topology func() model.TopologySnapshot) *RouteProbe {
	return &RouteProbe{registry: registry, topology: topology}
}

func (p *RouteProbe) Verify(ctx context.Context, plan model.RoutePlan) error {
	snapshot := p.topology()
	verifiedAt := time.Now().UTC()
	edges := make(map[string]bool, len(snapshot.Links))
	for _, link := range snapshot.Links {
		if link.Usable(verifiedAt, 0) {
			edges[link.Source+"\x00"+link.Target] = true
		}
	}
	states := make(map[string]model.DeviceState)
	names := p.registry.Names()
	// The forwarding walk needs a complete set of observations, but independent
	// device reads need not serialize hundreds of remote round trips.
	for start := 0; start < len(names); start += 8 {
		batch := names[start:min(start+8, len(names))]
		observed := make([]model.DeviceState, len(batch))
		failures := make([]error, len(batch))
		var pending sync.WaitGroup
		for index, name := range batch {
			pending.Add(1)
			go func(index int, name string) {
				defer pending.Done()
				device, _ := p.registry.Get(name)
				observed[index], failures[index] = device.State(ctx)
			}(index, name)
		}
		pending.Wait()
		for index, name := range batch {
			if failures[index] != nil {
				return fmt.Errorf("read %s: %w", name, failures[index])
			}
			states[name] = observed[index]
		}
	}
	for _, intent := range plan.Intents {
		current := intent.Source
		visited := make(map[string]bool)
		for hops := 0; hops <= len(snapshot.Nodes); hops++ {
			if current == intent.Destination {
				break
			}
			if visited[current] {
				return fmt.Errorf("intent %s forwarding loop at %s", intent.ID, current)
			}
			visited[current] = true
			state, exists := states[current]
			if !exists || !state.Healthy {
				return fmt.Errorf("intent %s reached unavailable device %s", intent.ID, current)
			}
			candidates := make([]model.RouteOperation, 0)
			for _, route := range state.Routes {
				if route.Prefix == intent.DestinationPrefix {
					candidates = append(candidates, route)
				}
			}
			if len(candidates) == 0 {
				return fmt.Errorf("intent %s has blackhole at %s", intent.ID, current)
			}
			sort.Slice(candidates, func(i, j int) bool { return candidates[i].Metric < candidates[j].Metric })
			next := candidates[0].NextHopNode
			if !edges[current+"\x00"+next] {
				return fmt.Errorf("intent %s uses unavailable edge %s -> %s", intent.ID, current, next)
			}
			current = next
		}
		if current != intent.Destination {
			return fmt.Errorf("intent %s exceeded hop limit", intent.ID)
		}
	}
	return nil
}

type Multi []interface {
	Verify(context.Context, model.RoutePlan) error
}

func (m Multi) Verify(ctx context.Context, plan model.RoutePlan) error {
	var failures []error
	for _, verifier := range m {
		if err := verifier.Verify(ctx, plan); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}
