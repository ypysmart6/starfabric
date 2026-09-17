package validation

import (
	"errors"
	"fmt"
	"net/netip"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

// Plan checks control-plane safety before an adapter sees a change.
func Plan(plan model.RoutePlan, snapshot model.TopologySnapshot, now time.Time) error {
	if plan.ID == "" {
		return errors.New("plan id is required")
	}
	if plan.TopologyVersion != snapshot.Version {
		return fmt.Errorf("topology version mismatch: plan=%d current=%d", plan.TopologyVersion, snapshot.Version)
	}
	if !plan.ExpiresAt.After(now) {
		return errors.New("route plan has expired")
	}
	nodes := make(map[string]model.Node, len(snapshot.Nodes))
	usableEdges := make(map[string]bool, len(snapshot.Links))
	for _, node := range snapshot.Nodes {
		nodes[node.ID] = node
	}
	for _, link := range snapshot.Links {
		if link.Usable(now, 0) {
			usableEdges[link.Source+"\x00"+link.Target] = true
		}
	}
	seenRoute := make(map[string]bool)
	for _, route := range plan.Routes {
		if _, ok := nodes[route.Device]; !ok {
			return fmt.Errorf("route references unknown device %q", route.Device)
		}
		next, ok := nodes[route.NextHopNode]
		if !ok || !next.Enabled {
			return fmt.Errorf("route on %q references unavailable next hop %q", route.Device, route.NextHopNode)
		}
		address, err := nodes[route.Device].NextHopAddress(next)
		if err != nil {
			return err
		}
		if address != route.NextHop {
			return fmt.Errorf("route next-hop address mismatch for %q", route.NextHopNode)
		}
		if !usableEdges[route.Device+"\x00"+route.NextHopNode] {
			return fmt.Errorf("next hop %s -> %s is not reachable", route.Device, route.NextHopNode)
		}
		if _, err := netip.ParsePrefix(route.Prefix); err != nil {
			return fmt.Errorf("invalid prefix %q: %w", route.Prefix, err)
		}
		key := fmt.Sprintf("%s\x00%s\x00%d\x00%s", route.Device, route.Prefix, route.Metric, route.NextHopNode)
		if seenRoute[key] {
			return fmt.Errorf("duplicate route on %q for %q", route.Device, route.Prefix)
		}
		seenRoute[key] = true
	}
	for intentID, paths := range plan.Paths {
		if len(paths) == 0 {
			return fmt.Errorf("intent %q has no path", intentID)
		}
		for _, path := range paths {
			visited := make(map[string]bool, len(path.Nodes))
			for _, node := range path.Nodes {
				if visited[node] {
					return fmt.Errorf("intent %q path contains loop at %q", intentID, node)
				}
				visited[node] = true
			}
		}
	}
	return nil
}
