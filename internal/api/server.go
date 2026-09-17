package api

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/predictive"
	"github.com/starfabric/starfabric/internal/topology"
)

const maxRequestBytes = 1 << 20

type Server struct {
	app    *app.App
	token  string
	logger *observability.Logger
	mux    *http.ServeMux
}

func New(application *app.App, token string, logger *observability.Logger) *Server {
	if logger == nil {
		logger = observability.NewLogger(ioDiscard{})
	}
	server := &Server{app: application, token: token, logger: logger, mux: http.NewServeMux()}
	server.routes()
	return server
}

type ioDiscard struct{}

func (ioDiscard) Write(p []byte) (int, error) { return len(p), nil }

func (s *Server) Handler() http.Handler {
	return s.requestContext(s.accessLog(s.authenticate(s.mux)))
}

// NewHealthHandler exposes only liveness and readiness. It is intended for a
// cluster-internal listener so kubelet does not need possession of an API
// client certificate. Network policy must restrict this listener to the
// cluster control plane and monitoring namespace.
func NewHealthHandler(application *app.App) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "time": time.Now().UTC()})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		if !application.IsLeader() {
			writeError(w, http.StatusServiceUnavailable, "not_leader", app.ErrNotLeader)
			return
		}
		if err := application.Topology().Validate(); err != nil {
			writeError(w, http.StatusServiceUnavailable, "not_ready", err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		_ = application.Metrics().WritePrometheus(w)
	})
	return mux
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /readyz", s.ready)
	s.mux.HandleFunc("GET /metrics", s.metrics)
	s.mux.HandleFunc("GET /api/v1/topology", s.getTopology)
	s.mux.HandleFunc("POST /api/v1/topology/events", s.applyEvent)
	s.mux.HandleFunc("POST /api/v1/topology/events/batch", s.applyEvents)
	s.mux.HandleFunc("GET /api/v1/intents", s.getIntents)
	s.mux.HandleFunc("PUT /api/v1/intents", s.putIntents)
	s.mux.HandleFunc("POST /api/v1/plans/preview", s.preview)
	s.mux.HandleFunc("POST /api/v1/reconcile", s.reconcile)
	s.mux.HandleFunc("GET /api/v1/status", s.status)
	s.mux.HandleFunc("GET /api/v1/devices", s.devices)
	s.mux.HandleFunc("POST /api/v1/faults/device", s.deviceFault)
	s.mux.HandleFunc("POST /api/v1/predictive/forecast", s.predictiveForecast)
	s.mux.HandleFunc("POST /api/v1/predictive/schedules", s.createPredictiveSchedule)
	s.mux.HandleFunc("GET /api/v1/predictive/schedules", s.getPredictiveSchedules)
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "time": time.Now().UTC()})
}

func (s *Server) ready(w http.ResponseWriter, _ *http.Request) {
	if !s.app.IsLeader() {
		writeError(w, http.StatusServiceUnavailable, "not_leader", app.ErrNotLeader)
		return
	}
	if err := s.app.Topology().Validate(); err != nil {
		writeError(w, http.StatusServiceUnavailable, "not_ready", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	if err := s.app.Metrics().WritePrometheus(w); err != nil {
		s.logger.Event("error", "metrics response failed", map[string]any{"error": err.Error()})
	}
}

func (s *Server) getTopology(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.app.Topology())
}

func (s *Server) applyEvent(w http.ResponseWriter, r *http.Request) {
	var event model.TopologyEvent
	if err := decodeJSON(w, r, &event); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	snapshot, err := s.app.ApplyEvent(event)
	if err != nil {
		if errors.Is(err, app.ErrNotLeader) {
			writeError(w, http.StatusServiceUnavailable, "not_leader", err)
			return
		}
		status := http.StatusUnprocessableEntity
		if errors.Is(err, topology.ErrDuplicateEvent) {
			status = http.StatusConflict
		}
		writeError(w, status, "topology_event_rejected", err)
		return
	}
	response := map[string]any{"topology": snapshot}
	if queryBool(r, "reconcile") {
		plan, status, reconcileErr := s.app.Reconcile(r.Context())
		response["plan"] = plan
		response["status"] = status
		if reconcileErr != nil {
			writeError(w, http.StatusConflict, "reconcile_failed", reconcileErr)
			return
		}
	}
	writeJSON(w, http.StatusAccepted, response)
}

func (s *Server) getIntents(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"intents": s.app.Intents()})
}

func (s *Server) applyEvents(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Events []model.TopologyEvent `json:"events"`
	}
	if err := decodeJSONLimit(w, r, &request, 8<<20); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	snapshot, err := s.app.ApplyEvents(request.Events)
	if err != nil {
		status := http.StatusUnprocessableEntity
		code := "topology_event_rejected"
		if errors.Is(err, app.ErrNotLeader) {
			status, code = http.StatusServiceUnavailable, "not_leader"
		}
		if errors.Is(err, topology.ErrDuplicateEvent) {
			status = http.StatusConflict
		}
		writeError(w, status, code, err)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"topology": snapshot})
}

func (s *Server) putIntents(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Intents []model.RouteIntent `json:"intents"`
	}
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	if err := s.app.SetIntents(request.Intents); err != nil {
		if errors.Is(err, app.ErrNotLeader) {
			writeError(w, http.StatusServiceUnavailable, "not_leader", err)
			return
		}
		writeError(w, http.StatusUnprocessableEntity, "invalid_intents", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"intents": s.app.Intents()})
}

func (s *Server) preview(w http.ResponseWriter, _ *http.Request) {
	plan, err := s.app.Preview()
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "planning_failed", err)
		return
	}
	writeJSON(w, http.StatusOK, plan)
}

