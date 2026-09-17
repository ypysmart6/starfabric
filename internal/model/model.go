package model

import (
	"errors"
	"fmt"
	"net/netip"
	"sort"
	"strconv"
	"time"
)

// Node is a controllable forwarding element or an endpoint in the network.
type Node struct {
	ID       string            `json:"id"`
	Kind     string            `json:"kind,omitempty"`
	Loopback string            `json:"loopback,omitempty"`
	Labels   map[string]string `json:"labels,omitempty"`
	Enabled  bool              `json:"enabled"`
}

// NextHopAddress prefers an explicitly bound adjacent interface. This lets a
// physical fabric program the chosen edge without recursive IGP forwarding.
// Inventories without interface bindings retain their loopback behavior.
func (n Node) NextHopAddress(peer Node) (string, error) {
	address, bound := n.Labels["next_hop:"+peer.ID]
	if !bound {
		if n.Labels["next_hop_scheme"] == "live-pair-v1" {
			return n.livePairNextHop(peer)
		}
		address = peer.Loopback
	}
	if _, err := netip.ParseAddr(address); err != nil {
		return "", fmt.Errorf("invalid next-hop address %s -> %s: %w", n.ID, peer.ID, err)
	}
	return address, nil
}

// livePairNextHop matches the stable /30 pool in lab/live/model.py. Three
// labels per node replace an all-pairs map that exceeds ConfigMap limits.
func (n Node) livePairNextHop(peer Node) (string, error) {
	count, countErr := strconv.Atoi(n.Labels["next_hop_nodes"])
	left, leftErr := strconv.Atoi(n.Labels["next_hop_index"])
	right, rightErr := strconv.Atoi(peer.Labels["next_hop_index"])
	if countErr != nil || leftErr != nil || rightErr != nil || count < 2 || count > 255 ||
		left < 0 || left >= count || right < 0 || right >= count || left == right ||
		peer.Labels["next_hop_scheme"] != "live-pair-v1" || peer.Labels["next_hop_nodes"] != n.Labels["next_hop_nodes"] {
		return "", fmt.Errorf("invalid live pair address binding %s -> %s", n.ID, peer.ID)
	}
	host := 2
	if left > right {
		left, right = right, left
		host = 1
	}
	pair := left*(2*count-left-1)/2 + right - left
	address := uint32(0x0a800000 + 4*pair + host)
	return netip.AddrFrom4([4]byte{byte(address >> 24), byte(address >> 16), byte(address >> 8), byte(address)}).String(), nil
}

// Link models a directed link. A bidirectional physical link is represented by
// two Link values so asymmetric telemetry can be represented without loss.
type Link struct {
	ID               string  `json:"id"`
	Source           string  `json:"source"`
	Target           string  `json:"target"`
	LinkType         string  `json:"link_type,omitempty"`
	AcquisitionState string  `json:"acquisition_state,omitempty"`
	AdminUp          bool    `json:"admin_up"`
	OperationalUp    bool    `json:"operational_up"`
	LatencyUS        int64   `json:"latency_us"`
	CapacityBPS      int64   `json:"capacity_bps"`
	LossPPM          int64   `json:"loss_ppm,omitempty"`
	ReliabilityPPM   int64   `json:"reliability_ppm,omitempty"`
	RangeKM          float64 `json:"range_km,omitempty"`
	DopplerHz        float64 `json:"doppler_hz,omitempty"`
	// RiskGroups identify shared physical failure domains such as an optical
	// terminal, feeder gateway, power domain, ground site, or weather cell.
	RiskGroups      []string  `json:"risk_groups,omitempty"`
	TelemetryAt     time.Time `json:"telemetry_at,omitempty"`
	ValidUntil      time.Time `json:"valid_until,omitempty"`
	AvailabilityEnd time.Time `json:"availability_end,omitempty"`
}

func (l Link) Usable(at time.Time, maxTelemetryAge time.Duration) bool {
	if !l.AdminUp || !l.OperationalUp || l.CapacityBPS <= 0 || l.LatencyUS < 0 {
		return false
	}
	if l.AcquisitionState != "" && l.AcquisitionState != "locked" && l.AcquisitionState != "degraded" {
		return false
	}
	if !l.ValidUntil.IsZero() && !at.Before(l.ValidUntil) {
		return false
	}
	if !l.AvailabilityEnd.IsZero() && !at.Before(l.AvailabilityEnd) {
		return false
	}
	if maxTelemetryAge > 0 && !l.TelemetryAt.IsZero() && at.Sub(l.TelemetryAt) > maxTelemetryAge {
		return false
	}
	return true
}

