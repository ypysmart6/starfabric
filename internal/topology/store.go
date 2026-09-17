package topology

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"

	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/model"
)

var (
	ErrDuplicateEvent = errors.New("duplicate topology event")
	ErrStaleEvent     = errors.New("stale or out-of-order topology event")
	ErrFutureEvent    = errors.New("topology event is not effective yet")
)

type persistentState struct {
	Snapshot      model.TopologySnapshot `json:"snapshot"`
	SeenEvents    map[string]time.Time   `json:"seen_events"`
	LastSequences map[string]uint64      `json:"last_sequences"`
}

// Store owns a versioned topology and enforces event idempotency and monotonic
// per-subject sequencing. Mutations are persisted atomically when a path is set.
type Store struct {
	mu            sync.RWMutex
	snapshot      model.TopologySnapshot
	seenEvents    map[string]time.Time
	lastSequences map[string]uint64
	persistPath   string
	now           func() time.Time
}

func New(initial model.TopologySnapshot, persistPath string) (*Store, error) {
	if err := initial.Validate(); err != nil {
		return nil, err
	}
	store := &Store{
		snapshot:      initial.Clone(),
		seenEvents:    make(map[string]time.Time),
		lastSequences: make(map[string]uint64),
		persistPath:   persistPath,
		now:           time.Now,
	}
	return store, nil
}

func Load(path string) (*Store, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var state persistentState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("decode topology state: %w", err)
	}
	if err := state.Snapshot.Validate(); err != nil {
		return nil, fmt.Errorf("invalid persisted topology: %w", err)
	}
	if state.SeenEvents == nil {
		state.SeenEvents = make(map[string]time.Time)
	}
	if state.LastSequences == nil {
		state.LastSequences = make(map[string]uint64)
	}
	return &Store{snapshot: state.Snapshot, seenEvents: state.SeenEvents, lastSequences: state.LastSequences, persistPath: path, now: time.Now}, nil
}

func (s *Store) Snapshot() model.TopologySnapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.snapshot.Clone()
}

// Reload atomically refreshes in-memory state after a follower wins leadership.
func (s *Store) Reload() error {
	if s.persistPath == "" {
		return nil
	}
	loaded, err := Load(s.persistPath)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.snapshot = loaded.snapshot.Clone()
	s.seenEvents = loaded.seenEvents
	s.lastSequences = loaded.lastSequences
	return nil
}

