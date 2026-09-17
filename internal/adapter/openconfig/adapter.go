package openconfig

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/model"
)

type DeviceConfig struct {
	Name               string
	Management         Endpoint
	GRIBI              Endpoint
	NetworkInstance    string
	RequireGNOITime    bool
	ExternalProgrammer Programmer
	StatePath          string
}

type transaction struct {
	plan           model.RoutePlan
	previous       []model.RouteOperation
	previousPlanID string
}

// Device implements DeviceAdapter with gNMI/gNOI for management and gRIBI for
// route programming. The controller transaction remains target-independent.
type Device struct {
	mu            sync.Mutex
	name          string
	management    *Client
	programmer    Programmer
	requireGNOI   bool
	prepared      map[string]transaction
	history       map[string]transaction
	appliedPlanID string
	current       []model.RouteOperation
	statePath     string
	now           func() time.Time
}

func NewDevice(config DeviceConfig) (*Device, error) {
	if config.Name == "" {
		return nil, errors.New("OpenConfig device name is required")
	}
	management, err := NewClient(config.Management)
	if err != nil {
		return nil, fmt.Errorf("create gNMI/gNOI client: %w", err)
	}
	programmer := config.ExternalProgrammer
	if programmer == nil {
		programmer, err = NewGRIBIProgrammer(config.GRIBI, config.NetworkInstance)
		if err != nil {
			management.Close()
			return nil, fmt.Errorf("create gRIBI client: %w", err)
		}
	}
	device := &Device{
		name: config.Name, management: management, programmer: programmer, requireGNOI: config.RequireGNOITime,
		prepared: make(map[string]transaction), history: make(map[string]transaction), statePath: config.StatePath, now: time.Now,
	}
	if err := device.load(); err != nil {
		device.Close()
		return nil, fmt.Errorf("load OpenConfig adapter state for %s: %w", config.Name, err)
	}
	if rememberer, ok := programmer.(interface {
		RememberRoutes([]model.RouteOperation) error
	}); ok {
		if err := rememberer.RememberRoutes(device.current); err != nil {
			device.Close()
			return nil, fmt.Errorf("restore gRIBI route catalog for %s: %w", config.Name, err)
		}
		for _, tx := range device.history {
			if err := rememberer.RememberRoutes(tx.previous); err != nil {
				device.Close()
				return nil, fmt.Errorf("restore gRIBI rollback catalog for %s: %w", config.Name, err)
			}
		}
	}
	return device, nil
}

func (d *Device) Name() string { return d.name }

func (d *Device) Close() error {
	return errors.Join(d.management.Close(), d.programmer.Close())
}

func (d *Device) Health(ctx context.Context) error {
	if _, err := d.management.Capabilities(ctx); err != nil {
		return fmt.Errorf("gNMI capabilities: %w", err)
	}
	if d.requireGNOI {
		if _, err := d.management.Time(ctx); err != nil {
			return fmt.Errorf("gNOI time: %w", err)
		}
	}
	return nil
}

func (d *Device) Prepare(ctx context.Context, plan model.RoutePlan) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if plan.ID == "" {
		return errors.New("plan id is required")
	}
	if plan.ID == d.appliedPlanID || d.prepared[plan.ID].plan.ID != "" {
		return nil
	}
	for _, route := range plan.Routes {
		if route.Device != d.name {
			return fmt.Errorf("OpenConfig adapter %q received route for %q", d.name, route.Device)
		}
	}
	actual, err := d.programmer.Read(ctx)
	if err != nil {
		return fmt.Errorf("read current gRIBI AFT: %w", err)
	}
	d.prepared[plan.ID] = transaction{plan: plan.Clone(), previous: actual, previousPlanID: d.appliedPlanID}
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
	if err := d.programmer.Apply(ctx, tx.previous, tx.plan.Routes); err != nil {
		return err
	}
	oldPlanID := d.appliedPlanID
	oldCurrent := append([]model.RouteOperation(nil), d.current...)
	d.history[planID] = transaction{previous: append([]model.RouteOperation(nil), tx.previous...), previousPlanID: tx.previousPlanID}
	d.appliedPlanID = planID
	d.current = append([]model.RouteOperation(nil), tx.plan.Routes...)
	if err := d.persist(); err != nil {
		delete(d.history, planID)
		d.appliedPlanID = oldPlanID
		d.current = oldCurrent
		undoErr := d.programmer.Apply(ctx, tx.plan.Routes, tx.previous)
		return errors.Join(fmt.Errorf("persist OpenConfig adapter state: %w", err), undoErr)
	}
	delete(d.prepared, planID)
	return nil
}

func (d *Device) Rollback(ctx context.Context, planID string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if tx, exists := d.prepared[planID]; exists {
		delete(d.prepared, planID)
		_ = tx
		return nil
	}
	tx, exists := d.history[planID]
	if !exists {
		return nil
	}
	actual, err := d.programmer.Read(ctx)
	if err != nil {
		return err
	}
	if err := d.programmer.Apply(ctx, actual, tx.previous); err != nil {
		return err
	}
	currentPlanID := d.appliedPlanID
	currentRoutes := append([]model.RouteOperation(nil), d.current...)
	d.appliedPlanID = tx.previousPlanID
	d.current = append([]model.RouteOperation(nil), tx.previous...)
	delete(d.history, planID)
	if err := d.persist(); err != nil {
		d.history[planID] = tx
		d.appliedPlanID = currentPlanID
		d.current = currentRoutes
		undoErr := d.programmer.Apply(ctx, tx.previous, actual)
		return errors.Join(fmt.Errorf("persist OpenConfig rollback state: %w", err), undoErr)
	}
	return nil
}

func (d *Device) State(ctx context.Context) (model.DeviceState, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	routes, err := d.programmer.Read(ctx)
	if err != nil {
		return model.DeviceState{}, err
	}
	return model.DeviceState{
		Device: d.name, Healthy: true, AppliedPlanID: d.appliedPlanID,
		Routes: routes, ObservedAt: d.now().UTC(), Message: "gRIBI FIB state verified",
	}, nil
}

type persistedTransaction struct {
	Previous       []model.RouteOperation `json:"previous"`
	PreviousPlanID string                 `json:"previous_plan_id,omitempty"`
}

type persistedState struct {
	Schema        int                             `json:"schema"`
	Device        string                          `json:"device"`
	AppliedPlanID string                          `json:"applied_plan_id,omitempty"`
	Current       []model.RouteOperation          `json:"current,omitempty"`
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
		Schema: 1, Device: d.name, AppliedPlanID: d.appliedPlanID,
		Current: append([]model.RouteOperation(nil), d.current...), History: history,
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
	d.appliedPlanID = state.AppliedPlanID
	d.current = append([]model.RouteOperation(nil), state.Current...)
	for planID, tx := range state.History {
		d.history[planID] = transaction{previous: append([]model.RouteOperation(nil), tx.Previous...), previousPlanID: tx.PreviousPlanID}
	}
	return nil
}
