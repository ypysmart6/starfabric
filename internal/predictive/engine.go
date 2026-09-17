// Package predictive turns contact windows into future topology snapshots and
// shadow-validates route plans before their activation time.
package predictive

import (
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/validation"
)

type ContactWindow struct {
	Link  model.Link `json:"link"`
	Start time.Time  `json:"start"`
	End   time.Time  `json:"end"`
}

func (w ContactWindow) Validate() error {
	if w.Link.ID == "" || w.Link.Source == "" || w.Link.Target == "" {
		return errors.New("contact link id, source, and target are required")
	}
	if w.Start.IsZero() || !w.End.After(w.Start) {
		return errors.New("contact end must be after a non-zero start")
	}
	return nil
}

type Activation struct {
	TopologyAt      time.Time              `json:"topology_at"`
	ActivateAt      time.Time              `json:"activate_at"`
	Topology        model.TopologySnapshot `json:"topology"`
	Plan            model.RoutePlan        `json:"plan"`
	ShadowValidated bool                   `json:"shadow_validated"`
	ChangedPrefixes []string               `json:"changed_prefixes,omitempty"`
}

type Engine struct {
	Planner *planner.Planner
	Lead    time.Duration
}

// Schedule returns the current plan plus every future contact boundary in the
// horizon. Future plans are computed against their effective topology, then
// validated without touching a device. Activation is Lead before the boundary.
func (e Engine) Schedule(base model.TopologySnapshot, windows []ContactWindow, intents []model.RouteIntent, now time.Time, horizon time.Duration) ([]Activation, error) {
	if e.Planner == nil {
		return nil, errors.New("predictive engine requires a planner")
	}
	if horizon <= 0 {
		return nil, errors.New("predictive horizon must be positive")
	}
	if e.Lead < 0 {
		return nil, errors.New("predictive activation lead cannot be negative")
	}
	for index, window := range windows {
		if err := window.Validate(); err != nil {
			return nil, fmt.Errorf("contact window %d: %w", index, err)
		}
	}
	boundaries := []time.Time{now}
	limit := now.Add(horizon)
	for _, window := range windows {
		for _, boundary := range []time.Time{window.Start, window.End} {
			if boundary.After(now) && !boundary.After(limit) {
				boundaries = append(boundaries, boundary)
			}
		}
	}
	sort.Slice(boundaries, func(i, j int) bool { return boundaries[i].Before(boundaries[j]) })
	boundaries = uniqueTimes(boundaries)
	var output []Activation
	var previous *model.RoutePlan
	for index, at := range boundaries {
		next := time.Time{}
		if index+1 < len(boundaries) {
			next = boundaries[index+1]
		}
		snapshot, err := SnapshotAt(base, windows, at, next, base.Version+uint64(index))
		if err != nil {
			return nil, err
		}
		plan, err := e.Planner.Build(snapshot, intents, at)
		if err != nil {
			return nil, fmt.Errorf("build forecast at %s: %w", at.Format(time.RFC3339Nano), err)
		}
		if err := validation.Plan(plan, snapshot, at); err != nil {
			return nil, fmt.Errorf("shadow validation at %s: %w", at.Format(time.RFC3339Nano), err)
		}
		// Even identical forwarding needs the new contact snapshot: otherwise
		// an expired snapshot remains active and later reconciliation fails.
		activateAt := at
		if at.After(now) {
			activateAt = at.Add(-e.Lead)
			if activateAt.Before(now) {
				activateAt = now
			}
		}
		activation := Activation{TopologyAt: at, ActivateAt: activateAt, Topology: snapshot, Plan: plan, ShadowValidated: true}
		if previous != nil {
			activation.ChangedPrefixes = changedPrefixes(*previous, plan)
		}
		output = append(output, activation)
		copy := plan.Clone()
		previous = &copy
	}
	return output, nil
}

func SnapshotAt(base model.TopologySnapshot, windows []ContactWindow, at, validUntil time.Time, version uint64) (model.TopologySnapshot, error) {
	snapshot := base.Clone()
	snapshot.Version = version
	snapshot.GeneratedAt = at
	snapshot.ValidFrom = at
	snapshot.ValidUntil = validUntil
	byID := make(map[string]int, len(snapshot.Links))
	for index := range snapshot.Links {
		byID[snapshot.Links[index].ID] = index
	}
	windowIDs := make(map[string]bool)
	for _, window := range windows {
		if err := window.Validate(); err != nil {
			return model.TopologySnapshot{}, err
		}
		windowIDs[window.Link.ID] = true
		index, exists := byID[window.Link.ID]
		if !exists {
			snapshot.Links = append(snapshot.Links, window.Link)
			index = len(snapshot.Links) - 1
			byID[window.Link.ID] = index
		}
		if !at.Before(window.Start) && at.Before(window.End) {
			link := window.Link
			link.AdminUp = true
			link.OperationalUp = true
			link.ValidUntil = window.End
			link.AvailabilityEnd = window.End
			snapshot.Links[index] = link
		}
	}
	for id := range windowIDs {
		index := byID[id]
		active := false
		for _, window := range windows {
			if window.Link.ID == id && !at.Before(window.Start) && at.Before(window.End) {
				active = true
				break
			}
		}
		if !active {
			snapshot.Links[index].OperationalUp = false
		}
	}
	if err := snapshot.Validate(); err != nil {
		return model.TopologySnapshot{}, err
	}
	return snapshot, nil
}

func uniqueTimes(input []time.Time) []time.Time {
	output := input[:0]
	for _, value := range input {
		if len(output) == 0 || !value.Equal(output[len(output)-1]) {
			output = append(output, value)
		}
	}
	return output
}

func changedPrefixes(a, b model.RoutePlan) []string {
	changed := make(map[string]bool)
	left, right := make(map[string][]model.RouteOperation), make(map[string][]model.RouteOperation)
	for _, route := range a.Routes {
		left[route.Prefix] = append(left[route.Prefix], route)
	}
	for _, route := range b.Routes {
		right[route.Prefix] = append(right[route.Prefix], route)
	}
	for prefix := range left {
		if !routeSlicesEqual(left[prefix], right[prefix]) {
			changed[prefix] = true
		}
	}
	for prefix := range right {
		if !routeSlicesEqual(left[prefix], right[prefix]) {
			changed[prefix] = true
		}
	}
	output := make([]string, 0, len(changed))
	for prefix := range changed {
		output = append(output, prefix)
	}
	sort.Strings(output)
	return output
}

func routeSlicesEqual(a, b []model.RouteOperation) bool {
	if len(a) != len(b) {
		return false
	}
	a, b = append([]model.RouteOperation(nil), a...), append([]model.RouteOperation(nil), b...)
	model.SortRoutes(a)
	model.SortRoutes(b)
	for index := range a {
		if a[index] != b[index] {
			return false
		}
	}
	return true
}
