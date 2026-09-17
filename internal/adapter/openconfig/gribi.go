package openconfig

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"net/netip"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"

	gribipb "github.com/openconfig/gribi/v1/proto/service"
	gribiclient "github.com/openconfig/gribigo/client"
	"github.com/openconfig/gribigo/fluent"
	"github.com/starfabric/starfabric/internal/model"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"
)

// Programmer is the gRIBI boundary used by the transactional adapter. It is
// small enough to exercise with a fake target and keeps target-specific setup
// outside path planning and reconciliation.
type Programmer interface {
	Apply(context.Context, []model.RouteOperation, []model.RouteOperation) error
	Read(context.Context) ([]model.RouteOperation, error)
	Close() error
}

type GRIBIProgrammer struct {
	endpoint        Endpoint
	networkInstance string
	conn            *grpc.ClientConn
	electionID      atomic.Uint64
	catalogMu       sync.RWMutex
	catalog         map[string][]model.RouteOperation
}

func NewGRIBIProgrammer(endpoint Endpoint, networkInstance string) (*GRIBIProgrammer, error) {
	if networkInstance == "" {
		networkInstance = "DEFAULT"
	}
	conn, err := dial(endpoint)
	if err != nil {
		return nil, err
	}
	return &GRIBIProgrammer{
		endpoint: endpoint, networkInstance: networkInstance, conn: conn,
		catalog: make(map[string][]model.RouteOperation),
	}, nil
}

func (p *GRIBIProgrammer) Close() error { return p.conn.Close() }

func (p *GRIBIProgrammer) client(ctx context.Context) (*gribiclient.Client, *gribipb.Uint128, error) {
	// SINGLE_PRIMARY matches the controller's single-writer/leader-election
	// model. Every short-lived programming session uses a monotonically newer
	// election ID so a reconnect can take ownership of preserved entries.
	electionID := &gribipb.Uint128{Low: p.electionID.Add(1)}
	client, err := gribiclient.New(
		gribiclient.ElectedPrimaryClient(electionID),
		gribiclient.PersistEntries(),
		gribiclient.FIBACK(),
	)
	if err != nil {
		return nil, nil, err
	}
	if err := client.UseStub(gribipb.NewGRIBIClient(p.conn)); err != nil {
		return nil, nil, err
	}
	if err := client.Connect(p.endpoint.authenticated(ctx)); err != nil {
		return nil, nil, err
	}
	client.StartSending()
	if err := client.AwaitConverged(ctx); err != nil {
		client.Close()
		return nil, nil, fmt.Errorf("establish gRIBI FIB-ACK session: %w", err)
	}
	return client, electionID, nil
}

func (p *GRIBIProgrammer) Apply(ctx context.Context, previous, desired []model.RouteOperation) error {
	if err := p.RememberRoutes(previous); err != nil {
		return err
	}
	if err := p.RememberRoutes(desired); err != nil {
		return err
	}
	previousEntries, err := entriesForRoutes(p.networkInstance, previous)
	if err != nil {
		return err
	}
	desiredEntries, err := entriesForRoutes(p.networkInstance, desired)
	if err != nil {
		return err
	}
	stages, err := diffEntries(previousEntries, desiredEntries)
	if err != nil {
		return err
	}
	client, electionID, err := p.client(ctx)
	if err != nil {
		return err
	}
	defer client.Close()
	var operationID uint64
	for _, stage := range stages {
		if len(stage) == 0 {
			continue
		}
		for _, operation := range stage {
			operationID++
			operation.Id = operationID
			operation.ElectionId = electionID
		}
		expected := make(map[uint64]bool, len(stage))
		for _, operation := range stage {
			expected[operation.Id] = false
		}
		client.Q(&gribipb.ModifyRequest{Operation: stage})
		if err := client.AwaitConverged(ctx); err != nil {
			return fmt.Errorf("gRIBI programming stage failed: %w", err)
		}
		results, err := client.Results()
		if err != nil {
			return fmt.Errorf("read gRIBI FIB ACK: %w", err)
		}
		for _, result := range results {
			if _, belongsToStage := expected[result.OperationID]; !belongsToStage {
				continue
			}
			if result.ClientError != "" || result.ServerError != "" {
				return fmt.Errorf("gRIBI operation %d was not FIB programmed: %s", result.OperationID, result)
			}
			switch result.ProgrammingResult {
			case gribipb.AFTResult_RIB_PROGRAMMED:
				// FIB-ACK targets may emit the RIB acknowledgement first.
			case gribipb.AFTResult_FIB_PROGRAMMED:
				expected[result.OperationID] = true
			default:
				return fmt.Errorf("gRIBI operation %d was not FIB programmed: %s", result.OperationID, result)
			}
		}
		for id, acknowledged := range expected {
			if !acknowledged {
				return fmt.Errorf("gRIBI operation %d received no FIB acknowledgement", id)
			}
		}
	}
	return nil
}

