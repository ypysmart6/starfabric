package leadership

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestLeaseElectionAndTakeover(t *testing.T) {
	var mu sync.Mutex
	var current *lease
	handler := func(r *http.Request) *http.Response {
		mu.Lock()
		defer mu.Unlock()
		status := http.StatusOK
		var body []byte
		switch r.Method {
		case http.MethodGet:
			if current == nil {
				status = http.StatusNotFound
				break
			}
			body, _ = json.Marshal(current)
		case http.MethodPost:
			if current != nil {
				status = http.StatusConflict
				break
			}
			var value lease
			json.NewDecoder(r.Body).Decode(&value)
			validateLeaseTimes(t, value)
			value.Metadata.ResourceVersion = "1"
			current = &value
			status = http.StatusCreated
		case http.MethodPut:
			var value lease
			json.NewDecoder(r.Body).Decode(&value)
			validateLeaseTimes(t, value)
			value.Metadata.ResourceVersion = "2"
			current = &value
		}
		return &http.Response{StatusCode: status, Body: io.NopCloser(bytes.NewReader(body)), Header: make(http.Header)}
	}
	client := &http.Client{Transport: roundTripFunc(handler)}
	config := Config{APIEndpoint: "http://kubernetes", Namespace: "network", LeaseName: "sf", Identity: "pod-a", LeaseDuration: 10 * time.Second, RetryPeriod: time.Second}
	elector, err := newWithClient(config, client, "token")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 1, 1, 0, 0, 0, 123456789, time.UTC)
	if leader, err := elector.tryAcquireOrRenew(context.Background(), now); err != nil || !leader {
		t.Fatalf("initial acquire leader=%v err=%v", leader, err)
	}
	if leader, err := elector.tryAcquireOrRenew(context.Background(), now.Add(time.Second)); err != nil || !leader {
		t.Fatalf("renew leader=%v err=%v", leader, err)
	}
	otherConfig := config
	otherConfig.Identity = "pod-b"
	other, _ := newWithClient(otherConfig, client, "token")
	if leader, err := other.tryAcquireOrRenew(context.Background(), now.Add(5*time.Second)); err != nil || leader {
		t.Fatalf("unexpired takeover leader=%v err=%v", leader, err)
	}
	if leader, err := other.tryAcquireOrRenew(context.Background(), now.Add(12*time.Second)); err != nil || !leader {
		t.Fatalf("expired takeover leader=%v err=%v", leader, err)
	}
}

func validateLeaseTimes(t *testing.T, value lease) {
	t.Helper()
	for _, stamp := range []string{value.Spec.AcquireTime, value.Spec.RenewTime} {
		if _, err := time.Parse("2006-01-02T15:04:05.000000Z07:00", stamp); err != nil {
			t.Fatalf("Lease timestamp rejected by Kubernetes MicroTime: %q: %v", stamp, err)
		}
	}
}

type roundTripFunc func(*http.Request) *http.Response

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request), nil
}

func TestProjectedTokenRotationAndReadFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(path, []byte("initial\n"), 0600); err != nil {
		t.Fatal(err)
	}
	var observed []string
	client := &http.Client{Transport: roundTripFunc(func(r *http.Request) *http.Response {
		observed = append(observed, r.Header.Get("Authorization"))
		return &http.Response{StatusCode: http.StatusNotFound, Body: io.NopCloser(bytes.NewReader(nil)), Header: make(http.Header)}
	})}
	elector, err := newWithClient(Config{APIEndpoint: "http://kubernetes", Namespace: "network", LeaseName: "sf", TokenPath: path}, client, "cached-initial")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := elector.get(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path+".next", []byte("rotated\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path+".next", path); err != nil {
		t.Fatal(err)
	}
	if _, _, err := elector.get(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(observed) != 2 || observed[0] != "Bearer initial" || observed[1] != "Bearer rotated" {
		t.Fatal("request did not observe the replacement projected token")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, _, err := elector.get(context.Background()); err == nil || len(observed) != 2 {
		t.Fatal("missing token must fail before sending a cached credential")
	}
	if err := os.WriteFile(path, []byte(" \n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := elector.write(context.Background(), http.MethodPost, elector.collectionURL(), lease{}); err == nil || len(observed) != 2 {
		t.Fatal("empty token must fail before sending a Lease mutation")
	}
}