type TopologySnapshot struct {
	Version     uint64    `json:"version"`
	GeneratedAt time.Time `json:"generated_at"`
	ValidFrom   time.Time `json:"valid_from"`
	ValidUntil  time.Time `json:"valid_until,omitempty"`
	Nodes       []Node    `json:"nodes"`
	Links       []Link    `json:"links"`
}

func (s TopologySnapshot) Clone() TopologySnapshot {
	out := s
	out.Nodes = append([]Node(nil), s.Nodes...)
	out.Links = append([]Link(nil), s.Links...)
	for i := range out.Links {
		out.Links[i].RiskGroups = append([]string(nil), s.Links[i].RiskGroups...)
	}
	for i := range out.Nodes {
		if out.Nodes[i].Labels != nil {
			out.Nodes[i].Labels = make(map[string]string, len(out.Nodes[i].Labels))
			for key, value := range s.Nodes[i].Labels {
				out.Nodes[i].Labels[key] = value
			}
		}
	}
	return out
}

func (s TopologySnapshot) Validate() error {
	if s.Version == 0 {
		return errors.New("topology version must be greater than zero")
	}
	ids := make(map[string]struct{}, len(s.Nodes))
	for _, node := range s.Nodes {
		if node.ID == "" {
			return errors.New("node id is required")
		}
		if _, exists := ids[node.ID]; exists {
			return fmt.Errorf("duplicate node %q", node.ID)
		}
		ids[node.ID] = struct{}{}
	}
	linkIDs := make(map[string]struct{}, len(s.Links))
	for _, link := range s.Links {
		if link.ID == "" || link.Source == "" || link.Target == "" {
			return errors.New("link id, source, and target are required")
		}
		if link.Source == link.Target {
			return fmt.Errorf("link %q is a self-loop", link.ID)
		}
		if _, ok := ids[link.Source]; !ok {
			return fmt.Errorf("link %q references unknown source %q", link.ID, link.Source)
		}
		if _, ok := ids[link.Target]; !ok {
			return fmt.Errorf("link %q references unknown target %q", link.ID, link.Target)
		}
		if _, exists := linkIDs[link.ID]; exists {
			return fmt.Errorf("duplicate link %q", link.ID)
		}
		riskGroups := make(map[string]bool, len(link.RiskGroups))
		for _, group := range link.RiskGroups {
			if group == "" {
				return fmt.Errorf("link %q contains an empty risk group", link.ID)
			}
			if riskGroups[group] {
				return fmt.Errorf("link %q contains duplicate risk group %q", link.ID, group)
			}
			riskGroups[group] = true
		}
		linkIDs[link.ID] = struct{}{}
	}
	return nil
}

type EventType string

const (
	EventLinkUp     EventType = "link_up"
	EventLinkDown   EventType = "link_down"
	EventLinkUpdate EventType = "link_update"
	EventNodeUp     EventType = "node_up"
	EventNodeDown   EventType = "node_down"
)

// TopologyEvent is ordered per Subject by Sequence. EventID provides global
// idempotency across retries and controller restarts.
type TopologyEvent struct {
	EventID     string         `json:"event_id"`
	Subject     string         `json:"subject"`
	Sequence    uint64         `json:"sequence"`
	Type        EventType      `json:"type"`
	ObservedAt  time.Time      `json:"observed_at"`
	EffectiveAt time.Time      `json:"effective_at"`
	Link        *Link          `json:"link,omitempty"`
	Node        *Node          `json:"node,omitempty"`
	Attributes  map[string]any `json:"attributes,omitempty"`
}

type RouteIntent struct {
	ID                string   `json:"id"`
	Source            string   `json:"source"`
	Destination       string   `json:"destination"`
	GatewayCandidates []string `json:"gateway_candidates,omitempty"`
	DestinationPrefix string   `json:"destination_prefix"`
	Policy            string   `json:"policy,omitempty"`
	MaxLatencyUS      int64    `json:"max_latency_us,omitempty"`
	MinCapacityBPS    int64    `json:"min_capacity_bps,omitempty"`
	// DemandBPS is reserved on the selected primary path while subsequent
	// intents are planned. MinCapacityBPS remains a per-link admission floor.
	DemandBPS           int64             `json:"demand_bps,omitempty"`
	MaxLossPPM          int64             `json:"max_loss_ppm,omitempty"`
	RequiredReliability int64             `json:"required_reliability_ppm,omitempty"`
	Redundancy          int               `json:"redundancy,omitempty"`
	Class               string            `json:"class,omitempty"`
	Priority            int               `json:"priority,omitempty"`
	Labels              map[string]string `json:"labels,omitempty"`
}

