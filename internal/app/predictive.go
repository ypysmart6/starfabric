package app

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"slices"
	"sort"
	"time"

	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/predictive"
)

type PredictiveSchedule struct {
	ID                      string                  `json:"id"`
	State                   string                  `json:"state"`
	BaseTopologyVersion     uint64                  `json:"base_topology_version"`
	ExpectedTopologyVersion uint64                  `json:"expected_topology_version"`
	CreatedAt               time.Time               `json:"created_at"`
	UpdatedAt               time.Time               `json:"updated_at"`
	NextActivation          int                     `json:"next_activation"`
	Activations             []predictive.Activation `json:"activations"`
	Error                   string                  `json:"error,omitempty"`
}

type persistedPredictiveSchedules struct {
	SchemaVersion int                           `json:"schema_version"`
	Schedules     map[string]PredictiveSchedule `json:"schedules"`
}

func (a *App) Forecast(windows []predictive.ContactWindow, horizon, lead time.Duration) ([]predictive.Activation, error) {
	engine := predictive.Engine{Planner: a.planner, Lead: lead}
	return engine.Schedule(a.Topology(), windows, a.Intents(), a.now().UTC(), horizon)
}

// CreatePredictiveSchedule shadow-validates all contact boundaries, then owns
// activation of every forwarding change, including a current-boundary change
// caused by newly supplied contact information.
func (a *App) CreatePredictiveSchedule(windows []predictive.ContactWindow, horizon, lead time.Duration) (PredictiveSchedule, error) {
	if !a.IsLeader() {
		return PredictiveSchedule{}, ErrNotLeader
	}
	base := a.Topology()
	forecastBase := base.Clone()
	forecastBase.Version++ // The first forecast may itself require activation.
	engine := predictive.Engine{Planner: a.planner, Lead: lead}
	activations, err := engine.Schedule(forecastBase, windows, a.Intents(), a.now().UTC(), horizon)
	if err != nil {
		return PredictiveSchedule{}, err
	}
	now := a.now().UTC()
	schedule := PredictiveSchedule{
		ID: fmt.Sprintf("predictive-%d", now.UnixNano()), State: "running", BaseTopologyVersion: base.Version,
		ExpectedTopologyVersion: base.Version, CreatedAt: now, UpdatedAt: now, NextActivation: 0,
		Activations: cloneActivations(activations),
	}
	if committed := a.CommittedPlan(); committed != nil && len(activations) > 0 {
		left := append([]model.RouteOperation(nil), committed.Routes...)
		right := append([]model.RouteOperation(nil), activations[0].Plan.Routes...)
		model.SortRoutes(left)
		model.SortRoutes(right)
		if slices.Equal(left, right) {
			schedule.NextActivation = 1
		}
	}
	if schedule.NextActivation >= len(activations) {
		schedule.State = "completed"
	}
	a.scheduleMu.Lock()
	for _, current := range a.schedules {
		if current.State == "running" {
			a.scheduleMu.Unlock()
			return PredictiveSchedule{}, fmt.Errorf("predictive schedule %s is already running", current.ID)
		}
	}
	a.schedules[schedule.ID] = schedule
	if err := a.persistPredictiveSchedulesLocked(); err != nil {
		delete(a.schedules, schedule.ID)
		a.scheduleMu.Unlock()
		return PredictiveSchedule{}, fmt.Errorf("persist predictive schedule: %w", err)
	}
	a.scheduleMu.Unlock()
	if schedule.State == "running" {
		a.startPredictiveWorker(schedule.ID)
	}
	a.metrics.Inc("predictive_schedule_total")
	return clonePredictiveSchedule(schedule), nil
}

func (a *App) PredictiveSchedule(id string) (PredictiveSchedule, bool) {
	a.scheduleMu.RLock()
	defer a.scheduleMu.RUnlock()
	schedule, exists := a.schedules[id]
	return clonePredictiveSchedule(schedule), exists
}

func (a *App) PredictiveSchedules() []PredictiveSchedule {
	a.scheduleMu.RLock()
	defer a.scheduleMu.RUnlock()
	result := make([]PredictiveSchedule, 0, len(a.schedules))
	for _, schedule := range a.schedules {
		result = append(result, clonePredictiveSchedule(schedule))
	}
	sort.Slice(result, func(i, j int) bool { return result[i].CreatedAt.Before(result[j].CreatedAt) })
	return result
}

func (a *App) startPredictiveWorker(id string) {
	a.workers.Add(1)
	go func() {
		defer a.workers.Done()
		a.runPredictiveSchedule(id)
	}()
}

func (a *App) runPredictiveSchedule(id string) {
	for {
		a.scheduleMu.RLock()
		schedule, exists := a.schedules[id]
		if !exists || schedule.State != "running" || schedule.NextActivation >= len(schedule.Activations) {
			a.scheduleMu.RUnlock()
			return
		}
		activation := schedule.Activations[schedule.NextActivation]
		a.scheduleMu.RUnlock()

		delay := time.Until(activation.ActivateAt)
		if delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-a.ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
		if err := a.activatePrediction(id, activation); err != nil {
			a.finishPredictiveActivation(id, 0, err)
			return
		}
		a.finishPredictiveActivation(id, activation.Topology.Version, nil)
	}
}

