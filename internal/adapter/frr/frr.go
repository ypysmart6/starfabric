// Package frr implements the DeviceAdapter contract with FRRouting's vtysh.
// It is intentionally opt-in: production deployments should prefer gRIBI on
// targets that support it.
package frr

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/model"
)

type Runner interface {
	Run(context.Context, ...string) ([]byte, error)
}

// ExecRunner executes a fixed binary and prefix without invoking a shell.
// Example: NewExecRunner("docker", "exec", "clab-sf-r1", "vtysh").
type ExecRunner struct{ command []string }

func NewExecRunner(command string, prefix ...string) *ExecRunner {
	return &ExecRunner{command: append([]string{command}, prefix...)}
}

func (r *ExecRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, r.command[0], append(r.command[1:], args...)...)
	output, err := command.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("%s failed: %w: %s", r.command[0], err, output)
	}
	return output, nil
}

type transaction struct {
	desired        []model.RouteOperation
	previous       []model.RouteOperation
	previousPlanID string
}

type Device struct {
	mu            sync.RWMutex
	name          string
	runner        Runner
	prepared      map[string]transaction
	routes        []model.RouteOperation
	appliedPlanID string
	history       map[string]transaction
	statePath     string
}

func NewDevice(name string, runner Runner) *Device {
	return &Device{name: name, runner: runner, prepared: make(map[string]transaction), history: make(map[string]transaction)}
}

// NewPersistentDevice restores the last committed route ownership and rollback
// journal. Without this journal a controller restart cannot safely undo a plan
// that was programmed before the restart.
func NewPersistentDevice(name string, runner Runner, statePath string) (*Device, error) {
	device := NewDevice(name, runner)
	device.statePath = statePath
	if err := device.load(); err != nil {
		return nil, fmt.Errorf("load FRR adapter state for %s: %w", name, err)
	}
	return device, nil
}

var _ adapter.DeviceAdapter = (*Device)(nil)

func (d *Device) Name() string { return d.name }

func (d *Device) Health(ctx context.Context) error {
	_, err := d.runner.Run(ctx, "-c", "show version")
	return err
}

func (d *Device) Prepare(_ context.Context, plan model.RoutePlan) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if plan.ID == "" {
		return errors.New("plan id is required")
	}
	if plan.ID == d.appliedPlanID {
		return nil
	}
	if _, exists := d.prepared[plan.ID]; exists {
		return nil
	}
	desired := append([]model.RouteOperation(nil), plan.Routes...)
	for _, route := range desired {
		if route.Device != d.name {
			return fmt.Errorf("adapter %q received route for %q", d.name, route.Device)
		}
		if _, err := netip.ParsePrefix(route.Prefix); err != nil {
			return fmt.Errorf("prefix %q: %w", route.Prefix, err)
		}
		if _, err := netip.ParseAddr(route.NextHop); err != nil {
			return fmt.Errorf("next hop %q: %w", route.NextHop, err)
		}
		if route.Metric < 1 || route.Metric > 255 {
			return fmt.Errorf("route distance %d is outside FRR range 1..255", route.Metric)
		}
	}
	model.SortRoutes(desired)
	d.prepared[plan.ID] = transaction{desired: desired, previous: append([]model.RouteOperation(nil), d.routes...), previousPlanID: d.appliedPlanID}
	return nil
}

func (d *Device) Commit(ctx context.Context, planID string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if planID == d.appliedPlanID {
		return nil
	}
	tx, exists := d.prepared[planID]
	if !exists {
		return adapter.ErrNothingPrepared
	}
	if err := d.apply(ctx, d.routes, tx.desired); err != nil {
		return err
	}
	oldRoutes := append([]model.RouteOperation(nil), d.routes...)
	oldPlanID := d.appliedPlanID
	d.history[planID] = transaction{previous: append([]model.RouteOperation(nil), tx.previous...), previousPlanID: tx.previousPlanID}
	d.routes = append([]model.RouteOperation(nil), tx.desired...)
	d.appliedPlanID = planID
	if err := d.persist(); err != nil {
		delete(d.history, planID)
		d.routes = oldRoutes
		d.appliedPlanID = oldPlanID
		undoErr := d.apply(ctx, tx.desired, oldRoutes)
		return errors.Join(fmt.Errorf("persist FRR adapter state: %w", err), undoErr)
	}
	delete(d.prepared, planID)
	return nil
}

func (d *Device) Rollback(ctx context.Context, planID string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, exists := d.prepared[planID]; exists {
		delete(d.prepared, planID)
		return nil
	}
	previous, exists := d.history[planID]
	if !exists {
		return nil
	}
	current := append([]model.RouteOperation(nil), d.routes...)
	currentPlanID := d.appliedPlanID
	if err := d.apply(ctx, current, previous.previous); err != nil {
		return err
	}
	d.routes = append([]model.RouteOperation(nil), previous.previous...)
	d.appliedPlanID = previous.previousPlanID
	delete(d.history, planID)
	if err := d.persist(); err != nil {
		d.history[planID] = previous
		d.routes = current
		d.appliedPlanID = currentPlanID
		undoErr := d.apply(ctx, previous.previous, current)
		return errors.Join(fmt.Errorf("persist FRR rollback state: %w", err), undoErr)
	}
	return nil
}