func (i RouteIntent) Validate() error {
	if i.ID == "" || i.Source == "" || i.DestinationPrefix == "" {
		return errors.New("intent id, source, and destination_prefix are required")
	}
	if i.Destination == "" && len(i.GatewayCandidates) == 0 {
		return errors.New("destination or gateway_candidates is required")
	}
	if i.Redundancy < 0 || i.Redundancy > 2 {
		return errors.New("redundancy must be between zero and two")
	}
	if i.MinCapacityBPS < 0 || i.DemandBPS < 0 {
		return errors.New("capacity constraints cannot be negative")
	}
	return nil
}

type Path struct {
	Nodes       []string `json:"nodes"`
	Links       []string `json:"links"`
	LatencyUS   int64    `json:"latency_us"`
	CapacityBPS int64    `json:"capacity_bps"`
	LossPPM     int64    `json:"loss_ppm"`
	Cost        int64    `json:"cost"`
	RiskGroups  []string `json:"risk_groups,omitempty"`
}

type RouteOperation struct {
	Device      string `json:"device"`
	Prefix      string `json:"prefix"`
	NextHop     string `json:"next_hop"`
	NextHopNode string `json:"next_hop_node"`
	Metric      int    `json:"metric,omitempty"`
	IntentID    string `json:"intent_id"`
	PathRole    string `json:"path_role"`
}

type RoutePlan struct {
	ID              string                 `json:"id"`
	TopologyVersion uint64                 `json:"topology_version"`
	CreatedAt       time.Time              `json:"created_at"`
	ExpiresAt       time.Time              `json:"expires_at"`
	Policy          string                 `json:"policy"`
	Intents         []RouteIntent          `json:"intents"`
	Paths           map[string][]Path      `json:"paths"`
	Routes          []RouteOperation       `json:"routes"`
	Preconditions   []string               `json:"preconditions,omitempty"`
	RollbackPlanID  string                 `json:"rollback_plan_id,omitempty"`
	Metadata        map[string]interface{} `json:"metadata,omitempty"`
}

func (p RoutePlan) Clone() RoutePlan {
	out := p
	out.Intents = append([]RouteIntent(nil), p.Intents...)
	out.Routes = append([]RouteOperation(nil), p.Routes...)
	out.Preconditions = append([]string(nil), p.Preconditions...)
	out.Paths = make(map[string][]Path, len(p.Paths))
	for key, paths := range p.Paths {
		cloned := append([]Path(nil), paths...)
		for index := range cloned {
			cloned[index].Nodes = append([]string(nil), paths[index].Nodes...)
			cloned[index].Links = append([]string(nil), paths[index].Links...)
			cloned[index].RiskGroups = append([]string(nil), paths[index].RiskGroups...)
		}
		out.Paths[key] = cloned
	}
	return out
}

func SortRoutes(routes []RouteOperation) {
	sort.Slice(routes, func(a, b int) bool {
		if routes[a].Device != routes[b].Device {
			return routes[a].Device < routes[b].Device
		}
		if routes[a].Prefix != routes[b].Prefix {
			return routes[a].Prefix < routes[b].Prefix
		}
		if routes[a].Metric != routes[b].Metric {
			return routes[a].Metric < routes[b].Metric
		}
		return routes[a].NextHopNode < routes[b].NextHopNode
	})
}

type DeviceState struct {
	Device        string           `json:"device"`
	Healthy       bool             `json:"healthy"`
	AppliedPlanID string           `json:"applied_plan_id,omitempty"`
	Routes        []RouteOperation `json:"routes"`
	ObservedAt    time.Time        `json:"observed_at"`
	Message       string           `json:"message,omitempty"`
}

type ReconcilePhase string

const (
	PhaseIdle       ReconcilePhase = "idle"
	PhaseValidating ReconcilePhase = "validating"
	PhasePreparing  ReconcilePhase = "preparing"
	PhaseCommitting ReconcilePhase = "committing"
	PhaseVerifying  ReconcilePhase = "verifying"
	PhaseCommitted  ReconcilePhase = "committed"
	PhaseRolledBack ReconcilePhase = "rolled_back"
	PhaseFailed     ReconcilePhase = "failed"
)

type ReconcileStatus struct {
	Phase            ReconcilePhase `json:"phase"`
	PlanID           string         `json:"plan_id,omitempty"`
	TopologyVersion  uint64         `json:"topology_version,omitempty"`
	StartedAt        time.Time      `json:"started_at,omitempty"`
	FinishedAt       time.Time      `json:"finished_at,omitempty"`
	PreparedDevices  []string       `json:"prepared_devices,omitempty"`
	CommittedDevices []string       `json:"committed_devices,omitempty"`
	DeferredDevices  []string       `json:"deferred_devices,omitempty"`
	FailedDevices    []string       `json:"failed_devices,omitempty"`
	Error            string         `json:"error,omitempty"`
	RollbackReason   string         `json:"rollback_reason,omitempty"`
}
