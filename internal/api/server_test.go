package api_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/starfabric/starfabric/internal/api"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/testutil"
)

func TestAuthenticationAndReconcile(t *testing.T) {
	t.Parallel()
	application, err := app.New(app.Config{}, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	handler := api.New(application, "secret", observability.NewLogger(io.Discard)).Handler()
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/status", nil))
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("status without token = %d", recorder.Code)
	}
	request := httptest.NewRequest(http.MethodPost, "/api/v1/reconcile", nil)
	request.Header.Set("Authorization", "Bearer secret")
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("reconcile status = %d", recorder.Code)
	}
}

func TestEveryDocumentedRouteExecutes(t *testing.T) {
	t.Parallel()
	application, err := app.New(app.Config{}, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = application.Close() })
	handler := api.New(application, "secret", observability.NewLogger(io.Discard)).Handler()

	request := func(method, path string, body any, want int) map[string]any {
		t.Helper()
		var encoded []byte
		if body != nil {
			encoded, err = json.Marshal(body)
			if err != nil {
				t.Fatal(err)
			}
		}
		req := httptest.NewRequest(method, path, bytes.NewReader(encoded))
		req.Header.Set("Authorization", "Bearer secret")
		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, req)
		if recorder.Code != want {
			t.Fatalf("%s %s status=%d want=%d body=%s", method, path, recorder.Code, want, recorder.Body.String())
		}
		if recorder.Body.Len() == 0 || path == "/metrics" {
			return nil
		}
		var response map[string]any
		if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
			t.Fatalf("%s %s returned invalid JSON: %v", method, path, err)
		}
		return response
	}

	request(http.MethodGet, "/healthz", nil, http.StatusOK)
	request(http.MethodGet, "/readyz", nil, http.StatusOK)
	request(http.MethodGet, "/metrics", nil, http.StatusOK)
	request(http.MethodGet, "/api/v1/topology", nil, http.StatusOK)
	intents := request(http.MethodGet, "/api/v1/intents", nil, http.StatusOK)
	request(http.MethodPut, "/api/v1/intents", map[string]any{"intents": intents["intents"]}, http.StatusOK)
	request(http.MethodPost, "/api/v1/plans/preview", nil, http.StatusOK)
	request(http.MethodPost, "/api/v1/reconcile", nil, http.StatusOK)
	request(http.MethodGet, "/api/v1/status", nil, http.StatusOK)
	request(http.MethodGet, "/api/v1/devices", nil, http.StatusOK)
	request(http.MethodPost, "/api/v1/faults/device", map[string]any{"device": "a", "mode": ""}, http.StatusAccepted)

	predictiveRequest := map[string]any{"windows": []any{}, "horizon_seconds": 60, "lead_seconds": 2}
	request(http.MethodPost, "/api/v1/predictive/forecast", predictiveRequest, http.StatusOK)
	schedule := request(http.MethodPost, "/api/v1/predictive/schedules", predictiveRequest, http.StatusAccepted)
	request(http.MethodGet, "/api/v1/predictive/schedules", nil, http.StatusOK)
	request(http.MethodGet, "/api/v1/predictive/schedules?id="+schedule["id"].(string), nil, http.StatusOK)

	now := time.Now().UTC()
	link := testutil.Diamond().Links[0]
	event := model.TopologyEvent{
		EventID: "api-contract-event", Subject: "ab", Sequence: 1, Type: model.EventLinkDown,
		ObservedAt: now, EffectiveAt: now, Link: &link,
	}
	request(http.MethodPost, "/api/v1/topology/events", event, http.StatusAccepted)
	event.EventID, event.Sequence, event.Type = "api-contract-batch", 2, model.EventLinkUp
	batch := map[string]any{"events": []model.TopologyEvent{event}}
	accepted := request(http.MethodPost, "/api/v1/topology/events/batch", batch, http.StatusAccepted)
	if accepted["topology"].(map[string]any)["version"] != float64(3) {
		t.Fatalf("batch must publish one version: %#v", accepted)
	}
	request(http.MethodPost, "/api/v1/topology/events/batch", batch, http.StatusConflict)
	request(http.MethodPost, "/api/v1/plans/preview", map[string]any{}, http.StatusOK)
}

func TestPredictiveRequestRejectsUnknownAndUnsafeDurations(t *testing.T) {
	t.Parallel()
	application, err := app.New(app.Config{}, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = application.Close() })
	handler := api.New(application, "", observability.NewLogger(io.Discard)).Handler()
	for _, body := range []string{
		`{"windows":[],"horizon_seconds":0}`,
		`{"windows":[],"horizon_seconds":86401}`,
		`{"windows":[],"horizon_seconds":60,"unknown":true}`,
	} {
		req := httptest.NewRequest(http.MethodPost, "/api/v1/predictive/forecast", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, req)
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("body %s status=%d response=%s", body, recorder.Code, recorder.Body.String())
		}
	}
}
