package policy

import (
	"container/heap"
	"errors"
	"fmt"
	"math"
	"sort"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

var ErrNoPath = errors.New("no path satisfies the intent")

type Options struct {
	At                 time.Time
	MaxTelemetryAge    time.Duration
	ExcludedLinks      map[string]bool
	ExcludedNodes      map[string]bool
	ExcludedRiskGroups map[string]bool
}

type edge struct {
	link model.Link
	cost int64
}

type item struct {
	node string
	dist int64
	idx  int
}

type priorityQueue []*item

func (q priorityQueue) Len() int           { return len(q) }
func (q priorityQueue) Less(i, j int) bool { return q[i].dist < q[j].dist }
func (q priorityQueue) Swap(i, j int)      { q[i], q[j] = q[j], q[i]; q[i].idx, q[j].idx = i, j }
func (q *priorityQueue) Push(value any)    { *q = append(*q, value.(*item)) }
func (q *priorityQueue) Pop() any {
	old := *q
	item := old[len(old)-1]
	*q = old[:len(old)-1]
	return item
}

// Shortest computes a deterministic constrained shortest path. Policy values
// are latency (default), hops, capacity, and reliability.
func Shortest(snapshot model.TopologySnapshot, intent model.RouteIntent, policyName string, options Options) (model.Path, error) {
	if err := intent.Validate(); err != nil {
		return model.Path{}, err
	}
	if options.At.IsZero() {
		options.At = time.Now().UTC()
	}
	enabled := make(map[string]bool, len(snapshot.Nodes))
	for _, node := range snapshot.Nodes {
		enabled[node.ID] = node.Enabled && !options.ExcludedNodes[node.ID]
	}
	if !enabled[intent.Source] || !enabled[intent.Destination] {
		return model.Path{}, fmt.Errorf("%w: endpoint disabled or absent", ErrNoPath)
	}
	adj := make(map[string][]edge)
	for _, link := range snapshot.Links {
		if options.ExcludedLinks[link.ID] || !enabled[link.Source] || !enabled[link.Target] || !link.Usable(options.At, options.MaxTelemetryAge) {
			continue
		}
		if intersectsRiskGroups(link.RiskGroups, options.ExcludedRiskGroups) {
			continue
		}
		requiredCapacity := max(intent.MinCapacityBPS, intent.DemandBPS)
		if requiredCapacity > 0 && link.CapacityBPS < requiredCapacity {
			continue
		}
		if intent.MaxLossPPM > 0 && link.LossPPM > intent.MaxLossPPM {
			continue
		}
		if intent.RequiredReliability > 0 && link.ReliabilityPPM < intent.RequiredReliability {
			continue
		}
		adj[link.Source] = append(adj[link.Source], edge{link: link, cost: edgeCost(link, policyName)})
	}
	for node := range adj {
		sort.Slice(adj[node], func(i, j int) bool {
			if adj[node][i].cost != adj[node][j].cost {
				return adj[node][i].cost < adj[node][j].cost
			}
			return adj[node][i].link.ID < adj[node][j].link.ID
		})
	}

	distance := make(map[string]int64, len(snapshot.Nodes))
	previous := make(map[string]model.Link, len(snapshot.Nodes))
	for node := range enabled {
		distance[node] = math.MaxInt64
	}
	distance[intent.Source] = 0
	queue := priorityQueue{&item{node: intent.Source, dist: 0}}
	heap.Init(&queue)
	for queue.Len() > 0 {
		current := heap.Pop(&queue).(*item)
		if current.dist != distance[current.node] {
			continue
		}
		if current.node == intent.Destination {
			break
		}
		for _, candidate := range adj[current.node] {
			if current.dist > math.MaxInt64-candidate.cost {
				continue
			}
			nextDistance := current.dist + candidate.cost
			old, ok := distance[candidate.link.Target]
			oldLink := previous[candidate.link.Target]
			if !ok || nextDistance < old || (nextDistance == old && candidate.link.ID < oldLink.ID) {
				distance[candidate.link.Target] = nextDistance
				previous[candidate.link.Target] = candidate.link
				heap.Push(&queue, &item{node: candidate.link.Target, dist: nextDistance})
			}
		}
	}
	if distance[intent.Destination] == math.MaxInt64 {
		return model.Path{}, ErrNoPath
	}

	reversedLinks := make([]model.Link, 0)
	for node := intent.Destination; node != intent.Source; {
		link, ok := previous[node]
		if !ok {
			return model.Path{}, ErrNoPath
		}
		reversedLinks = append(reversedLinks, link)
		node = link.Source
	}
	path := model.Path{Nodes: []string{intent.Source}, CapacityBPS: math.MaxInt64, Cost: distance[intent.Destination]}
	riskGroups := make(map[string]bool)
	for i := len(reversedLinks) - 1; i >= 0; i-- {
		link := reversedLinks[i]
		path.Links = append(path.Links, link.ID)
		path.Nodes = append(path.Nodes, link.Target)
		path.LatencyUS += link.LatencyUS
		path.LossPPM = combinedLoss(path.LossPPM, link.LossPPM)
		if link.CapacityBPS < path.CapacityBPS {
			path.CapacityBPS = link.CapacityBPS
		}
		for _, group := range link.RiskGroups {
			riskGroups[group] = true
		}
	}
	for group := range riskGroups {
		path.RiskGroups = append(path.RiskGroups, group)
	}
	sort.Strings(path.RiskGroups)
	if intent.MaxLatencyUS > 0 && path.LatencyUS > intent.MaxLatencyUS {
		return model.Path{}, fmt.Errorf("%w: latency %d exceeds %d", ErrNoPath, path.LatencyUS, intent.MaxLatencyUS)
	}
	return path, nil
}

func DisjointBackup(snapshot model.TopologySnapshot, intent model.RouteIntent, primary model.Path, nodeDisjoint bool, options Options) (model.Path, error) {
	if options.ExcludedLinks == nil {
		options.ExcludedLinks = make(map[string]bool)
	}
	for _, id := range primary.Links {
		options.ExcludedLinks[id] = true
	}
	// Exclude reverse edges as well; otherwise a backup may use the same
	// physical inter-satellite link in the opposite direction.
	primaryEdges := make(map[string]bool)
	for i := 0; i+1 < len(primary.Nodes); i++ {
		primaryEdges[primary.Nodes[i]+"\x00"+primary.Nodes[i+1]] = true
		primaryEdges[primary.Nodes[i+1]+"\x00"+primary.Nodes[i]] = true
	}
	for _, link := range snapshot.Links {
		if primaryEdges[link.Source+"\x00"+link.Target] {
			options.ExcludedLinks[link.ID] = true
		}
	}
	if options.ExcludedRiskGroups == nil {
		options.ExcludedRiskGroups = make(map[string]bool)
	}
	for _, group := range primary.RiskGroups {
		options.ExcludedRiskGroups[group] = true
	}
	if nodeDisjoint {
		if options.ExcludedNodes == nil {
			options.ExcludedNodes = make(map[string]bool)
		}
		for _, node := range primary.Nodes[1 : len(primary.Nodes)-1] {
			options.ExcludedNodes[node] = true
		}
	}
	return Shortest(snapshot, intent, intent.Policy, options)
}

func intersectsRiskGroups(groups []string, excluded map[string]bool) bool {
	for _, group := range groups {
		if excluded[group] {
			return true
		}
	}
	return false
}

func edgeCost(link model.Link, policyName string) int64 {
	switch policyName {
	case "hops":
		return 1
	case "capacity":
		// Higher capacity becomes lower cost while retaining latency as a
		// deterministic tie breaker.
		return 1_000_000_000_000/link.CapacityBPS + max(1, link.LatencyUS/1_000)
	case "reliability":
		return max(1, 1_000_000-link.ReliabilityPPM) + max(1, link.LatencyUS/100)
	default:
		return max(1, link.LatencyUS)
	}
}

func combinedLoss(current, next int64) int64 {
	// loss_total = 1 - product(1-loss_i), represented in parts per million.
	return current + next - (current*next)/1_000_000
}

func max(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
