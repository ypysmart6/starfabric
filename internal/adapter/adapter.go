package adapter

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

var (
	ErrUnhealthy       = errors.New("device is unhealthy")
	ErrNothingPrepared = errors.New("no matching prepared plan")
)

// DeviceAdapter is the narrow boundary between StarFabric and a network OS.
// Production implementations can target gRIBI/OpenConfig or FRR without
// changing route planning and reconciliation.
type DeviceAdapter interface {
	Name() string
	Health(context.Context) error
	Prepare(context.Context, model.RoutePlan) error
	Commit(context.Context, string) error
	Rollback(context.Context, string) error
	State(context.Context) (model.DeviceState, error)
}

type FaultMode string

const (
	FaultNone    FaultMode = ""
	FaultHealth  FaultMode = "health"
	FaultPrepare FaultMode = "prepare"
	FaultCommit  FaultMode = "commit"
	FaultState   FaultMode = "state"
)

type transaction struct {
	plan           model.RoutePlan
	previous       []model.RouteOperation
	previousPlanID string
}

// MemoryDevice is a deterministic forwarding-device implementation used for
// local runs, CI, shadow control, and failure injection.
type MemoryDevice struct {
	mu            sync.RWMutex
	name          string
	healthy       bool
	fault         FaultMode
	routes        []model.RouteOperation
	appliedPlanID string
	prepared      map[string]transaction
	history       map[string]transaction
	now           func() time.Time
}

func NewMemoryDevice(name string) *MemoryDevice {
	return &MemoryDevice{name: name, healthy: true, prepared: make(map[string]transaction), history: make(map[string]transaction), now: time.Now}
}

func (d *MemoryDevice) Name() string { return d.name }

func (d *MemoryDevice) SetFault(fault FaultMode) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.fault = fault
}

func (d *MemoryDevice) SetHealthy(healthy bool) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.healthy = healthy
}

func (d *MemoryDevice) Health(context.Context) error {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if !d.healthy || d.fault == FaultHealth {
		return ErrUnhealthy
	}
	return nil
}

func (d *MemoryDevice) Prepare(_ context.Context, plan model.RoutePlan) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if !d.healthy {
		return ErrUnhealthy
	}
	if d.fault == FaultPrepare {
		return errors.New("injected prepare failure")
	}
	if plan.ID == "" {
		return errors.New("plan id is required")
	}
	if plan.ID == d.appliedPlanID {
		return nil
	}
	if _, exists := d.prepared[plan.ID]; exists {
		return nil
	}
	routes := make([]model.RouteOperation, 0)
	for _, route := range plan.Routes {
		if route.Device != d.name {
			return fmt.Errorf("adapter %q received route for %q", d.name, route.Device)
		}
		routes = append(routes, route)
	}
	model.SortRoutes(routes)
	d.prepared[plan.ID] = transaction{plan: plan.Clone(), previous: append([]model.RouteOperation(nil), d.routes...), previousPlanID: d.appliedPlanID}
	return nil
}

func (d *MemoryDevice) Commit(_ context.Context, planID string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if !d.healthy {
		return ErrUnhealthy
	}
	if planID == d.appliedPlanID {
		return nil
	}
	if d.fault == FaultCommit {
		return errors.New("injected commit failure")
	}
	tx, ok := d.prepared[planID]
	if !ok {
		return ErrNothingPrepared
	}
	routes := append([]model.RouteOperation(nil), tx.plan.Routes...)
	model.SortRoutes(routes)
	d.history[planID] = transaction{previous: append([]model.RouteOperation(nil), tx.previous...), previousPlanID: tx.previousPlanID}
	d.routes = routes
	d.appliedPlanID = planID
	delete(d.prepared, planID)
	return nil
}

func (d *MemoryDevice) Rollback(_ context.Context, planID string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if tx, exists := d.prepared[planID]; exists {
		d.routes = append([]model.RouteOperation(nil), tx.previous...)
		delete(d.prepared, planID)
		return nil
	}
	if previous, exists := d.history[planID]; exists {
		d.routes = append([]model.RouteOperation(nil), previous.previous...)
		delete(d.history, planID)
		if d.appliedPlanID == planID {
			d.appliedPlanID = previous.previousPlanID
		}
	}
	return nil
}

func (d *MemoryDevice) State(context.Context) (model.DeviceState, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.fault == FaultState {
		return model.DeviceState{}, errors.New("injected state read failure")
	}
	routes := append([]model.RouteOperation(nil), d.routes...)
	return model.DeviceState{Device: d.name, Healthy: d.healthy, AppliedPlanID: d.appliedPlanID, Routes: routes, ObservedAt: d.now().UTC()}, nil
}

type Registry struct {
	mu      sync.RWMutex
	devices map[string]DeviceAdapter
}

func (r *Registry) Close() error {
	r.mu.RLock()
	defer r.mu.RUnlock()
	var result error
	for _, device := range r.devices {
		if closer, ok := device.(interface{ Close() error }); ok {
			result = errors.Join(result, closer.Close())
		}
	}
	return result
}

func NewRegistry(devices ...DeviceAdapter) *Registry {
	registry := &Registry{devices: make(map[string]DeviceAdapter, len(devices))}
	for _, device := range devices {
		registry.devices[device.Name()] = device
	}
	return registry
}

func (r *Registry) Add(device DeviceAdapter) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.devices[device.Name()] = device
}

func (r *Registry) Get(name string) (DeviceAdapter, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	device, ok := r.devices[name]
	return device, ok
}

func (r *Registry) Names() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	names := make([]string, 0, len(r.devices))
	for name := range r.devices {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