func (s *Server) reconcile(w http.ResponseWriter, r *http.Request) {
	plan, status, err := s.app.Reconcile(r.Context())
	if err != nil {
		if errors.Is(err, app.ErrNotLeader) {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": map[string]any{"code": "not_leader", "message": err.Error(), "request_id": requestID(r.Context())}})
			return
		}
		writeJSON(w, http.StatusConflict, map[string]any{"error": map[string]any{"code": "reconcile_failed", "message": err.Error(), "request_id": requestID(r.Context())}, "plan": plan, "status": status})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"plan": plan, "status": status})
}

func (s *Server) status(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"reconcile": s.app.Status(), "committed_plan": s.app.CommittedPlan()})
}

func (s *Server) devices(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"devices": s.app.DeviceStates(r.Context())})
}

func (s *Server) deviceFault(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Device string            `json:"device"`
		Mode   adapter.FaultMode `json:"mode"`
	}
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	if err := s.app.SetDeviceFault(request.Device, request.Mode); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "fault_rejected", err)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"device": request.Device, "mode": request.Mode})
}

type predictiveRequest struct {
	Windows        []predictive.ContactWindow `json:"windows"`
	HorizonSeconds int64                      `json:"horizon_seconds"`
	LeadSeconds    int64                      `json:"lead_seconds,omitempty"`
}

func (request predictiveRequest) durations() (time.Duration, time.Duration, error) {
	if request.HorizonSeconds <= 0 {
		return 0, 0, errors.New("horizon_seconds must be positive")
	}
	if request.LeadSeconds < 0 {
		return 0, 0, errors.New("lead_seconds cannot be negative")
	}
	const maxSeconds = int64((24 * time.Hour) / time.Second)
	if request.HorizonSeconds > maxSeconds || request.LeadSeconds > maxSeconds {
		return 0, 0, errors.New("predictive horizon and lead cannot exceed 24 hours")
	}
	return time.Duration(request.HorizonSeconds) * time.Second, time.Duration(request.LeadSeconds) * time.Second, nil
}

func (s *Server) predictiveForecast(w http.ResponseWriter, r *http.Request) {
	var request predictiveRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	horizon, lead, err := request.durations()
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	activations, err := s.app.Forecast(request.Windows, horizon, lead)
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "forecast_failed", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"activations": activations})
}

func (s *Server) createPredictiveSchedule(w http.ResponseWriter, r *http.Request) {
	var request predictiveRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	horizon, lead, err := request.durations()
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err)
		return
	}
	schedule, err := s.app.CreatePredictiveSchedule(request.Windows, horizon, lead)
	if err != nil {
		if errors.Is(err, app.ErrNotLeader) {
			writeError(w, http.StatusServiceUnavailable, "not_leader", err)
			return
		}
		writeError(w, http.StatusUnprocessableEntity, "schedule_failed", err)
		return
	}
	writeJSON(w, http.StatusAccepted, schedule)
}

func (s *Server) getPredictiveSchedules(w http.ResponseWriter, r *http.Request) {
	if id := strings.TrimSpace(r.URL.Query().Get("id")); id != "" {
		schedule, exists := s.app.PredictiveSchedule(id)
		if !exists {
			writeError(w, http.StatusNotFound, "not_found", errors.New("predictive schedule not found"))
			return
		}
		writeJSON(w, http.StatusOK, schedule)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"schedules": s.app.PredictiveSchedules()})
}

func (s *Server) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s.token == "" || r.URL.Path == "/healthz" || r.URL.Path == "/readyz" {
			next.ServeHTTP(w, r)
			return
		}
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if len(provided) != len(s.token) || subtle.ConstantTimeCompare([]byte(provided), []byte(s.token)) != 1 {
			writeError(w, http.StatusUnauthorized, "unauthorized", errors.New("valid bearer token required"))
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) requestContext(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if id == "" || len(id) > 128 {
			id = newRequestID()
		}
		w.Header().Set("X-Request-ID", id)
		ctx := context.WithValue(r.Context(), requestIDKey{}, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

func (s *Server) accessLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)
		s.logger.Event("info", "http request", map[string]any{
			"request_id": requestID(r.Context()), "method": r.Method, "path": r.URL.Path,
			"status": recorder.status, "duration_us": time.Since(started).Microseconds(),
		})
	})
}

func decodeJSON(w http.ResponseWriter, r *http.Request, destination any) error {
	return decodeJSONLimit(w, r, destination, maxRequestBytes)
}

func decodeJSONLimit(w http.ResponseWriter, r *http.Request, destination any, limit int64) error {
	if contentType := r.Header.Get("Content-Type"); contentType != "" && !strings.HasPrefix(contentType, "application/json") {
		return errors.New("Content-Type must be application/json")
	}
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err == nil {
		return errors.New("request body must contain one JSON value")
	} else if !errors.Is(err, io.EOF) {
		return err
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code string, err error) {
	writeJSON(w, status, map[string]any{"error": map[string]any{"code": code, "message": err.Error()}})
}

func queryBool(r *http.Request, key string) bool {
	value, err := strconv.ParseBool(r.URL.Query().Get(key))
	return err == nil && value
}

func newRequestID() string {
	buffer := make([]byte, 12)
	if _, err := rand.Read(buffer); err != nil {
		return fmt.Sprintf("req-%d", time.Now().UnixNano())
	}
	return "req-" + hex.EncodeToString(buffer)
}

type requestIDKey struct{}

func requestID(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey{}).(string)
	return id
}
