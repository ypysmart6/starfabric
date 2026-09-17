package planner

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"sort"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/policy"
)

type Config struct {
	PlanTTL         time.Duration
	MaxTelemetryAge time.Duration
	NodeDisjoint    bool
	Policies        *policy.Registry
}

type Planner struct {
	config   Config
	policies *policy.Registry
}

func New(config Config) *Planner {
	if config.PlanTTL <= 0 {
		config.PlanTTL = 30 * time.Second
	}
	if config.Policies == nil {
		config.Policies = policy.NewRegistry()
	}
	return &Planner{config: config, policies: config.Policies}
}

func (p *Planner) Build(snapshot model.TopologySnapshot, intents []model.RouteIntent, now time.Time) (model.RoutePlan, error) {
	if err := snapshot.Validate(); err != nil {
		return model.RoutePlan{}, err
	}
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if !snapshot.ValidUntil.IsZero() && !now.Before(snapshot.ValidUntil) {
		return model.RoutePlan{}, errors.New("topology snapshot has expired")
	}
	nodes := make(map[string]model.Node, len(snapshot.Nodes))
	for _, node := range snapshot.Nodes {
		nodes[node.ID] = node
	}
	plan := model.RoutePlan{
		TopologyVersion: snapshot.Version,
		CreatedAt:       now,
		ExpiresAt:       now.Add(p.config.PlanTTL),
		Policy:          "per-intent",
		Intents:         nil,
		Paths:           make(map[string][]model.Path, len(intents)),
		Preconditions:   []string{"topology-version-match", "next-hop-reachable", "plan-not-expired", "loop-free"},
		Metadata:        map[string]interface{}{"selected_gateways": map[string]string{}},
	}
	linksByID := make(map[string]model.Link, len(snapshot.Links))
	for _, link := range snapshot.Links {
		linksByID[link.ID] = link
	}
	orderedIntents := append([]model.RouteIntent(nil), intents...)
	sort.SliceStable(orderedIntents, func(i, j int) bool {
		if orderedIntents[i].Priority != orderedIntents[j].Priority {
			return orderedIntents[i].Priority > orderedIntents[j].Priority
		}
		return orderedIntents[i].ID < orderedIntents[j].ID
	})
	residual := snapshot.Clone()
	reservations := make(map[string]int64)
	for _, intent := range orderedIntents {
		if err := intent.Validate(); err != nil {
			return model.RoutePlan{}, fmt.Errorf("intent %q: %w", intent.ID, err)
		}
		if _, err := netip.ParsePrefix(intent.DestinationPrefix); err != nil {
			return model.RoutePlan{}, fmt.Errorf("intent %q destination prefix: %w", intent.ID, err)
		}
		selectedIntent, primary, err := p.selectDestination(residual, intent, now)
		if err != nil {
			return model.RoutePlan{}, fmt.Errorf("intent %q: %w", intent.ID, err)
		}
		intent = selectedIntent
		plan.Intents = append(plan.Intents, intent)
		if len(intent.GatewayCandidates) > 0 {
			plan.Metadata["selected_gateways"].(map[string]string)[intent.ID] = intent.Destination
		}
		paths := []model.Path{primary}
		if intent.Redundancy > 0 {
			engine, exists := p.policies.Policy(intent.Policy)
			if !exists {
				return model.RoutePlan{}, fmt.Errorf("intent %q: unknown path policy %q", intent.ID, intent.Policy)
			}
			options := policy.Options{
				At: now, MaxTelemetryAge: p.config.MaxTelemetryAge,
				ExcludedLinks: make(map[string]bool), ExcludedNodes: make(map[string]bool), ExcludedRiskGroups: make(map[string]bool),
			}
			previous := primary
			for backupIndex := 0; backupIndex < intent.Redundancy; backupIndex++ {
				backup, backupErr := policy.DisjointWithPolicy(engine, residual, intent, previous, p.config.NodeDisjoint, options)
				if backupErr != nil {
					return model.RoutePlan{}, fmt.Errorf("intent %q backup %d: %w", intent.ID, backupIndex+1, backupErr)
				}
				paths = append(paths, backup)
				previous = backup
			}
		}
		plan.Paths[intent.ID] = paths
		if intent.DemandBPS > 0 {
			for _, linkID := range primary.Links {
				for i := range residual.Links {
					if residual.Links[i].ID == linkID {
						residual.Links[i].CapacityBPS -= intent.DemandBPS
						reservations[linkID] += intent.DemandBPS
						break
					}
				}
			}
		}
		for _, candidatePath := range paths {
			for _, linkID := range candidatePath.Links {
				link := linksByID[linkID]
				for _, end := range []time.Time{link.ValidUntil, link.AvailabilityEnd} {
					if !end.IsZero() && end.Before(plan.ExpiresAt) {
						plan.ExpiresAt = end
					}
				}
			}
		}
		for pathIndex, path := range paths {
			role := "primary"
			metric := 100
			if pathIndex > 0 {
				role = "backup"
				metric = 200 + pathIndex
				if intent.Policy == "ecmp" && path.Cost == primary.Cost {
					role = "ecmp"
					metric = 100
				}
			}
			for i := 0; i+1 < len(path.Nodes); i++ {
				nextHop, exists := nodes[path.Nodes[i+1]]
				if !exists {
					return model.RoutePlan{}, fmt.Errorf("unknown next-hop node %q", path.Nodes[i+1])
				}
				address, err := nodes[path.Nodes[i]].NextHopAddress(nextHop)
				if err != nil {
					return model.RoutePlan{}, err
				}
				plan.Routes = append(plan.Routes, model.RouteOperation{
					Device: path.Nodes[i], Prefix: intent.DestinationPrefix, NextHop: address,
					NextHopNode: nextHop.ID, Metric: metric, IntentID: intent.ID, PathRole: role,
				})
			}
		}
	}
	plan.Metadata["capacity_reservations_bps"] = reservations
	model.SortRoutes(plan.Routes)
	if !snapshot.ValidUntil.IsZero() && snapshot.ValidUntil.Before(plan.ExpiresAt) {
		plan.ExpiresAt = snapshot.ValidUntil
	}
	if !now.Before(plan.ExpiresAt) {
		return model.RoutePlan{}, errors.New("computed route has no remaining contact-window lifetime")
	}
	plan.ID = planID(plan)
	return plan, nil
}