func (p *GRIBIProgrammer) Read(ctx context.Context) ([]model.RouteOperation, error) {
	client, _, err := p.client(ctx)
	if err != nil {
		return nil, err
	}
	defer client.Close()
	response, err := client.Get(p.endpoint.authenticated(ctx), &gribipb.GetRequest{
		NetworkInstance: &gribipb.GetRequest_Name{Name: p.networkInstance}, Aft: gribipb.AFTType_ALL,
	})
	if err != nil {
		return nil, err
	}
	actual := make(map[string]proto.Message)
	for _, entry := range response.GetEntry() {
		if key, message, ok := aftEntry(entry); ok {
			actual[key] = message
		}
	}
	var routes []model.RouteOperation
	seen := make(map[string]bool)
	for _, entry := range response.GetEntry() {
		var raw []byte
		if ipv4 := entry.GetIpv4(); ipv4 != nil && ipv4.GetIpv4Entry() != nil && ipv4.GetIpv4Entry().GetEntryMetadata() != nil {
			raw = ipv4.GetIpv4Entry().GetEntryMetadata().GetValue()
		}
		if ipv6 := entry.GetIpv6(); ipv6 != nil && ipv6.GetIpv6Entry() != nil && ipv6.GetIpv6Entry().GetEntryMetadata() != nil {
			raw = ipv6.GetIpv6Entry().GetEntryMetadata().GetValue()
		}
		if len(raw) != 8 {
			continue
		}
		p.catalogMu.RLock()
		group := append([]model.RouteOperation(nil), p.catalog[string(raw)]...)
		p.catalogMu.RUnlock()
		if len(group) == 0 {
			continue
		}
		for _, route := range group {
			key := routeKey(route)
			if !seen[key] {
				routes = append(routes, route)
				seen[key] = true
			}
		}
	}
	model.SortRoutes(routes)
	expectedEntries, err := entriesForRoutes(p.networkInstance, routes)
	if err != nil {
		return nil, err
	}
	expected := make(map[string]proto.Message, len(expectedEntries))
	for _, entry := range expectedEntries {
		op, opErr := entry.entry.OpProto()
		if opErr != nil {
			return nil, opErr
		}
		expected[entry.key] = aftOperation(op)
	}
	for key, wanted := range expected {
		got, exists := actual[key]
		if !exists || !proto.Equal(got, wanted) {
			return nil, fmt.Errorf("gRIBI AFT drift for %s", key)
		}
	}
	// Known prior dependencies are owned by StarFabric. Seeing one outside the
	// active expected graph means a withdrawal was incomplete.
	p.catalogMu.RLock()
	for _, knownRoutes := range p.catalog {
		knownEntries, knownErr := entriesForRoutes(p.networkInstance, knownRoutes)
		if knownErr != nil {
			p.catalogMu.RUnlock()
			return nil, knownErr
		}
		for _, entry := range knownEntries {
			if _, present := actual[entry.key]; present {
				if _, active := expected[entry.key]; !active {
					p.catalogMu.RUnlock()
					return nil, fmt.Errorf("stale StarFabric gRIBI AFT entry %s", entry.key)
				}
			}
		}
	}
	p.catalogMu.RUnlock()
	return routes, nil
}

// RememberRoutes supplies the controller-owned fields that are not represented
// by an AFT (intent, role, and logical next-hop node). The eight-byte metadata
// stored on the IP entry is a stable key into this durable adapter catalog.
func (p *GRIBIProgrammer) RememberRoutes(routes []model.RouteOperation) error {
	groups := routeGroups(routes)
	p.catalogMu.Lock()
	defer p.catalogMu.Unlock()
	for _, group := range groups {
		token, err := routeToken(group)
		if err != nil {
			return err
		}
		if existing, ok := p.catalog[string(token)]; ok && !reflect.DeepEqual(existing, group) {
			return errors.New("gRIBI route metadata token collision")
		}
		p.catalog[string(token)] = append([]model.RouteOperation(nil), group...)
	}
	return nil
}

