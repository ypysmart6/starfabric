package reconcile

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/validation"
)

type Config struct {
	OperationTimeout time.Duration
	RetryCount       int
	RetryBackoff     time.Duration
	BatchSize        int
	StatePath        string
	Verifier         DataPlaneVerifier
}

type DataPlaneVerifier interface {
	Verify(context.Context, model.RoutePlan) error
}

type persistentState struct {
	Status         model.ReconcileStatus `json:"status"`
	CommittedPlan  *model.RoutePlan      `json:"committed_plan,omitempty"`
	PendingDevices []string              `json:"pending_devices,omitempty"`
}

type Reconciler struct {
	mu             sync.Mutex
	config         Config
	topology       func() model.TopologySnapshot
	registry       *adapter.Registry
	metrics        *observability.Metrics
	logger         *observability.Logger
	status         model.ReconcileStatus
	committedPlan  *model.RoutePlan
	pendingDevices map[string]bool
	verifier       DataPlaneVerifier
	now            func() time.Time
}

func New(config Config, topology func() model.TopologySnapshot, registry *adapter.Registry, metrics *observability.Metrics, logger *observability.Logger) (*Reconciler, error) {
	if config.OperationTimeout <= 0 {
		config.OperationTimeout = 2 * time.Second
	}
	if config.RetryCount < 0 {
		config.RetryCount = 0
	}
	if config.RetryBackoff <= 0 {
		config.RetryBackoff = 25 * time.Millisecond
	}
	if config.BatchSize <= 0 {
		config.BatchSize = 4
	}
	if metrics == nil {
		metrics = observability.NewMetrics()
	}
	if logger == nil {
		logger = observability.NewLogger(ioDiscard{})
	}
	r := &Reconciler{config: config, topology: topology, registry: registry, metrics: metrics, logger: logger, status: model.ReconcileStatus{Phase: model.PhaseIdle}, pendingDevices: make(map[string]bool), verifier: config.Verifier, now: time.Now}
	if config.StatePath != "" {
		if err := r.load(); err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
	}
	return r, nil
}

type ioDiscard struct{}

func (ioDiscard) Write(p []byte) (int, error) { return len(p), nil }

func (r *Reconciler) Status() model.ReconcileStatus {
	r.mu.Lock()
	defer r.mu.Unlock()
	return cloneStatus(r.status)
}

func (r *Reconciler) CommittedPlan() *model.RoutePlan {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.committedPlan == nil {
		return nil
	}
	plan := r.committedPlan.Clone()
	return &plan
}

func (r *Reconciler) Reload() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.config.StatePath == "" {
		return nil
	}
	return r.load()
}