func (p *Planner) selectDestination(snapshot model.TopologySnapshot, intent model.RouteIntent, now time.Time) (model.RouteIntent, model.Path, error) {
	candidates := append([]string(nil), intent.GatewayCandidates...)
	if intent.Destination != "" {
		candidates = append(candidates, intent.Destination)
	}
	if len(candidates) == 0 {
		return intent, model.Path{}, errors.New("no destination candidate")
	}
	sort.Strings(candidates)
	var selected model.Path
	selectedDestination := ""
	var failures []error
	for _, destination := range candidates {
		candidateIntent := intent
		candidateIntent.Destination = destination
		path, err := p.policies.Compute(intent.Policy, snapshot, candidateIntent, policy.Options{At: now, MaxTelemetryAge: p.config.MaxTelemetryAge})
		if err != nil {
			failures = append(failures, fmt.Errorf("%s: %w", destination, err))
			continue
		}
		if selectedDestination == "" || path.Cost < selected.Cost || (path.Cost == selected.Cost && destination < selectedDestination) {
			selected, selectedDestination = path, destination
		}
	}
	if selectedDestination == "" {
		return intent, model.Path{}, fmt.Errorf("no reachable destination: %w", errors.Join(failures...))
	}
	intent.Destination = selectedDestination
	return intent, selected, nil
}

func planID(plan model.RoutePlan) string {
	payload := struct {
		TopologyVersion uint64                  `json:"topology_version"`
		Intents         []model.RouteIntent     `json:"intents"`
		Paths           map[string][]model.Path `json:"paths"`
		Routes          []model.RouteOperation  `json:"routes"`
	}{plan.TopologyVersion, plan.Intents, plan.Paths, plan.Routes}
	data, _ := json.Marshal(payload)
	digest := sha256.Sum256(data)
	return "rp-" + hex.EncodeToString(digest[:])[:16]
}