func (s *Store) Replace(snapshot model.TopologySnapshot) error {
	if err := snapshot.Validate(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if snapshot.Version <= s.snapshot.Version {
		return fmt.Errorf("%w: version %d is not newer than %d", ErrStaleEvent, snapshot.Version, s.snapshot.Version)
	}
	old := s.snapshot
	s.snapshot = snapshot.Clone()
	if err := s.persistLocked(); err != nil {
		s.snapshot = old
		return err
	}
	return nil
}

func (s *Store) Apply(event model.TopologyEvent) (model.TopologySnapshot, error) {
	if event.EventID == "" || event.Subject == "" || event.Sequence == 0 {
		return model.TopologySnapshot{}, errors.New("event_id, subject, and positive sequence are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.seenEvents[event.EventID]; exists {
		return s.snapshot.Clone(), ErrDuplicateEvent
	}
	if event.Sequence <= s.lastSequences[event.Subject] {
		return s.snapshot.Clone(), fmt.Errorf("%w: subject=%s sequence=%d last=%d", ErrStaleEvent, event.Subject, event.Sequence, s.lastSequences[event.Subject])
	}
	now := s.now().UTC()
	if !event.EffectiveAt.IsZero() && event.EffectiveAt.After(now) {
		return s.snapshot.Clone(), ErrFutureEvent
	}

	previous := s.snapshot.Clone()
	previousSequence, hadPreviousSequence := s.lastSequences[event.Subject]
	snapshot := s.snapshot.Clone()
	snapshot.Version++
	snapshot.GeneratedAt = now
	snapshot.ValidFrom = now
	if err := mutate(&snapshot, event); err != nil {
		return s.snapshot.Clone(), err
	}
	if err := snapshot.Validate(); err != nil {
		return s.snapshot.Clone(), err
	}
	s.snapshot = snapshot
	s.seenEvents[event.EventID] = now
	s.lastSequences[event.Subject] = event.Sequence
	if err := s.persistLocked(); err != nil {
		s.snapshot = previous
		delete(s.seenEvents, event.EventID)
		if hadPreviousSequence {
			s.lastSequences[event.Subject] = previousSequence
		} else {
			delete(s.lastSequences, event.Subject)
		}
		return s.snapshot.Clone(), err
	}
	return snapshot.Clone(), nil
}

// ApplyBatch publishes one physical sampling instant as one topology version.
// Validation or persistence failure leaves both topology and replay guards intact.
func (s *Store) ApplyBatch(events []model.TopologyEvent) (model.TopologySnapshot, error) {
	if len(events) == 0 || len(events) > 10000 {
		return model.TopologySnapshot{}, errors.New("event batch must contain 1–10000 events")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now().UTC()
	next := s.snapshot.Clone()
	seen := make(map[string]time.Time, len(s.seenEvents)+len(events))
	sequences := make(map[string]uint64, len(s.lastSequences)+len(events))
	for key, value := range s.seenEvents {
		// Per-subject sequence guards remain durable. Bound the extra event-ID
		// deduplication cache for long-lived physical telemetry streams.
		if !value.Before(now.Add(-5 * time.Minute)) {
			seen[key] = value
		}
	}
	for key, value := range s.lastSequences {
		sequences[key] = value
	}
	for _, event := range events {
		if event.EventID == "" || event.Subject == "" || event.Sequence == 0 {
			return s.snapshot.Clone(), errors.New("event_id, subject, and positive sequence are required")
		}
		if _, exists := seen[event.EventID]; exists {
			return s.snapshot.Clone(), ErrDuplicateEvent
		}
		if event.Sequence <= sequences[event.Subject] {
			return s.snapshot.Clone(), ErrStaleEvent
		}
		if event.EffectiveAt.After(now) {
			return s.snapshot.Clone(), ErrFutureEvent
		}
		if event.Link != nil && event.Link.ID != event.Subject || event.Node != nil && event.Node.ID != event.Subject {
			return s.snapshot.Clone(), errors.New("event subject must match its object id")
		}
		if err := mutate(&next, event); err != nil {
			return s.snapshot.Clone(), err
		}
		seen[event.EventID], sequences[event.Subject] = now, event.Sequence
	}
	next.Version++
	next.GeneratedAt, next.ValidFrom = now, now
	if err := next.Validate(); err != nil {
		return s.snapshot.Clone(), err
	}
	previous, previousSeen, previousSequences := s.snapshot, s.seenEvents, s.lastSequences
	s.snapshot, s.seenEvents, s.lastSequences = next, seen, sequences
	if err := s.persistLocked(); err != nil {
		s.snapshot, s.seenEvents, s.lastSequences = previous, previousSeen, previousSequences
		return s.snapshot.Clone(), err
	}
	return next.Clone(), nil
}

func mutate(snapshot *model.TopologySnapshot, event model.TopologyEvent) error {
	switch event.Type {
	case model.EventLinkUp, model.EventLinkDown, model.EventLinkUpdate:
		if event.Link == nil {
			return errors.New("link event requires link")
		}
		idx := -1
		for i := range snapshot.Links {
			if snapshot.Links[i].ID == event.Link.ID {
				idx = i
				break
			}
		}
		if idx < 0 {
			if event.Type != model.EventLinkUpdate && event.Type != model.EventLinkUp {
				return fmt.Errorf("unknown link %q", event.Link.ID)
			}
			snapshot.Links = append(snapshot.Links, *event.Link)
			idx = len(snapshot.Links) - 1
		} else if event.Type == model.EventLinkUpdate {
			snapshot.Links[idx] = *event.Link
		}
		if event.Type == model.EventLinkUp {
			snapshot.Links[idx].OperationalUp = true
		}
		if event.Type == model.EventLinkDown {
			snapshot.Links[idx].OperationalUp = false
		}
		if !event.ObservedAt.IsZero() {
			snapshot.Links[idx].TelemetryAt = event.ObservedAt
		}
	case model.EventNodeUp, model.EventNodeDown:
		if event.Node == nil {
			return errors.New("node event requires node")
		}
		for i := range snapshot.Nodes {
			if snapshot.Nodes[i].ID == event.Node.ID {
				snapshot.Nodes[i].Enabled = event.Type == model.EventNodeUp
				return nil
			}
		}
		return fmt.Errorf("unknown node %q", event.Node.ID)
	default:
		return fmt.Errorf("unsupported event type %q", event.Type)
	}
	return nil
}

func (s *Store) persistLocked() error {
	if s.persistPath == "" {
		return nil
	}
	state := persistentState{Snapshot: s.snapshot, SeenEvents: s.seenEvents, LastSequences: s.lastSequences}
	return durable.WriteJSON(s.persistPath, state, 0o600)
}

func (s *Store) ExpireSeenEvents(before time.Time) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	removed := 0
	for id, seen := range s.seenEvents {
		if seen.Before(before) {
			delete(s.seenEvents, id)
			removed++
		}
	}
	return removed
}

func (s *Store) Subjects() []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]string, 0, len(s.lastSequences))
	for subject := range s.lastSequences {
		out = append(out, subject)
	}
	sort.Strings(out)
	return out
}