func (r *Reconciler) Apply(ctx context.Context, plan model.RoutePlan) (model.ReconcileStatus, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	existingStatus := cloneStatus(r.status)
	var previousCommitted *model.RoutePlan
	if r.committedPlan != nil {
		copy := r.committedPlan.Clone()
		previousCommitted = &copy
	}
	previousPending := cloneSet(r.pendingDevices)
	started := r.now().UTC()
	r.status = model.ReconcileStatus{Phase: model.PhaseValidating, PlanID: plan.ID, TopologyVersion: plan.TopologyVersion, StartedAt: started}
	r.metrics.Inc("reconcile_total")
	r.log("info", "reconciliation started", nil)
	if err := validation.Plan(plan, r.topology(), started); err != nil {
		return r.fail(err, nil)
	}

	additionalDevices := []string(nil)
	if r.committedPlan != nil {
		seen := make(map[string]bool)
		for _, route := range r.committedPlan.Routes {
			if !seen[route.Device] {
				additionalDevices = append(additionalDevices, route.Device)
				seen[route.Device] = true
			}
		}
	}
	for name := range r.pendingDevices {
		if !contains(additionalDevices, name) {
			additionalDevices = append(additionalDevices, name)
		}
	}
	devicePlans, err := splitPlan(plan, additionalDevices)
	if err != nil {
		return r.fail(err, nil)
	}
	names := make([]string, 0, len(devicePlans))
	for name := range devicePlans {
		names = append(names, name)
	}
	sort.Strings(names)
	if r.committedPlan != nil && r.committedPlan.ID == plan.ID {
		if verifyErr := r.verify(ctx, plan.ID, names, devicePlans); verifyErr == nil {
			r.status = existingStatus
			r.metrics.Inc("reconcile_noop_total")
			return cloneStatus(r.status), nil
		}
		r.logger.Event("warn", "committed plan drift detected; reapplying", map[string]any{"plan_id": plan.ID})
	}
	prepared := make([]string, 0, len(names))
	activeNames := make([]string, 0, len(names))
	deferred := make([]string, 0)
	r.status.Phase = model.PhasePreparing
	programmingStarted := time.Now()
	// Independent device reads may overlap. All reads finish before preparing
	// transactions; commits and their canary gates retain their original order.
	health := r.readDevices(names, func(device adapter.DeviceAdapter) error {
		if err := r.invoke(ctx, func(call context.Context) error { return device.Health(call) }); err != nil {
			return err
		}
		// Refresh the adapter's observed FIB before calculating its delta. A new
		// topology version may coincide with external route withdrawal or restart,
		// so the same-plan drift check above alone is insufficient.
		state, err := invokeValue(r, ctx, func(call context.Context) (model.DeviceState, error) { return device.State(call) })
		if err == nil && !state.Healthy {
			return adapter.ErrUnhealthy
		}
		return err
	})
	for index, name := range names {
		device, exists := r.registry.Get(name)
		if !exists {
			err = fmt.Errorf("no device adapter registered for %q", name)
			r.status.FailedDevices = append(r.status.FailedDevices, name)
			return r.fail(err, prepared)
		}
		if err = health[index]; err != nil && len(devicePlans[name].Routes) == 0 {
			deferred = append(deferred, name)
			r.status.DeferredDevices = append(r.status.DeferredDevices, name)
			r.logger.Event("warn", "route cleanup deferred for unavailable device", map[string]any{"device": name, "plan_id": plan.ID})
			continue
		} else if err == nil {
			devicePlan := devicePlans[name]
			err = r.invoke(ctx, func(call context.Context) error { return device.Prepare(call, devicePlan) })
		}
		if err != nil {
			r.status.FailedDevices = append(r.status.FailedDevices, name)
			return r.fail(fmt.Errorf("prepare %s: %w", name, err), prepared)
		}
		prepared = append(prepared, name)
		activeNames = append(activeNames, name)
		r.status.PreparedDevices = append(r.status.PreparedDevices, name)
	}

	r.status.Phase = model.PhaseCommitting
	for start := 0; start < len(activeNames); start += r.config.BatchSize {
		if err = validation.Plan(plan, r.topology(), r.now().UTC()); err != nil {
			return r.fail(fmt.Errorf("plan became invalid before commit batch: %w", err), prepared)
		}
		end := min(start+r.config.BatchSize, len(activeNames))
		for _, name := range activeNames[start:end] {
			device, _ := r.registry.Get(name)
			if err = r.invoke(ctx, func(call context.Context) error { return device.Commit(call, plan.ID) }); err != nil {
				r.status.FailedDevices = append(r.status.FailedDevices, name)
				return r.fail(fmt.Errorf("commit %s: %w", name, err), prepared)
			}
			r.status.CommittedDevices = append(r.status.CommittedDevices, name)
		}
		// Verification after every batch is the canary safety gate.
		if err = r.verify(ctx, plan.ID, activeNames[start:end], devicePlans); err != nil {
			return r.fail(err, prepared)
		}
	}

	r.status.Phase = model.PhaseVerifying
	if err = validation.Plan(plan, r.topology(), r.now().UTC()); err != nil {
		return r.fail(fmt.Errorf("plan became invalid before final verification: %w", err), prepared)
	}
	if err = r.verify(ctx, plan.ID, activeNames, devicePlans); err != nil {
		return r.fail(err, prepared)
	}
	if r.verifier != nil {
		if err = r.invoke(ctx, func(call context.Context) error { return r.verifier.Verify(call, plan) }); err != nil {
			r.metrics.Inc("data_plane_verification_failure_total")
			return r.fail(fmt.Errorf("data-plane verification: %w", err), prepared)
		}
		r.metrics.Inc("data_plane_verification_success_total")
	}
	r.status.Phase = model.PhaseCommitted
	r.status.FinishedAt = r.now().UTC()
	committed := plan.Clone()
	r.committedPlan = &committed
	r.pendingDevices = make(map[string]bool, len(deferred))
	for _, name := range deferred {
		r.pendingDevices[name] = true
	}
	if persistErr := r.persist(); persistErr != nil {
		r.committedPlan = previousCommitted
		r.pendingDevices = previousPending
		return r.fail(fmt.Errorf("state persistence after device commit: %w", persistErr), prepared)
	}
	r.metrics.Inc("reconcile_success_total")
	r.metrics.Set("desired_actual_mismatch", 0)
	r.metrics.Set("deferred_device_cleanup", float64(len(deferred)))
	r.metrics.ObserveDuration("route_programming_last", programmingStarted)
	r.metrics.ObserveDuration("reconcile_last", started)
	r.log("info", "route plan committed", map[string]any{"devices": len(names)})
	return cloneStatus(r.status), nil
}

