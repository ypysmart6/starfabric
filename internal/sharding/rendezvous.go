package sharding

import (
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"sort"
)

type Controller struct {
	ID      string
	Region  string
	Healthy bool
}

type scored struct {
	controller Controller
	score      uint64
}

// Assign uses rendezvous hashing to give an orbital-plane or node shard a
// stable ordered owner set. It first maximizes failure-domain diversity, then
// fills any remaining replicas. Lease fencing is still required before an
// assigned controller may mutate devices.
func Assign(shard string, controllers []Controller, replicas int) ([]Controller, error) {
	if shard == "" || replicas <= 0 {
		return nil, errors.New("shard and positive replica count are required")
	}
	scores := make([]scored, 0, len(controllers))
	seenIDs := make(map[string]bool)
	for _, controller := range controllers {
		if controller.ID == "" || seenIDs[controller.ID] {
			return nil, errors.New("controller IDs must be non-empty and unique")
		}
		seenIDs[controller.ID] = true
		if !controller.Healthy {
			continue
		}
		digest := sha256.Sum256([]byte(shard + "\x00" + controller.ID))
		scores = append(scores, scored{controller: controller, score: binary.BigEndian.Uint64(digest[:8])})
	}
	if len(scores) < replicas {
		return nil, errors.New("not enough healthy controllers for requested replicas")
	}
	sort.Slice(scores, func(i, j int) bool {
		if scores[i].score != scores[j].score {
			return scores[i].score > scores[j].score
		}
		return scores[i].controller.ID < scores[j].controller.ID
	})
	result := make([]Controller, 0, replicas)
	regions := make(map[string]bool)
	selected := make(map[string]bool)
	for _, candidate := range scores {
		if regions[candidate.controller.Region] {
			continue
		}
		result = append(result, candidate.controller)
		regions[candidate.controller.Region] = true
		selected[candidate.controller.ID] = true
		if len(result) == replicas {
			return result, nil
		}
	}
	for _, candidate := range scores {
		if selected[candidate.controller.ID] {
			continue
		}
		result = append(result, candidate.controller)
		if len(result) == replicas {
			return result, nil
		}
	}
	return result, nil
}