func (d *Device) State(ctx context.Context) (model.DeviceState, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	expected := append([]model.RouteOperation(nil), d.routes...)
	planID := d.appliedPlanID
	observed := make([]model.RouteOperation, 0, len(expected))
	byFamily := map[bool][]model.RouteOperation{true: {}, false: {}}
	for _, route := range expected {
		prefix, _ := netip.ParsePrefix(route.Prefix)
		byFamily[prefix.Addr().Is6()] = append(byFamily[prefix.Addr().Is6()], route)
	}
	for ipv6, routes := range byFamily {
		if len(routes) == 0 {
			continue
		}
		command := "show ip route static json"
		if ipv6 {
			command = "show ipv6 route static json"
		}
		output, err := d.runner.Run(ctx, "-c", command)
		if err != nil {
			return model.DeviceState{}, err
		}
		installed, err := decodeInstalled(output)
		if err != nil {
			return model.DeviceState{}, fmt.Errorf("decode %s route state: %w", d.name, err)
		}
		for _, route := range routes {
			if installed[route.Prefix][route.NextHop] {
				observed = append(observed, route)
			}
		}
	}
	model.SortRoutes(observed)
	if len(observed) != len(expected) {
		planID = ""
		// A configured static route can disappear from the RIB while its
		// interface is down. Retain ownership so a later plan can remove it;
		// otherwise it unexpectedly returns when that interface recovers.
		d.appliedPlanID = ""
		if err := d.persist(); err != nil {
			return model.DeviceState{}, fmt.Errorf("persist observed FRR drift: %w", err)
		}
	}
	return model.DeviceState{Device: d.name, Healthy: true, AppliedPlanID: planID, Routes: observed, ObservedAt: time.Now().UTC()}, nil
}

type persistedTransaction struct {
	Previous       []model.RouteOperation `json:"previous"`
	PreviousPlanID string                 `json:"previous_plan_id,omitempty"`
}

type persistedState struct {
	Schema        int                             `json:"schema"`
	Device        string                          `json:"device"`
	Routes        []model.RouteOperation          `json:"routes"`
	AppliedPlanID string                          `json:"applied_plan_id,omitempty"`
	History       map[string]persistedTransaction `json:"history,omitempty"`
}

func (d *Device) persist() error {
	if d.statePath == "" {
		return nil
	}
	history := make(map[string]persistedTransaction, len(d.history))
	for planID, tx := range d.history {
		history[planID] = persistedTransaction{Previous: append([]model.RouteOperation(nil), tx.previous...), PreviousPlanID: tx.previousPlanID}
	}
	return durable.WriteJSON(d.statePath, persistedState{
		Schema: 1, Device: d.name, Routes: append([]model.RouteOperation(nil), d.routes...),
		AppliedPlanID: d.appliedPlanID, History: history,
	}, 0o640)
}

func (d *Device) load() error {
	if d.statePath == "" {
		return nil
	}
	data, err := os.ReadFile(d.statePath)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var state persistedState
	if err := json.Unmarshal(data, &state); err != nil {
		return err
	}
	if state.Schema != 1 || state.Device != d.name {
		return fmt.Errorf("invalid state schema/device %d/%q", state.Schema, state.Device)
	}
	d.routes = append([]model.RouteOperation(nil), state.Routes...)
	d.appliedPlanID = state.AppliedPlanID
	for planID, tx := range state.History {
		d.history[planID] = transaction{previous: append([]model.RouteOperation(nil), tx.Previous...), previousPlanID: tx.PreviousPlanID}
	}
	return nil
}

func (d *Device) apply(ctx context.Context, current, desired []model.RouteOperation) error {
	currentSet := routeSet(current)
	desiredSet := routeSet(desired)
	commands := []string{"-c", "configure terminal"}
	for _, route := range current {
		if !desiredSet[routeKey(route)] {
			commands = append(commands, "-c", render(route, true))
		}
	}
	for _, route := range desired {
		if !currentSet[routeKey(route)] || d.appliedPlanID == "" {
			// Reassert desired entries after observed drift, including entries
			// still owned in the journal but actually deleted from FRR.
			commands = append(commands, "-c", render(route, false))
		}
	}
	commands = append(commands, "-c", "end")
	if len(commands) == 4 { // configure terminal + end only
		return nil
	}
	_, err := d.runner.Run(ctx, commands...)
	return err
}

func render(route model.RouteOperation, remove bool) string {
	prefix, _ := netip.ParsePrefix(route.Prefix)
	command := "ip route"
	if prefix.Addr().Is6() {
		command = "ipv6 route"
	}
	if remove {
		command = "no " + command
	}
	return command + " " + route.Prefix + " " + route.NextHop + " " + strconv.Itoa(route.Metric)
}

func routeKey(route model.RouteOperation) string {
	return route.Prefix + "\x00" + route.NextHop + "\x00" + strconv.Itoa(route.Metric)
}

func routeSet(routes []model.RouteOperation) map[string]bool {
	set := make(map[string]bool, len(routes))
	for _, route := range routes {
		set[routeKey(route)] = true
	}
	return set
}

type routeEntry struct {
	Nexthops []struct {
		IP string `json:"ip"`
	} `json:"nexthops"`
}

func decodeInstalled(data []byte) (map[string]map[string]bool, error) {
	var table map[string][]routeEntry
	if err := json.Unmarshal(data, &table); err != nil {
		return nil, err
	}
	result := make(map[string]map[string]bool, len(table))
	prefixes := make([]string, 0, len(table))
	for prefix := range table {
		prefixes = append(prefixes, prefix)
	}
	sort.Strings(prefixes)
	for _, prefix := range prefixes {
		result[prefix] = make(map[string]bool)
		for _, entry := range table[prefix] {
			for _, nextHop := range entry.Nexthops {
				result[prefix][nextHop.IP] = true
			}
		}
	}
	return result, nil
}
