package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/reconcile"
	"github.com/starfabric/starfabric/internal/topology"
	"github.com/starfabric/starfabric/internal/verification"
)

var ErrNotLeader = errors.New("controller is not the active leader")

type Config struct {
	TopologyStatePath   string
	ReconcileStatePath  string
	IntentsStatePath    string
	PlanTTL             time.Duration
	MaxTelemetryAge     time.Duration
	NodeDisjoint        bool
	OperationTimeout    time.Duration
	RetryCount          int
	RetryBackoff        time.Duration
	BatchSize           int
	DeviceFactory       func(model.Node) (adapter.DeviceAdapter, error)
	DataPlaneVerifier   reconcile.DataPlaneVerifier
	CanMutate           func() bool
	PredictiveStatePath string
}

// App composes StarFabric's state, policy, adapters, and reconciliation loop.
// The in-memory device adapters make it a complete executable digital twin;
// network-OS adapters plug into the same registry.
type App struct {
	mu             sync.RWMutex
	topology       *topology.Store
	intents        []model.RouteIntent
	intentsPath    string
	planner        *planner.Planner
	reconciler     *reconcile.Reconciler
	registry       *adapter.Registry
	devices        map[string]*adapter.MemoryDevice
	factory        func(model.Node) (adapter.DeviceAdapter, error)
	metrics        *observability.Metrics
	logger         *observability.Logger
	now            func() time.Time
	canMutate      func() bool
	lastEvent      time.Time
	predictivePath string
	scheduleMu     sync.RWMutex
	schedules      map[string]PredictiveSchedule
	ctx            context.Context
	cancel         context.CancelFunc
	workers        sync.WaitGroup
}

func New(config Config, initial model.TopologySnapshot, intents []model.RouteIntent, logWriter io.Writer) (*App, error) {
	store, err := topology.New(initial, config.TopologyStatePath)
	if err != nil {
		return nil, err
	}
	return newWithStore(config, store, intents, logWriter)
}

func Load(config Config, fallback model.TopologySnapshot, intents []model.RouteIntent, logWriter io.Writer) (*App, error) {
	var store *topology.Store
	var err error
	if config.TopologyStatePath != "" {
		store, err = topology.Load(config.TopologyStatePath)
	}
	if errors.Is(err, os.ErrNotExist) || config.TopologyStatePath == "" {
		store, err = topology.New(fallback, config.TopologyStatePath)
	}
	if err != nil {
		return nil, err
	}
	return newWithStore(config, store, intents, logWriter)
}

func newWithStore(config Config, store *topology.Store, intents []model.RouteIntent, logWriter io.Writer) (*App, error) {
	if logWriter == nil {
		logWriter = io.Discard
	}
	loadedIntents, err := loadIntents(config.IntentsStatePath, intents)
	if err != nil {
		return nil, err
	}
	intents = loadedIntents
	for _, intent := range intents {
		if err := intent.Validate(); err != nil {
			return nil, fmt.Errorf("intent %q: %w", intent.ID, err)
		}
	}
	metrics := observability.NewMetrics()
	logger := observability.NewLogger(logWriter)
	registry := adapter.NewRegistry()
	devices := make(map[string]*adapter.MemoryDevice)
	factory := config.DeviceFactory
	if factory == nil {
		factory = func(node model.Node) (adapter.DeviceAdapter, error) {
			device := adapter.NewMemoryDevice(node.ID)
			device.SetHealthy(node.Enabled)
			return device, nil
		}
	}
	for _, node := range store.Snapshot().Nodes {
		device, factoryErr := factory(node)
		if factoryErr != nil {
			return nil, fmt.Errorf("create adapter for %s: %w", node.ID, factoryErr)
		}
		registry.Add(device)
		if simulated, ok := device.(*adapter.MemoryDevice); ok {
			devices[node.ID] = simulated
		}
	}
	verifiers := verification.Multi{verification.NewRouteProbe(registry, store.Snapshot)}
	if config.DataPlaneVerifier != nil {
		verifiers = append(verifiers, config.DataPlaneVerifier)
	}
	r, err := reconcile.New(reconcile.Config{
		OperationTimeout: config.OperationTimeout, RetryCount: config.RetryCount,
		RetryBackoff: config.RetryBackoff, BatchSize: config.BatchSize, StatePath: config.ReconcileStatePath, Verifier: verifiers,
	}, store.Snapshot, registry, metrics, logger)
	if err != nil {
		return nil, err
	}
	appContext, cancel := context.WithCancel(context.Background())
	app := &App{
		topology: store, intents: cloneIntents(intents), intentsPath: config.IntentsStatePath,
		planner:    planner.New(planner.Config{PlanTTL: config.PlanTTL, MaxTelemetryAge: config.MaxTelemetryAge, NodeDisjoint: config.NodeDisjoint}),
		reconciler: r, registry: registry, devices: devices, factory: factory, metrics: metrics, logger: logger, now: time.Now,
		canMutate:      config.CanMutate,
		predictivePath: config.PredictiveStatePath, schedules: make(map[string]PredictiveSchedule), ctx: appContext, cancel: cancel,
	}
	if err := app.loadPredictiveSchedules(); err != nil {
		cancel()
		_ = registry.Close()
		return nil, err
	}
	app.refreshMetrics()
	app.resumePredictiveSchedules()
	return app, nil
}

