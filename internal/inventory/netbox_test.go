package inventory

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) { return fn(request) }

func TestSnapshot(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		body := `{"next":null,"results":[]}`
		if strings.Contains(request.URL.Path, "/devices/") {
			body = `{"next":null,"results":[{"name":"sat-01","role":{"slug":"satellite"},"primary_ip6":{"address":"2001:db8::1/128"},"custom_fields":{"gnmi_target":"sat-01:9339"}},{"name":"gw-01","role":{"slug":"gateway"},"primary_ip6":{"address":"2001:db8::2/128"}}]}`
		} else if strings.Contains(request.URL.Path, "/cables/") {
			body = `{"next":null,"results":[{"id":7,"status":{"slug":"connected"},"a_terminations":[{"object":{"device":{"name":"sat-01"}}}],"b_terminations":[{"object":{"device":{"name":"gw-01"}}}],"custom_fields":{"link_type":"rf","latency_us":4000,"capacity_bps":100000000}}]}`
		}
		return &http.Response{StatusCode: 200, Status: "200 OK", Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})}
	value, err := NewNetBoxClient("https://netbox.example/", "secret", "starfabric", client)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := value.Snapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Nodes) != 2 || len(snapshot.Links) != 2 {
		t.Fatalf("unexpected topology: %#v", snapshot)
	}
	if snapshot.Nodes[0].Loopback != "2001:db8::1" || snapshot.Nodes[0].Labels["gnmi_target"] == "" {
		t.Fatalf("device mapping failed: %#v", snapshot.Nodes[0])
	}
	if snapshot.Links[0].LinkType != "rf" || snapshot.Links[0].LatencyUS != 4000 {
		t.Fatalf("cable mapping failed: %#v", snapshot.Links[0])
	}
}

func TestRejectsInsecureRemoteEndpoint(t *testing.T) {
	if _, err := NewNetBoxClient("http://netbox.example", "", "", nil); err == nil {
		t.Fatal("expected HTTPS rejection")
	}
}