func routeGroups(routes []model.RouteOperation) map[string][]model.RouteOperation {
	groups := make(map[string][]model.RouteOperation)
	for _, route := range routes {
		groups[route.Prefix] = append(groups[route.Prefix], route)
	}
	for prefix := range groups {
		model.SortRoutes(groups[prefix])
	}
	return groups
}

func routeToken(group []model.RouteOperation) ([]byte, error) {
	encoded, err := json.Marshal(group)
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(encoded)
	return append([]byte(nil), digest[:8]...), nil
}

func aftEntry(entry *gribipb.AFTEntry) (string, proto.Message, bool) {
	switch {
	case entry.GetIpv4() != nil:
		return "ipv4/" + entry.GetIpv4().GetPrefix(), entry.GetIpv4(), true
	case entry.GetIpv6() != nil:
		return "ipv6/" + entry.GetIpv6().GetPrefix(), entry.GetIpv6(), true
	case entry.GetNextHopGroup() != nil:
		return fmt.Sprintf("nhg/%d", entry.GetNextHopGroup().GetId()), entry.GetNextHopGroup(), true
	case entry.GetNextHop() != nil:
		return fmt.Sprintf("nh/%d", entry.GetNextHop().GetIndex()), entry.GetNextHop(), true
	default:
		return "", nil, false
	}
}

func aftOperation(operation *gribipb.AFTOperation) proto.Message {
	switch {
	case operation.GetIpv4() != nil:
		return operation.GetIpv4()
	case operation.GetIpv6() != nil:
		return operation.GetIpv6()
	case operation.GetNextHopGroup() != nil:
		return operation.GetNextHopGroup()
	case operation.GetNextHop() != nil:
		return operation.GetNextHop()
	default:
		return nil
	}
}

type builtEntry struct {
	key   string
	kind  int
	entry fluent.GRIBIEntry
}

const (
	kindNextHop = iota + 1
	kindNextHopGroup
	kindIP
)

func entriesForRoutes(networkInstance string, routes []model.RouteOperation) ([]builtEntry, error) {
	groups := routeGroups(routes)
	for _, route := range routes {
		if route.Prefix == "" || route.NextHop == "" {
			return nil, errors.New("gRIBI route prefix and next-hop are required")
		}
		if _, err := netip.ParsePrefix(route.Prefix); err != nil {
			return nil, fmt.Errorf("gRIBI prefix %q: %w", route.Prefix, err)
		}
		if _, err := netip.ParseAddr(route.NextHop); err != nil {
			return nil, fmt.Errorf("gRIBI next-hop %q: %w", route.NextHop, err)
		}
	}
	var output []builtEntry
	prefixes := make([]string, 0, len(groups))
	for prefix := range groups {
		prefixes = append(prefixes, prefix)
	}
	sort.Strings(prefixes)
	for _, prefix := range prefixes {
		group := groups[prefix]
		model.SortRoutes(group)
		roles := map[string][]model.RouteOperation{"primary": {}, "backup": {}}
		for _, route := range group {
			role := route.PathRole
			if role != "backup" {
				role = "primary"
			}
			roles[role] = append(roles[role], route)
		}
		roleGroupIDs := make(map[string]uint64)
		for _, role := range []string{"backup", "primary"} {
			roleRoutes := roles[role]
			if len(roleRoutes) == 0 {
				continue
			}
			groupID := stableID("nhg", networkInstance, prefix, role)
			roleGroupIDs[role] = groupID
			nhg := fluent.NextHopGroupEntry().WithNetworkInstance(networkInstance).WithID(groupID)
			seenNextHop := make(map[uint64]bool)
			for _, route := range roleRoutes {
				nextHopID := stableID("nh", networkInstance, route.NextHop)
				if !seenNextHop[nextHopID] {
					nextHop := fluent.NextHopEntry().WithNetworkInstance(networkInstance).WithIndex(nextHopID).WithIPAddress(route.NextHop)
					output = append(output, builtEntry{key: fmt.Sprintf("nh/%d", nextHopID), kind: kindNextHop, entry: nextHop})
					seenNextHop[nextHopID] = true
				}
				nhg.AddNextHop(nextHopID, 1)
			}
			if role == "primary" && roleGroupIDs["backup"] != 0 {
				nhg.WithBackupNHG(roleGroupIDs["backup"])
			}
			output = append(output, builtEntry{key: fmt.Sprintf("nhg/%d", groupID), kind: kindNextHopGroup, entry: nhg})
		}
		primaryID := roleGroupIDs["primary"]
		if primaryID == 0 {
			primaryID = roleGroupIDs["backup"]
		}
		metadata, err := routeToken(group)
		if err != nil {
			return nil, err
		}
		parsed := netip.MustParsePrefix(prefix)
		if parsed.Addr().Is4() {
			entry := fluent.IPv4Entry().WithNetworkInstance(networkInstance).WithPrefix(prefix).WithNextHopGroup(primaryID).WithMetadata(metadata)
			output = append(output, builtEntry{key: "ipv4/" + prefix, kind: kindIP, entry: entry})
		} else {
			entry := fluent.IPv6Entry().WithNetworkInstance(networkInstance).WithPrefix(prefix).WithNextHopGroup(primaryID).WithMetadata(metadata)
			output = append(output, builtEntry{key: "ipv6/" + prefix, kind: kindIP, entry: entry})
		}
	}
	return deduplicateEntries(output), nil
}