func (a *App) Topology() model.TopologySnapshot { return a.topology.Snapshot() }

func (a *App) Intents() []model.RouteIntent {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return cloneIntents(a.intents)
}

func (a *App) SetIntents(intents []model.RouteIntent) error {
	if !a.IsLeader() {
		return ErrNotLeader
	}
	ids := make(map[string]bool, len(intents))
	for _, intent := range intents {
		if err := intent.Validate(); err != nil {
			return fmt.Errorf("intent %q: %w", intent.ID, err)
		}
		if ids[intent.ID] {
			return fmt.Errorf("duplicate intent %q", intent.ID)
		}
		ids[intent.ID] = true
	}
	intents = cloneIntents(intents)
	if err := persistIntents(a.intentsPath, intents); err != nil {
		return fmt.Errorf("persist desired intents: %w", err)
	}
	a.mu.Lock()
	a.intents = intents
	a.mu.Unlock()
	a.metrics.Set("intents", float64(len(intents)))
	return nil
}

func (a *App) Preview() (model.RoutePlan, error) {
	a.mu.RLock()
	intents := cloneIntents(a.intents)
	a.mu.RUnlock()
	started := time.Now()
	plan, err := a.planner.Build(a.topology.Snapshot(), intents, a.now().UTC())
	a.metrics.ObserveDuration("path_computation_last", started)
	a.metrics.Inc("path_computation_total")
	if err != nil {
		a.metrics.Inc("path_computation_failure_total")
	}
	return plan, err
}

func (a *App) Reconcile(ctx context.Context) (model.RoutePlan, model.ReconcileStatus, error) {
	if !a.IsLeader() {
		return model.RoutePlan{}, a.Status(), ErrNotLeader
	}
	plan, err := a.Preview()
	if err != nil {
		return model.RoutePlan{}, model.ReconcileStatus{}, err
	}
	status, err := a.reconciler.Apply(ctx, plan)
	if err == nil {
		a.mu.RLock()
		lastEvent := a.lastEvent
		a.mu.RUnlock()
		if !lastEvent.IsZero() {
			a.metrics.Set("topology_to_forwarding_last_seconds", time.Since(lastEvent).Seconds())
		}
	}
	return plan, status, err
}

func (a *App) ApplyPlan(ctx context.Context, plan model.RoutePlan) (model.ReconcileStatus, error) {
	return a.reconciler.Apply(ctx, plan)
}

func (a *App) ApplyEvent(event model.TopologyEvent) (model.TopologySnapshot, error) {
	if !a.IsLeader() {
		return a.Topology(), ErrNotLeader
	}
	snapshot, err := a.topology.Apply(event)
	if err != nil {
		if errors.Is(err, topology.ErrDuplicateEvent) {
			a.metrics.Inc("topology_duplicate_events_total")
		} else if errors.Is(err, topology.ErrStaleEvent) {
			a.metrics.Inc("topology_stale_events_total")
		} else {
			a.metrics.Inc("topology_event_failure_total")
		}
		return snapshot, err
	}
	a.metrics.Inc("topology_events_total")
	a.mu.Lock()
	a.lastEvent = time.Now()
	a.mu.Unlock()
	a.syncDeviceHealth(snapshot)
	a.refreshMetrics()
	a.logger.Event("info", "topology event applied", map[string]any{"event_id": event.EventID, "subject": event.Subject, "sequence": event.Sequence, "topology_version": snapshot.Version})
	return snapshot, nil
}

// ApplyEvents atomically advances all links from one physical sampling instant.
func (a *App) ApplyEvents(events []model.TopologyEvent) (model.TopologySnapshot, error) {
	if !a.IsLeader() {
		return a.Topology(), ErrNotLeader
	}
	snapshot, err := a.topology.ApplyBatch(events)
	if err != nil {
		return snapshot, err
	}
	a.mu.Lock()
	a.lastEvent = a.now().UTC()
	a.mu.Unlock()
	a.refreshMetrics()
	a.metrics.Inc("topology_event_batches_total")
	a.syncDeviceHealth(snapshot)
	a.logger.Event("info", "topology event batch applied", map[string]any{"events": len(events), "topology_version": snapshot.Version})
	return snapshot, nil
}

func (a *App) Status() model.ReconcileStatus   { return a.reconciler.Status() }
func (a *App) CommittedPlan() *model.RoutePlan { return a.reconciler.CommittedPlan() }
func (a *App) Metrics() *observability.Metrics { return a.metrics }
func (a *App) Close() error {
	a.cancel()
	a.workers.Wait()
	return a.registry.Close()
}

