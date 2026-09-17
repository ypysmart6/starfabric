// Package constellation builds configurable, synthetic network scenarios for
// the existing planner and memory-adapter experiment runner. Plane/slot labels
// describe a logical mesh, not orbital propagation or measured radio contacts.
package constellation

import (
	"fmt"
	"strconv"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/scenario"
)

type Config struct {
	Satellites      int   `json:"satellites"`
	Gateways        int   `json:"gateways"`
	Planes          int   `json:"planes"`
	GatewayUplinks  int   `json:"gateway_uplinks"`
	Flows           int   `json:"flows"`
	CapacityBPS     int64 `json:"capacity_bps"`
	DemandBPS       int64 `json:"demand_bps"`
	ISLLatencyUS    int64 `json:"isl_latency_us"`
	FeederLatencyUS int64 `json:"feeder_latency_us"`
}

func Defaults() Config {
	return Config{Satellites: 120, Gateways: 4, GatewayUplinks: 3,
		CapacityBPS: 10_000_000_000, DemandBPS: 10_000_000,
		ISLLatencyUS: 2000, FeederLatencyUS: 4000}
}

func (c Config) Normalize() (Config, error) {
	if c.Satellites < 4 || c.Satellites > 10000 {
		return c, fmt.Errorf("satellites must be between 4 and 10000")
	}
	if c.Gateways < 2 || c.Gateways > 1000 {
		return c, fmt.Errorf("gateways must be between 2 and 1000")
	}
	if c.Planes == 0 {
		// Choose a compact rectangular mesh; prime sizes retain one ring.
		c.Planes = 1
		for p := 2; p*p <= c.Satellites; p++ {
			if c.Satellites%p == 0 {
				c.Planes = p
			}
		}
	}
	if c.Planes < 1 || c.Planes > c.Satellites || c.Satellites%c.Planes != 0 {
		return c, fmt.Errorf("planes must be positive and divide satellites (or 0 for automatic)")
	}
	if c.GatewayUplinks < 3 || c.GatewayUplinks > c.Satellites {
		return c, fmt.Errorf("gateway_uplinks must be between 3 and satellites to retain primary/backup after one failure")
	}
	if c.Flows == 0 {
		c.Flows = 2 * c.Gateways
	}
	if c.Flows < 1 || c.Flows > 10000 {
		return c, fmt.Errorf("flows must be between 1 and 10000 (or 0 for twice gateways)")
	}
	if c.CapacityBPS <= 0 || c.DemandBPS <= 0 || c.DemandBPS > c.CapacityBPS {
		return c, fmt.Errorf("capacity_bps and demand_bps must be positive, with demand_bps <= capacity_bps")
	}
	if c.ISLLatencyUS <= 0 || c.FeederLatencyUS <= 0 || c.ISLLatencyUS > 1_000_000_000 || c.FeederLatencyUS > 1_000_000_000 {
		return c, fmt.Errorf("link latency must be between 1 and 1000000000 microseconds")
	}
	return c, nil
}

type Summary struct {
	Config                  Config   `json:"config"`
	ScenarioID              string   `json:"scenario_id"`
	TotalNodes              int      `json:"total_nodes"`
	SatellitesPerPlane      int      `json:"satellites_per_plane"`
	BidirectionalLinks      int      `json:"bidirectional_links"`
	DirectedLinks           int      `json:"directed_links"`
	ISLPairs                int      `json:"isl_pairs"`
	FeederPairs             int      `json:"feeder_pairs"`
	InitialRoutedSatellites int      `json:"initial_routed_satellites"`
	TimelineSteps           int      `json:"timeline_steps"`
	FaultLinkIDs            []string `json:"fault_link_ids"`
	FaultSatellite          string   `json:"fault_satellite"`
	EvidenceScope           string   `json:"evidence_scope"`
	RealPacketDataPlane     bool     `json:"real_packet_data_plane"`
	Unified5GIntegration    bool     `json:"unified_5g_integration"`
}

func satellite(index int) string    { return fmt.Sprintf("sat-%04d", index+1) }
func gateway(index int) string      { return fmt.Sprintf("gw-%03d", index+1) }
func linkID(from, to string) string { return from + "--" + to }