func deduplicateEntries(input []builtEntry) []builtEntry {
	byKey := make(map[string]builtEntry, len(input))
	for _, entry := range input {
		byKey[entry.key] = entry
	}
	output := make([]builtEntry, 0, len(byKey))
	for _, entry := range byKey {
		output = append(output, entry)
	}
	sort.Slice(output, func(i, j int) bool {
		if output[i].kind != output[j].kind {
			return output[i].kind < output[j].kind
		}
		return output[i].key < output[j].key
	})
	return output
}

// diffEntries returns ordered stages: new dependencies, route switch, stale
// routes, stale groups, and stale next hops. This is make-before-break at the
// AFT dependency level.
func diffEntries(previous, desired []builtEntry) ([][]*gribipb.AFTOperation, error) {
	old := make(map[string]builtEntry, len(previous))
	current := make(map[string]builtEntry, len(desired))
	for _, entry := range previous {
		old[entry.key] = entry
	}
	for _, entry := range desired {
		current[entry.key] = entry
	}
	stages := make([][]*gribipb.AFTOperation, 5)
	for _, entry := range desired {
		operation := gribipb.AFTOperation_ADD
		if prior, exists := old[entry.key]; exists {
			oldProto, oldErr := prior.entry.OpProto()
			newProto, newErr := entry.entry.OpProto()
			if oldErr != nil || newErr != nil {
				return nil, errors.Join(oldErr, newErr)
			}
			if proto.Equal(oldProto, newProto) {
				continue
			}
			operation = gribipb.AFTOperation_REPLACE
		}
		op, err := entry.entry.OpProto()
		if err != nil {
			return nil, err
		}
		op.Op = operation
		stage := 0
		if entry.kind == kindIP {
			stage = 1
		}
		stages[stage] = append(stages[stage], op)
	}
	for _, entry := range previous {
		if _, exists := current[entry.key]; exists {
			continue
		}
		op, err := entry.entry.OpProto()
		if err != nil {
			return nil, err
		}
		op.Op = gribipb.AFTOperation_DELETE
		stage := 2
		if entry.kind == kindNextHopGroup {
			stage = 3
		} else if entry.kind == kindNextHop {
			stage = 4
		}
		stages[stage] = append(stages[stage], op)
	}
	return stages, nil
}

func stableID(parts ...string) uint64 {
	digest := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	value := binary.BigEndian.Uint64(digest[:8]) & ((1 << 63) - 1)
	if value == 0 {
		return 1
	}
	return value
}

func routeKey(route model.RouteOperation) string {
	return strings.Join([]string{route.Device, route.Prefix, route.NextHop, route.PathRole, fmt.Sprint(route.Metric), route.IntentID}, "\x00")
}
