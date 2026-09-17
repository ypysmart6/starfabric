package sharding

import "testing"

func TestStableAndFailureDomainDiverseAssignment(t *testing.T) {
	controllers := []Controller{{ID: "a", Region: "east", Healthy: true}, {ID: "b", Region: "east", Healthy: true}, {ID: "c", Region: "west", Healthy: true}}
	first, err := Assign("orbital-plane-07", controllers, 2)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Assign("orbital-plane-07", controllers, 2)
	if err != nil {
		t.Fatal(err)
	}
	if first[0] != second[0] || first[1] != second[1] {
		t.Fatalf("assignment is not deterministic: %v %v", first, second)
	}
	if first[0].Region == first[1].Region {
		t.Fatalf("failure domains not diversified: %v", first)
	}
}

func TestUnhealthyControllerRemoved(t *testing.T) {
	controllers := []Controller{{ID: "a", Region: "east", Healthy: false}, {ID: "b", Region: "west", Healthy: true}}
	assigned, err := Assign("sat-42", controllers, 1)
	if err != nil {
		t.Fatal(err)
	}
	if assigned[0].ID != "b" {
		t.Fatalf("unhealthy owner selected: %v", assigned)
	}
}