func (a *App) activatePrediction(id string, activation predictive.Activation) error {
	if !a.IsLeader() {
		return ErrNotLeader
	}
	a.scheduleMu.RLock()
	schedule, exists := a.schedules[id]
	a.scheduleMu.RUnlock()
	if !exists || schedule.State != "running" {
		return errors.New("predictive schedule is no longer running")
	}
	current := a.Topology()
	if current.Version == activation.Topology.Version {
		if committed := a.CommittedPlan(); committed != nil && committed.ID == activation.Plan.ID {
			return nil
		}
		return fmt.Errorf("topology %d was activated without predicted plan %s", current.Version, activation.Plan.ID)
	}
	if current.Version != schedule.ExpectedTopologyVersion {
		return fmt.Errorf("topology changed since forecast: expected %d, current %d", schedule.ExpectedTopologyVersion, current.Version)
	}
	previous := current.Clone()
	if err := a.topology.Replace(activation.Topology); err != nil {
		return fmt.Errorf("activate predicted topology: %w", err)
	}
	a.syncDeviceHealth(activation.Topology)
	a.refreshMetrics()
	if _, err := a.ApplyPlan(a.ctx, activation.Plan); err != nil {
		restored := previous.Clone()
		restored.Version = activation.Topology.Version + 1
		restored.GeneratedAt = a.now().UTC()
		restored.ValidFrom = restored.GeneratedAt
		restored.ValidUntil = time.Time{}
		restoreErr := a.topology.Replace(restored)
		a.syncDeviceHealth(restored)
		a.refreshMetrics()
		return errors.Join(fmt.Errorf("apply predicted plan: %w", err), restoreErr)
	}
	a.mu.Lock()
	a.lastEvent = time.Now()
	a.mu.Unlock()
	a.metrics.Inc("predictive_activation_success_total")
	a.logger.Event("info", "predicted topology and route plan activated", map[string]any{
		"schedule_id": id, "plan_id": activation.Plan.ID, "topology_version": activation.Topology.Version,
		"topology_at": activation.TopologyAt, "activate_at": activation.ActivateAt,
	})
	return nil
}

func (a *App) finishPredictiveActivation(id string, version uint64, activationErr error) {
	a.scheduleMu.Lock()
	defer a.scheduleMu.Unlock()
	schedule, exists := a.schedules[id]
	if !exists {
		return
	}
	schedule.UpdatedAt = a.now().UTC()
	if activationErr != nil {
		schedule.State = "failed"
		schedule.Error = activationErr.Error()
		a.metrics.Inc("predictive_activation_failure_total")
	} else {
		schedule.ExpectedTopologyVersion = version
		schedule.NextActivation++
		if schedule.NextActivation >= len(schedule.Activations) {
			schedule.State = "completed"
		}
	}
	a.schedules[id] = schedule
	if err := a.persistPredictiveSchedulesLocked(); err != nil {
		a.logger.Event("error", "predictive schedule state persistence failed", map[string]any{"schedule_id": id, "error": err.Error()})
	}
}

func (a *App) loadPredictiveSchedules() error {
	if a.predictivePath == "" {
		return nil
	}
	data, err := os.ReadFile(a.predictivePath)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var state persistedPredictiveSchedules
	if err := json.Unmarshal(data, &state); err != nil {
		return fmt.Errorf("decode predictive schedules: %w", err)
	}
	if state.SchemaVersion != 1 {
		return fmt.Errorf("unsupported predictive schedule schema %d", state.SchemaVersion)
	}
	for id, schedule := range state.Schedules {
		if id == "" || id != schedule.ID || schedule.NextActivation < 0 || schedule.NextActivation > len(schedule.Activations) {
			return fmt.Errorf("invalid persisted predictive schedule %q", id)
		}
		a.schedules[id] = clonePredictiveSchedule(schedule)
	}
	return nil
}

func (a *App) resumePredictiveSchedules() {
	for _, schedule := range a.PredictiveSchedules() {
		if schedule.State == "running" {
			a.startPredictiveWorker(schedule.ID)
		}
	}
}

func (a *App) persistPredictiveSchedulesLocked() error {
	if a.predictivePath == "" {
		return nil
	}
	copy := make(map[string]PredictiveSchedule, len(a.schedules))
	for id, schedule := range a.schedules {
		copy[id] = clonePredictiveSchedule(schedule)
	}
	return durable.WriteJSON(a.predictivePath, persistedPredictiveSchedules{SchemaVersion: 1, Schedules: copy}, 0o600)
}

func clonePredictiveSchedule(input PredictiveSchedule) PredictiveSchedule {
	input.Activations = cloneActivations(input.Activations)
	return input
}

func cloneActivations(input []predictive.Activation) []predictive.Activation {
	output := append([]predictive.Activation(nil), input...)
	for index := range output {
		output[index].Topology = input[index].Topology.Clone()
		output[index].Plan = input[index].Plan.Clone()
		output[index].ChangedPrefixes = append([]string(nil), input[index].ChangedPrefixes...)
	}
	return output
}
