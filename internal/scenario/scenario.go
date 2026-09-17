package scenario

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/model"
)

type Scenario struct {
	ID          string                 `json:"scenario_id"`
	Description string                 `json:"description,omitempty"`
	Seed        int64                  `json:"random_seed"`
	Topology    model.TopologySnapshot `json:"topology"`
	Intents     []model.RouteIntent    `json:"intents"`
	Timeline    []Action               `json:"timeline"`
}

type Action struct {
	AtMS          int64             `json:"at_ms"`
	Type          string            `json:"type"`
	LinkIDs       []string          `json:"link_ids,omitempty"`
	NodeID        string            `json:"node_id,omitempty"`
	Fault         adapter.FaultMode `json:"fault,omitempty"`
	Reconcile     bool              `json:"reconcile,omitempty"`
	ExpectFailure bool              `json:"expect_failure,omitempty"`
}

type StepResult struct {
	AtMS            int64                  `json:"at_ms"`
	Action          Action                 `json:"action"`
	TopologyVersion uint64                 `json:"topology_version"`
	PlanID          string                 `json:"plan_id,omitempty"`
	Status          *model.ReconcileStatus `json:"status,omitempty"`
	DurationUS      int64                  `json:"duration_us"`
	Error           string                 `json:"error,omitempty"`
	ExpectedFailure bool                   `json:"expected_failure,omitempty"`
	Assertion       string                 `json:"assertion"`
}

type Report struct {
	ScenarioID    string                 `json:"scenario_id"`
	Seed          int64                  `json:"random_seed"`
	InputVersion  uint64                 `json:"input_version"`
	StartedAt     time.Time              `json:"started_at"`
	FinishedAt    time.Time              `json:"finished_at"`
	Success       bool                   `json:"success"`
	Steps         []StepResult           `json:"steps"`
	FinalTopology model.TopologySnapshot `json:"final_topology"`
	FinalPlan     *model.RoutePlan       `json:"final_plan,omitempty"`
	DeviceStates  []model.DeviceState    `json:"device_states"`
	RollbackCount int                    `json:"rollback_count"`
	FailureCount  int                    `json:"failure_count"`
}

func Load(path string) (Scenario, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Scenario{}, err
	}
	var scenario Scenario
	if err := json.Unmarshal(data, &scenario); err != nil {
		return Scenario{}, fmt.Errorf("decode scenario: %w", err)
	}
	return scenario, Validate(scenario)
}

func Validate(s Scenario) error {
	if s.ID == "" {
		return errors.New("scenario_id is required")
	}
	if err := s.Topology.Validate(); err != nil {
		return fmt.Errorf("topology: %w", err)
	}
	intentIDs := make(map[string]bool, len(s.Intents))
	for _, intent := range s.Intents {
		if err := intent.Validate(); err != nil {
			return fmt.Errorf("intent %q: %w", intent.ID, err)
		}
		if intentIDs[intent.ID] {
			return fmt.Errorf("duplicate intent %q", intent.ID)
		}
		intentIDs[intent.ID] = true
	}
	for i, action := range s.Timeline {
		if action.AtMS < 0 {
			return fmt.Errorf("timeline[%d] has negative at_ms", i)
		}
		switch action.Type {
		case "reconcile":
		case "link_up", "link_down":
			if len(action.LinkIDs) == 0 {
				return fmt.Errorf("timeline[%d] requires link_ids", i)
			}
		case "node_up", "node_down", "device_fault":
			if action.NodeID == "" {
				return fmt.Errorf("timeline[%d] requires node_id", i)
			}
		case "clear_faults":
		default:
			return fmt.Errorf("timeline[%d] has unknown type %q", i, action.Type)
		}
	}
	return nil
}

type Runner struct {
	Config   app.Config
	Log      io.Writer
	RealTime bool
}