// Generate preflights the initial, link-failure and satellite-failure plans
// using the production planner. Infeasible traffic demands fail generation.
func Generate(config Config) (scenario.Scenario, Summary, error) {
	c, err := config.Normalize()
	if err != nil {
		return scenario.Scenario{}, Summary{}, err
	}
	at := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	s := scenario.Scenario{ID: fmt.Sprintf("synthetic-leo-%ds-%dg-%dp-%df", c.Satellites, c.Gateways, c.Planes, c.Flows),
		Description: "Synthetic logical plane/ring mesh with gateway traffic and fault replay. Memory-adapter control validation; not TLE-derived visibility, live FRR forwarding, or unified 5G acceptance.",
		Seed:        1, Topology: model.TopologySnapshot{Version: 1, GeneratedAt: at, ValidFrom: at}}
	slots := c.Satellites / c.Planes
	for i := 0; i < c.Satellites; i++ {
		s.Topology.Nodes = append(s.Topology.Nodes, model.Node{ID: satellite(i), Kind: "satellite", Enabled: true,
			Loopback: fmt.Sprintf("2001:db8:1::%x", i+1),
			Labels:   map[string]string{"logical_plane": strconv.Itoa(i/slots + 1), "logical_slot": strconv.Itoa(i%slots + 1), "model": "synthetic"}})
	}
	for i := 0; i < c.Gateways; i++ {
		s.Topology.Nodes = append(s.Topology.Nodes, model.Node{ID: gateway(i), Kind: "gateway", Enabled: true,
			Loopback: fmt.Sprintf("2001:db8:2::%x", i+1), Labels: map[string]string{"model": "synthetic"}})
	}
	seen := make(map[string]bool)
	addPair := func(a, b, kind string, latency int64) {
		if a == b || seen[linkID(a, b)] {
			return
		}
		for _, ends := range [][2]string{{a, b}, {b, a}} {
			l := model.Link{ID: linkID(ends[0], ends[1]), Source: ends[0], Target: ends[1], LinkType: kind,
				AdminUp: true, OperationalUp: true, LatencyUS: latency, CapacityBPS: c.CapacityBPS,
				ReliabilityPPM: 1_000_000}
			if kind == "oisl" {
				l.AcquisitionState = "locked"
			}
			s.Topology.Links = append(s.Topology.Links, l)
			seen[l.ID] = true
		}
	}
	for p := 0; p < c.Planes; p++ {
		for slot := 0; slot < slots; slot++ {
			i := p*slots + slot
			addPair(satellite(i), satellite(p*slots+(slot+1)%slots), "oisl", c.ISLLatencyUS)
			addPair(satellite(i), satellite(((p+1)%c.Planes)*slots+slot), "oisl", c.ISLLatencyUS)
		}
	}
	islPairs := len(s.Topology.Links) / 2
	for g := 0; g < c.Gateways; g++ {
		for u := 0; u < c.GatewayUplinks; u++ {
			i := (g*c.Satellites/c.Gateways + u*c.Satellites/c.GatewayUplinks) % c.Satellites
			addPair(gateway(g), satellite(i), "feeder", c.FeederLatencyUS)
		}
	}
	// Enumerate directed gateway pairs before repeating them. Every intent gets
	// its own prefix so distinct demands cannot conflict in the device AFT.
	for f := 0; f < c.Flows; f++ {
		source := f % c.Gateways
		destination := (source + 1 + (f/c.Gateways)%(c.Gateways-1)) % c.Gateways
		s.Intents = append(s.Intents, model.RouteIntent{ID: fmt.Sprintf("flow-%05d", f+1), Source: gateway(source), Destination: gateway(destination),
			DestinationPrefix: fmt.Sprintf("2001:db8:100:%x::/64", f+1), Policy: "latency", DemandBPS: c.DemandBPS,
			Redundancy: 1, Class: "synthetic-gateway-traffic", Priority: 100})
	}
	engine := planner.New(planner.Config{PlanTTL: time.Minute})
	initial, err := engine.Build(s.Topology, s.Intents, at)
	if err != nil {
		return s, Summary{}, fmt.Errorf("initial primary/backup preflight: %w", err)
	}
	primary := initial.Paths[s.Intents[0].ID][0]
	failedNode := primary.Nodes[1]
	// The source gateway's chosen feeder is always part of the first primary.
	failedLinks := []string{linkID(primary.Nodes[0], primary.Nodes[1]), linkID(primary.Nodes[1], primary.Nodes[0])}
	for _, failure := range []string{"link", "satellite"} {
		topology := s.Topology.Clone()
		if failure == "link" {
			for i := range topology.Links {
				if topology.Links[i].ID == failedLinks[0] || topology.Links[i].ID == failedLinks[1] {
					topology.Links[i].OperationalUp = false
				}
			}
		} else {
			for i := range topology.Nodes {
				if topology.Nodes[i].ID == failedNode {
					topology.Nodes[i].Enabled = false
				}
			}
		}
		if _, err := engine.Build(topology, s.Intents, at); err != nil {
			return s, Summary{}, fmt.Errorf("%s failure primary/backup preflight: %w", failure, err)
		}
	}
	s.Timeline = []scenario.Action{
		{AtMS: 0, Type: "reconcile"},
		{AtMS: 1000, Type: "link_down", LinkIDs: failedLinks, Reconcile: true},
		{AtMS: 2000, Type: "link_up", LinkIDs: failedLinks, Reconcile: true},
		{AtMS: 3000, Type: "node_down", NodeID: failedNode, Reconcile: true},
		{AtMS: 4000, Type: "node_up", NodeID: failedNode, Reconcile: true},
		{AtMS: 5000, Type: "device_fault", NodeID: gateway(0), Fault: adapter.FaultCommit},
		{AtMS: 5100, Type: "link_down", LinkIDs: failedLinks, Reconcile: true, ExpectFailure: true},
		{AtMS: 5200, Type: "clear_faults"},
		{AtMS: 5300, Type: "reconcile"},
		{AtMS: 6000, Type: "link_up", LinkIDs: failedLinks, Reconcile: true},
	}
	routed := make(map[string]bool)
	for _, paths := range initial.Paths {
		for _, path := range paths {
			for _, id := range path.Nodes {
				if len(id) >= 4 && id[:4] == "sat-" {
					routed[id] = true
				}
			}
		}
	}
	summary := Summary{Config: c, ScenarioID: s.ID, TotalNodes: len(s.Topology.Nodes), SatellitesPerPlane: slots,
		BidirectionalLinks: len(s.Topology.Links) / 2, DirectedLinks: len(s.Topology.Links), ISLPairs: islPairs,
		FeederPairs: len(s.Topology.Links)/2 - islPairs, InitialRoutedSatellites: len(routed), TimelineSteps: len(s.Timeline),
		FaultLinkIDs: failedLinks, FaultSatellite: failedNode,
		EvidenceScope: "generated synthetic topology; production planner preflight only; run sfctl experiment for memory-adapter fault verification"}
	return s, summary, scenario.Validate(s)
}