func (r *Reconciler) verify(ctx context.Context, planID string, names []string, plans map[string]model.RoutePlan) error {
	results := r.readDevices(names, func(device adapter.DeviceAdapter) error {
		name := device.Name()
		state, err := invokeValue(r, ctx, func(call context.Context) (model.DeviceState, error) { return device.State(call) })
		if err != nil {
			return fmt.Errorf("read state %s: %w", name, err)
		}
		if !state.Healthy || state.AppliedPlanID != planID {
			return fmt.Errorf("device %s did not apply plan %s", name, planID)
		}
		actual := append([]model.RouteOperation(nil), state.Routes...)
		expected := append([]model.RouteOperation(nil), plans[name].Routes...)
		model.SortRoutes(actual)
		model.SortRoutes(expected)
		if !routesEqual(actual, expected) {
			r.metrics.Set("desired_actual_mismatch", 1)
			return fmt.Errorf("device %s desired/actual route mismatch", name)
		}
		return nil
	})
	for _, err := range results {
		if err != nil {
			return err
		}
	}
	return nil
}

// readDevices bounds simultaneous southbound reads by the configured canary
// batch size (and a hard ceiling). Results remain in device order, and no read
// is left running when a failure causes rollback.
func (r *Reconciler) readDevices(names []string, read func(adapter.DeviceAdapter) error) []error {
	results := make([]error, len(names))
	width := min(r.config.BatchSize, 32)
	for start := 0; start < len(names); start += width {
		var pending sync.WaitGroup
		for index := start; index < min(start+width, len(names)); index++ {
			pending.Add(1)
			go func(index int) {
				defer pending.Done()
				device, exists := r.registry.Get(names[index])
				if !exists {
					results[index] = fmt.Errorf("no device adapter registered for %q", names[index])
					return
				}
				results[index] = read(device)
			}(index)
		}
		pending.Wait()
	}
	return results
}

func (r *Reconciler) fail(cause error, rollback []string) (model.ReconcileStatus, error) {
	r.status.Error = cause.Error()
	r.status.RollbackReason = cause.Error()
	rollbackErrs := make([]error, 0)
	for index := len(rollback) - 1; index >= 0; index-- {
		name := rollback[index]
		device, exists := r.registry.Get(name)
		if !exists {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), r.config.OperationTimeout)
		err := device.Rollback(ctx, r.status.PlanID)
		cancel()
		if err != nil {
			rollbackErrs = append(rollbackErrs, fmt.Errorf("rollback %s: %w", name, err))
		}
	}
	r.status.FinishedAt = r.now().UTC()
	if len(rollbackErrs) > 0 {
		r.status.Phase = model.PhaseFailed
		cause = errors.Join(append([]error{cause}, rollbackErrs...)...)
	} else if len(rollback) > 0 {
		r.status.Phase = model.PhaseRolledBack
	} else {
		r.status.Phase = model.PhaseFailed
	}
	r.metrics.Inc("reconcile_failure_total")
	r.metrics.ObserveDuration("reconcile_last", r.status.StartedAt)
	r.log("error", "reconciliation failed", map[string]any{"error": cause.Error(), "phase": r.status.Phase})
	_ = r.persist()
	return cloneStatus(r.status), cause
}