func (a *App) DeviceStates(ctx context.Context) []model.DeviceState {
	names := a.registry.Names()
	states := make([]model.DeviceState, 0, len(names))
	for _, name := range names {
		device, _ := a.registry.Get(name)
		state, err := device.State(ctx)
		if err != nil {
			state = model.DeviceState{Device: name, Healthy: false, ObservedAt: a.now().UTC(), Message: err.Error()}
		}
		states = append(states, state)
	}
	return states
}

func (a *App) SetDeviceFault(name string, fault adapter.FaultMode) error {
	if !a.IsLeader() {
		return ErrNotLeader
	}
	a.mu.RLock()
	device, ok := a.devices[name]
	a.mu.RUnlock()
	if !ok {
		return fmt.Errorf("device %q does not support digital-twin fault injection", name)
	}
	switch fault {
	case adapter.FaultNone, adapter.FaultHealth, adapter.FaultPrepare, adapter.FaultCommit, adapter.FaultState:
		device.SetFault(fault)
	default:
		return fmt.Errorf("unknown fault mode %q", fault)
	}
	a.logger.Event("warn", "device fault changed", map[string]any{"device": name, "fault": fault})
	return nil
}

func (a *App) IsLeader() bool { return a.canMutate == nil || a.canMutate() }

func (a *App) Reload() error {
	if err := a.topology.Reload(); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := a.reconciler.Reload(); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if a.intentsPath != "" {
		intents, err := readIntents(a.intentsPath)
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		if err == nil {
			a.mu.Lock()
			a.intents = cloneIntents(intents)
			a.mu.Unlock()
		}
	}
	a.syncDeviceHealth(a.topology.Snapshot())
	a.refreshMetrics()
	return nil
}

type persistedIntents struct {
	SchemaVersion int                 `json:"schema_version"`
	Intents       []model.RouteIntent `json:"intents"`
}

func loadIntents(path string, fallback []model.RouteIntent) ([]model.RouteIntent, error) {
	if path == "" {
		return cloneIntents(fallback), nil
	}
	intents, err := readIntents(path)
	if err == nil {
		return intents, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if err := persistIntents(path, fallback); err != nil {
		return nil, err
	}
	return cloneIntents(fallback), nil
}

func readIntents(path string) ([]model.RouteIntent, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var state persistedIntents
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("decode desired intents: %w", err)
	}
	if state.SchemaVersion != 1 {
		return nil, fmt.Errorf("unsupported desired-intent schema version %d", state.SchemaVersion)
	}
	ids := make(map[string]bool, len(state.Intents))
	for _, intent := range state.Intents {
		if err := intent.Validate(); err != nil {
			return nil, fmt.Errorf("persisted intent %q: %w", intent.ID, err)
		}
		if ids[intent.ID] {
			return nil, fmt.Errorf("duplicate persisted intent %q", intent.ID)
		}
		ids[intent.ID] = true
	}
	return cloneIntents(state.Intents), nil
}

func persistIntents(path string, intents []model.RouteIntent) error {
	if path == "" {
		return nil
	}
	return durable.WriteJSON(path, persistedIntents{SchemaVersion: 1, Intents: cloneIntents(intents)}, 0o600)
}

func cloneIntents(input []model.RouteIntent) []model.RouteIntent {
	output := append([]model.RouteIntent(nil), input...)
	for index := range output {
		output[index].GatewayCandidates = append([]string(nil), input[index].GatewayCandidates...)
		if input[index].Labels != nil {
			output[index].Labels = make(map[string]string, len(input[index].Labels))
			for key, value := range input[index].Labels {
				output[index].Labels[key] = value
			}
		}
	}
	return output
}

func (a *App) syncDeviceHealth(snapshot model.TopologySnapshot) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, node := range snapshot.Nodes {
		device, exists := a.devices[node.ID]
		if exists {
			device.SetHealthy(node.Enabled)
			continue
		}
		if _, registered := a.registry.Get(node.ID); registered {
			continue
		}
		created, err := a.factory(node)
		if err != nil {
			a.logger.Event("error", "device adapter creation failed", map[string]any{"device": node.ID, "error": err.Error()})
			continue
		}
		a.registry.Add(created)
		if simulated, ok := created.(*adapter.MemoryDevice); ok {
			a.devices[node.ID] = simulated
		}
	}
}

func (a *App) refreshMetrics() {
	snapshot := a.topology.Snapshot()
	activeNodes, activeLinks := 0, 0
	for _, node := range snapshot.Nodes {
		if node.Enabled {
			activeNodes++
		}
	}
	for _, link := range snapshot.Links {
		if link.Usable(a.now().UTC(), 0) {
			activeLinks++
		}
	}
	a.metrics.Set("topology_version", float64(snapshot.Version))
	a.metrics.Set("active_nodes", float64(activeNodes))
	a.metrics.Set("active_links", float64(activeLinks))
	a.metrics.Set("intents", float64(len(a.Intents())))
}
