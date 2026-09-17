package verification

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

func TestOTGProbeUsesCounterDelta(t *testing.T) {
	t.Parallel()
	var mu sync.Mutex
	requests := 0
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != "/monitor/metrics" || request.Method != http.MethodPost {
			return response(http.StatusNotFound, "unexpected request"), nil
		}
		mu.Lock()
		requests++
		sample := requests
		mu.Unlock()
		tx, rx := 1000, 995
		if sample > 1 {
			tx, rx = 1100, 1094
		}
		return response(http.StatusOK, fmt.Sprintf(`{"choice":"flow_metrics","flow_metrics":[{"name":"service","frames_tx":"%d","frames_rx":"%d"}]}`, tx, rx)), nil
	})
	probe, err := NewOTGProbe(OTGConfig{Endpoint: "http://otg.test", HTTPTransport: transport, SampleWindow: time.Millisecond, MaxLossPercent: 2, MinFrames: 50, FlowNames: []string{"service"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := probe.Verify(context.Background(), model.RoutePlan{}); err != nil {
		t.Fatal(err)
	}
}

func TestOTGProbeRejectsLoss(t *testing.T) {
	t.Parallel()
	request := 0
	transport := roundTripFunc(func(_ *http.Request) (*http.Response, error) {
		request++
		tx, rx := 100, 100
		if request > 1 {
			tx, rx = 200, 175
		}
		return response(http.StatusOK, fmt.Sprintf(`{"flow_metrics":[{"name":"service","frames_tx":%d,"frames_rx":%d}]}`, tx, rx)), nil
	})
	probe, err := NewOTGProbe(OTGConfig{Endpoint: "http://otg.test", HTTPTransport: transport, SampleWindow: time.Millisecond, MaxLossPercent: 5, MinFrames: 50})
	if err != nil {
		t.Fatal(err)
	}
	if err := probe.Verify(context.Background(), model.RoutePlan{}); err == nil {
		t.Fatal("loss SLO violation was accepted")
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func response(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}
}