func (r *Reconciler) invoke(parent context.Context, operation func(context.Context) error) error {
	var last error
	for attempt := 0; attempt <= r.config.RetryCount; attempt++ {
		ctx, cancel := context.WithTimeout(parent, r.config.OperationTimeout)
		last = operation(ctx)
		cancel()
		if last == nil {
			return nil
		}
		if attempt < r.config.RetryCount {
			r.metrics.Inc("adapter_retry_total")
			timer := time.NewTimer(r.config.RetryBackoff * time.Duration(1<<attempt))
			select {
			case <-parent.Done():
				timer.Stop()
				return parent.Err()
			case <-timer.C:
			}
		}
	}
	return last
}

func invokeValue[T any](r *Reconciler, parent context.Context, operation func(context.Context) (T, error)) (T, error) {
	var value T
	err := r.invoke(parent, func(ctx context.Context) error {
		var callErr error
		value, callErr = operation(ctx)
		return callErr
	})
	return value, err
}

func splitPlan(plan model.RoutePlan, additionalDevices []string) (map[string]model.RoutePlan, error) {
	out := make(map[string]model.RoutePlan)
	for _, device := range additionalDevices {
		devicePlan := plan.Clone()
		devicePlan.Routes = nil
		out[device] = devicePlan
	}
	for _, route := range plan.Routes {
		if route.Device == "" {
			return nil, errors.New("route device is required")
		}
		devicePlan, exists := out[route.Device]
		if !exists {
			devicePlan = plan.Clone()
			devicePlan.Routes = nil
		}
		devicePlan.Routes = append(devicePlan.Routes, route)
		out[route.Device] = devicePlan
	}
	return out, nil
}

func routesEqual(a, b []model.RouteOperation) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func cloneStatus(status model.ReconcileStatus) model.ReconcileStatus {
	status.PreparedDevices = append([]string(nil), status.PreparedDevices...)
	status.CommittedDevices = append([]string(nil), status.CommittedDevices...)
	status.DeferredDevices = append([]string(nil), status.DeferredDevices...)
	status.FailedDevices = append([]string(nil), status.FailedDevices...)
	return status
}

func (r *Reconciler) log(level, message string, fields map[string]any) {
	if fields == nil {
		fields = make(map[string]any)
	}
	fields["plan_id"] = r.status.PlanID
	fields["topology_version"] = r.status.TopologyVersion
	r.logger.Event(level, message, fields)
}

func (r *Reconciler) persist() error {
	if r.config.StatePath == "" {
		return nil
	}
	pending := make([]string, 0, len(r.pendingDevices))
	for name := range r.pendingDevices {
		pending = append(pending, name)
	}
	sort.Strings(pending)
	return durable.WriteJSON(r.config.StatePath, persistentState{Status: r.status, CommittedPlan: r.committedPlan, PendingDevices: pending}, 0o600)
}

func (r *Reconciler) load() error {
	data, err := os.ReadFile(r.config.StatePath)
	if err != nil {
		return err
	}
	var state persistentState
	if err := json.Unmarshal(data, &state); err != nil {
		return fmt.Errorf("decode reconciler state: %w", err)
	}
	r.status = state.Status
	r.committedPlan = state.CommittedPlan
	r.pendingDevices = make(map[string]bool, len(state.PendingDevices))
	for _, name := range state.PendingDevices {
		r.pendingDevices[name] = true
	}
	return nil
}

func cloneSet(input map[string]bool) map[string]bool {
	result := make(map[string]bool, len(input))
	for key, value := range input {
		result[key] = value
	}
	return result
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
