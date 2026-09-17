package ois

import (
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

func TestOpticalLinkOnlyUsableAfterLock(t *testing.T) {
	now := time.Now().UTC()
	runtime := New(now)
	runtime.RemoteReady = true
	for _, event := range []Event{StartSearch, BeaconDetected, LockAchieved} {
		if err := runtime.Apply(event, now); err != nil {
			t.Fatal(err)
		}
	}
	link := runtime.Project(model.Link{ID: "laser", AdminUp: true, CapacityBPS: 10_000_000_000}, now)
	if !link.Usable(now, 0) {
		t.Fatal("locked optical link should be usable")
	}
	if err := runtime.Apply(LockLost, now); err != nil {
		t.Fatal(err)
	}
	if runtime.Project(link, now).Usable(now, 0) {
		t.Fatal("link with lost optical lock must not be usable")
	}
}
