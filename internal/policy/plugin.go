package policy

import (
	"fmt"
	"sync"

	"github.com/starfabric/starfabric/internal/model"
)

// PathPolicy is the stable extension point for deterministic research or
// product policies. Implementations receive the same validated topology,
// intent and exclusions as built-ins; learned/adaptive policies are outside
// this deterministic repository.
type PathPolicy interface {
	Name() string
	Compute(model.TopologySnapshot, model.RouteIntent, Options) (model.Path, error)
}

type costPolicy string

func (p costPolicy) Name() string { return string(p) }
func (p costPolicy) Compute(snapshot model.TopologySnapshot, intent model.RouteIntent, options Options) (model.Path, error) {
	return Shortest(snapshot, intent, string(p), options)
}

type Registry struct {
	mu       sync.RWMutex
	policies map[string]PathPolicy
}

func NewRegistry(extra ...PathPolicy) *Registry {
	registry := &Registry{policies: make(map[string]PathPolicy)}
	for _, name := range []string{"latency", "ecmp", "hops", "capacity", "reliability"} {
		registry.policies[name] = costPolicy(name)
	}
	for _, candidate := range extra {
		_ = registry.Register(candidate)
	}
	return registry
}

func (r *Registry) Register(candidate PathPolicy) error {
	if candidate == nil || candidate.Name() == "" {
		return fmt.Errorf("policy and non-empty policy name are required")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, exists := r.policies[candidate.Name()]; exists {
		return fmt.Errorf("policy %q already registered", candidate.Name())
	}
	r.policies[candidate.Name()] = candidate
	return nil
}

func (r *Registry) Compute(name string, snapshot model.TopologySnapshot, intent model.RouteIntent, options Options) (model.Path, error) {
	if name == "" {
		name = "latency"
	}
	r.mu.RLock()
	candidate, exists := r.policies[name]
	r.mu.RUnlock()
	if !exists {
		return model.Path{}, fmt.Errorf("unknown path policy %q", name)
	}
	return candidate.Compute(snapshot, intent, options)
}

func DisjointWithPolicy(engine PathPolicy, snapshot model.TopologySnapshot, intent model.RouteIntent, primary model.Path, nodeDisjoint bool, options Options) (model.Path, error) {
	if options.ExcludedLinks == nil {
		options.ExcludedLinks = make(map[string]bool)
	}
	for _, id := range primary.Links {
		options.ExcludedLinks[id] = true
	}
	primaryEdges := make(map[string]bool)
	for i := 0; i+1 < len(primary.Nodes); i++ {
		primaryEdges[primary.Nodes[i]+"\x00"+primary.Nodes[i+1]] = true
		primaryEdges[primary.Nodes[i+1]+"\x00"+primary.Nodes[i]] = true
	}
	for _, link := range snapshot.Links {
		if primaryEdges[link.Source+"\x00"+link.Target] {
			options.ExcludedLinks[link.ID] = true
		}
	}
	if options.ExcludedRiskGroups == nil {
		options.ExcludedRiskGroups = make(map[string]bool)
	}
	for _, group := range primary.RiskGroups {
		options.ExcludedRiskGroups[group] = true
	}
	if nodeDisjoint {
		if options.ExcludedNodes == nil {
			options.ExcludedNodes = make(map[string]bool)
		}
		for _, node := range primary.Nodes[1 : len(primary.Nodes)-1] {
			options.ExcludedNodes[node] = true
		}
	}
	return engine.Compute(snapshot, intent, options)
}

func (r *Registry) Policy(name string) (PathPolicy, bool) {
	if name == "" {
		name = "latency"
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	candidate, exists := r.policies[name]
	return candidate, exists
}
