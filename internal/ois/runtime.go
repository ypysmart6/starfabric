// Package ois models the network-facing state of an optical inter-satellite
// terminal. Optical acquisition is deliberately kept separate from routing:
// only a locked/degraded terminal projects an operational network link.
package ois

import (
	"errors"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

type State string

const (
	Idle      State = "idle"
	Searching State = "searching"
	Acquiring State = "acquiring"
	Locked    State = "locked"
	Degraded  State = "degraded"
	Fault     State = "fault"
)

type Event string

const (
	StartSearch     Event = "start_search"
	BeaconDetected  Event = "beacon_detected"
	LockAchieved    Event = "lock_achieved"
	QualityLow      Event = "quality_low"
	QualityRestored Event = "quality_restored"
	LockLost        Event = "lock_lost"
	Failure         Event = "failure"
	Reset           Event = "reset"
)

type Runtime struct {
	State       State     `json:"state"`
	ChangedAt   time.Time `json:"changed_at"`
	SignalDBM   float64   `json:"signal_dbm,omitempty"`
	BER         float64   `json:"ber,omitempty"`
	RemoteReady bool      `json:"remote_ready"`
}

func New(at time.Time) Runtime { return Runtime{State: Idle, ChangedAt: at} }

func (r *Runtime) Apply(event Event, at time.Time) error {
	next := r.State
	switch {
	case event == Failure:
		next = Fault
	case event == Reset && r.State == Fault:
		next = Idle
	case event == StartSearch && r.State == Idle:
		next = Searching
	case event == BeaconDetected && r.State == Searching:
		next = Acquiring
	case event == LockAchieved && r.State == Acquiring:
		next = Locked
	case event == QualityLow && r.State == Locked:
		next = Degraded
	case event == QualityRestored && r.State == Degraded:
		next = Locked
	case event == LockLost && (r.State == Locked || r.State == Degraded || r.State == Acquiring):
		next = Searching
	default:
		return errors.New("invalid optical-terminal state transition")
	}
	r.State, r.ChangedAt = next, at
	return nil
}

func (r Runtime) Project(link model.Link, at time.Time) model.Link {
	link.LinkType = "oisl"
	link.AcquisitionState = string(r.State)
	link.OperationalUp = r.RemoteReady && (r.State == Locked || r.State == Degraded)
	link.TelemetryAt = at
	if r.State == Degraded && link.ReliabilityPPM > 0 {
		link.ReliabilityPPM = link.ReliabilityPPM * 9 / 10
	}
	return link
}
