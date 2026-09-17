package openconfig

import (
	"testing"

	"github.com/starfabric/starfabric/internal/model"
)

func TestParsePath(t *testing.T) {
	path, err := ParsePath("/interfaces/interface[name=eth0]/state/counters")
	if err != nil {
		t.Fatal(err)
	}
	if got := path.GetElem()[1].GetKey()["name"]; got != "eth0" {
		t.Fatalf("key = %q, want eth0", got)
	}
	if _, err := ParsePath("/interfaces/interface[name]"); err == nil {
		t.Fatal("malformed predicate accepted")
	}
}

func TestGRIBIDiffIsMakeBeforeBreak(t *testing.T) {
	oldRoutes := []model.RouteOperation{{Device: "r1", Prefix: "203.0.113.0/24", NextHop: "192.0.2.1", PathRole: "primary"}}
	newRoutes := []model.RouteOperation{{Device: "r1", Prefix: "203.0.113.0/24", NextHop: "192.0.2.2", PathRole: "primary"}}
	oldEntries, err := entriesForRoutes("DEFAULT", oldRoutes)
	if err != nil {
		t.Fatal(err)
	}
	newEntries, err := entriesForRoutes("DEFAULT", newRoutes)
	if err != nil {
		t.Fatal(err)
	}
	stages, err := diffEntries(oldEntries, newEntries)
	if err != nil {
		t.Fatal(err)
	}
	if len(stages[0]) == 0 || len(stages[1]) == 0 || len(stages[4]) == 0 {
		t.Fatalf("expected dependency, route-switch and cleanup stages, got sizes %d/%d/%d", len(stages[0]), len(stages[1]), len(stages[4]))
	}
}

func TestGRIBIEntriesCarryPrimaryAndBackup(t *testing.T) {
	routes := []model.RouteOperation{
		{Device: "r1", Prefix: "2001:db8::/64", NextHop: "2001:db8::1", PathRole: "primary"},
		{Device: "r1", Prefix: "2001:db8::/64", NextHop: "2001:db8::2", PathRole: "backup"},
	}
	entries, err := entriesForRoutes("DEFAULT", routes)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 5 {
		t.Fatalf("entries = %d, want 5 (2 NH, 2 NHG, 1 IPv6)", len(entries))
	}
	for _, entry := range entries {
		if entry.kind != kindIP {
			continue
		}
		op, err := entry.entry.OpProto()
		if err != nil {
			t.Fatal(err)
		}
		if got := len(op.GetIpv6().GetIpv6Entry().GetEntryMetadata().GetValue()); got != 8 {
			t.Fatalf("entry metadata length = %d, want OpenConfig maximum 8", got)
		}
	}
}