func (r Runner) Run(ctx context.Context, definition Scenario) (Report, error) {
	if err := Validate(definition); err != nil {
		return Report{}, err
	}
	application, err := app.New(r.Config, definition.Topology, definition.Intents, r.Log)
	if err != nil {
		return Report{}, err
	}
	report := Report{ScenarioID: definition.ID, Seed: definition.Seed, InputVersion: definition.Topology.Version, StartedAt: time.Now().UTC(), Success: true}
	actions := append([]Action(nil), definition.Timeline...)
	sort.SliceStable(actions, func(i, j int) bool { return actions[i].AtMS < actions[j].AtMS })
	lastAt := int64(0)
	sequences := make(map[string]uint64)
	for index, action := range actions {
		if r.RealTime && action.AtMS > lastAt {
			timer := time.NewTimer(time.Duration(action.AtMS-lastAt) * time.Millisecond)
			select {
			case <-ctx.Done():
				timer.Stop()
				return report, ctx.Err()
			case <-timer.C:
			}
		}
		lastAt = action.AtMS
		started := time.Now()
		step := StepResult{AtMS: action.AtMS, Action: action}
		stepErr := execute(ctx, application, definition.ID, index, action, sequences)
		if stepErr == nil && (action.Reconcile || action.Type == "reconcile") {
			plan, status, reconcileErr := application.Reconcile(ctx)
			step.PlanID = plan.ID
			step.Status = &status
			stepErr = reconcileErr
			if status.Phase == model.PhaseRolledBack {
				report.RollbackCount++
			}
		}
		step.TopologyVersion = application.Topology().Version
		step.DurationUS = time.Since(started).Microseconds()
		if action.ExpectFailure && stepErr != nil {
			step.ExpectedFailure = true
			step.Assertion = "expected failure observed"
		} else if action.ExpectFailure && stepErr == nil {
			step.Error = "expected action to fail, but it succeeded"
			step.Assertion = "failed"
			report.FailureCount++
			report.Success = false
		} else if stepErr != nil {
			step.Error = stepErr.Error()
			step.Assertion = "failed"
			report.FailureCount++
			report.Success = false
		} else {
			step.Assertion = "passed"
		}
		report.Steps = append(report.Steps, step)
	}
	report.FinishedAt = time.Now().UTC()
	report.FinalTopology = application.Topology()
	report.FinalPlan = application.CommittedPlan()
	report.DeviceStates = application.DeviceStates(ctx)
	return report, nil
}

func execute(ctx context.Context, application *app.App, scenarioID string, index int, action Action, sequences map[string]uint64) error {
	switch action.Type {
	case "reconcile":
		return nil
	case "link_up", "link_down":
		snapshot := application.Topology()
		links := make(map[string]model.Link, len(snapshot.Links))
		for _, link := range snapshot.Links {
			links[link.ID] = link
		}
		for _, id := range action.LinkIDs {
			link, exists := links[id]
			if !exists {
				return fmt.Errorf("unknown link %q", id)
			}
			sequences[id]++
			eventType := model.EventLinkDown
			if action.Type == "link_up" {
				eventType = model.EventLinkUp
			}
			_, err := application.ApplyEvent(model.TopologyEvent{
				EventID: fmt.Sprintf("%s-%04d-%s", scenarioID, index, id), Subject: id, Sequence: sequences[id],
				Type: eventType, ObservedAt: time.Now().UTC(), EffectiveAt: time.Now().UTC(), Link: &link,
			})
			if err != nil {
				return err
			}
		}
		return nil
	case "node_up", "node_down":
		snapshot := application.Topology()
		var selected *model.Node
		for _, node := range snapshot.Nodes {
			if node.ID == action.NodeID {
				copy := node
				selected = &copy
				break
			}
		}
		if selected == nil {
			return fmt.Errorf("unknown node %q", action.NodeID)
		}
		sequences[action.NodeID]++
		eventType := model.EventNodeDown
		if action.Type == "node_up" {
			eventType = model.EventNodeUp
		}
		_, err := application.ApplyEvent(model.TopologyEvent{EventID: fmt.Sprintf("%s-%04d-%s", scenarioID, index, action.NodeID), Subject: action.NodeID, Sequence: sequences[action.NodeID], Type: eventType, ObservedAt: time.Now().UTC(), EffectiveAt: time.Now().UTC(), Node: selected})
		return err
	case "device_fault":
		return application.SetDeviceFault(action.NodeID, action.Fault)
	case "clear_faults":
		for _, state := range application.DeviceStates(ctx) {
			if err := application.SetDeviceFault(state.Device, adapter.FaultNone); err != nil {
				return err
			}
		}
		return nil
	default:
		return fmt.Errorf("unsupported action %q", action.Type)
	}
}
