package api

import (
	"context"
	"crypto/subtle"
	"errors"
	"io"
	"sort"
	"strings"
	"time"

	starfabricv1 "github.com/starfabric/starfabric/gen/starfabric/v1"
	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/predictive"
	"github.com/starfabric/starfabric/internal/topology"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	"google.golang.org/protobuf/types/known/timestamppb"
)

// GRPCService exposes the versioned Protobuf contract over the same App state
// machine as the HTTP API. It intentionally contains no second desired-state
// store or reconciliation implementation.
type GRPCService struct {
	starfabricv1.UnimplementedStarFabricControlServer
	app *app.App
}

func NewGRPCService(application *app.App) *GRPCService {
	return &GRPCService{app: application}
}

// NewGRPCServer creates a production gRPC server with the generated v1
// service registered. Callers may supply transport credentials as options.
func NewGRPCServer(application *app.App, token string, logger *observability.Logger, options ...grpc.ServerOption) *grpc.Server {
	if logger == nil {
		logger = observability.NewLogger(io.Discard)
	}
	options = append(options, grpc.ChainUnaryInterceptor(grpcAuthInterceptor(token), grpcLogInterceptor(logger)))
	server := grpc.NewServer(options...)
	starfabricv1.RegisterStarFabricControlServer(server, NewGRPCService(application))
	return server
}

func grpcAuthInterceptor(token string) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if token != "" {
			values := metadata.ValueFromIncomingContext(ctx, "authorization")
			provided := ""
			if len(values) == 1 {
				provided = strings.TrimPrefix(values[0], "Bearer ")
			}
			if len(provided) != len(token) || subtle.ConstantTimeCompare([]byte(provided), []byte(token)) != 1 {
				return nil, status.Error(codes.Unauthenticated, "valid bearer token required")
			}
		}
		return handler(ctx, request)
	}
}

func grpcLogInterceptor(logger *observability.Logger) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		started := time.Now()
		response, err := handler(ctx, request)
		fields := map[string]any{"method": info.FullMethod, "duration_us": time.Since(started).Microseconds()}
		if err != nil {
			fields["code"] = status.Code(err).String()
			logger.Event("warn", "grpc request", fields)
		} else {
			fields["code"] = codes.OK.String()
			logger.Event("info", "grpc request", fields)
		}
		return response, err
	}
}

func (s *GRPCService) GetTopology(context.Context, *emptypb.Empty) (*starfabricv1.TopologySnapshot, error) {
	return topologyToProto(s.app.Topology()), nil
}

func (s *GRPCService) ApplyTopologyEvent(_ context.Context, request *starfabricv1.TopologyEvent) (*starfabricv1.ApplyEventResponse, error) {
	event, err := eventFromProto(request)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	snapshot, err := s.app.ApplyEvent(event)
	if err != nil {
		return nil, appStatus(err)
	}
	return &starfabricv1.ApplyEventResponse{Topology: topologyToProto(snapshot)}, nil
}

func (s *GRPCService) GetIntents(context.Context, *emptypb.Empty) (*starfabricv1.IntentList, error) {
	return intentsToProto(s.app.Intents()), nil
}

func (s *GRPCService) PutIntents(_ context.Context, request *starfabricv1.IntentList) (*starfabricv1.IntentList, error) {
	intents := intentsFromProto(request.GetIntents())
	if err := s.app.SetIntents(intents); err != nil {
		return nil, appStatus(err)
	}
	return intentsToProto(s.app.Intents()), nil
}

func (s *GRPCService) PreviewPlan(context.Context, *emptypb.Empty) (*starfabricv1.RoutePlan, error) {
	plan, err := s.app.Preview()
	if err != nil {
		return nil, appStatus(err)
	}
	return planToProto(plan), nil
}

func (s *GRPCService) Reconcile(ctx context.Context, _ *emptypb.Empty) (*starfabricv1.ReconcileResponse, error) {
	plan, reconcileStatus, err := s.app.Reconcile(ctx)
	if err != nil {
		return nil, appStatus(err)
	}
	return &starfabricv1.ReconcileResponse{Plan: planToProto(plan), Status: reconcileStatusToProto(reconcileStatus)}, nil
}

func (s *GRPCService) GetStatus(context.Context, *emptypb.Empty) (*starfabricv1.StatusResponse, error) {
	response := &starfabricv1.StatusResponse{Reconcile: reconcileStatusToProto(s.app.Status())}
	if plan := s.app.CommittedPlan(); plan != nil {
		response.CommittedPlan = planToProto(*plan)
	}
	return response, nil
}

func (s *GRPCService) GetDevices(ctx context.Context, _ *emptypb.Empty) (*starfabricv1.DeviceList, error) {
	states := s.app.DeviceStates(ctx)
	devices := make([]*starfabricv1.DeviceState, 0, len(states))
	for _, state := range states {
		devices = append(devices, deviceStateToProto(state))
	}
	return &starfabricv1.DeviceList{Devices: devices}, nil
}

func (s *GRPCService) SetDeviceFault(_ context.Context, request *starfabricv1.DeviceFaultRequest) (*starfabricv1.DeviceFaultResponse, error) {
	if request == nil || strings.TrimSpace(request.GetDevice()) == "" {
		return nil, status.Error(codes.InvalidArgument, "device is required")
	}
	mode := adapter.FaultMode(request.GetMode())
	if err := s.app.SetDeviceFault(request.GetDevice(), mode); err != nil {
		return nil, appStatus(err)
	}
	return &starfabricv1.DeviceFaultResponse{Device: request.GetDevice(), Mode: request.GetMode()}, nil
}

func (s *GRPCService) Forecast(_ context.Context, request *starfabricv1.PredictiveRequest) (*starfabricv1.ForecastResponse, error) {
	windows, horizon, lead, err := predictiveRequestFromProto(request)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	activations, err := s.app.Forecast(windows, horizon, lead)
	if err != nil {
		return nil, appStatus(err)
	}
	return &starfabricv1.ForecastResponse{Activations: activationsToProto(activations)}, nil
}

func (s *GRPCService) CreatePredictiveSchedule(_ context.Context, request *starfabricv1.PredictiveRequest) (*starfabricv1.PredictiveSchedule, error) {
	windows, horizon, lead, err := predictiveRequestFromProto(request)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	schedule, err := s.app.CreatePredictiveSchedule(windows, horizon, lead)
	if err != nil {
		return nil, appStatus(err)
	}
	return scheduleToProto(schedule), nil
}

func (s *GRPCService) GetPredictiveSchedule(_ context.Context, request *starfabricv1.PredictiveScheduleRequest) (*starfabricv1.PredictiveSchedule, error) {
	if request == nil || strings.TrimSpace(request.GetId()) == "" {
		return nil, status.Error(codes.InvalidArgument, "schedule ID is required")
	}
	schedule, exists := s.app.PredictiveSchedule(request.GetId())
	if !exists {
		return nil, status.Error(codes.NotFound, "predictive schedule not found")
	}
	return scheduleToProto(schedule), nil
}

func (s *GRPCService) ListPredictiveSchedules(context.Context, *emptypb.Empty) (*starfabricv1.PredictiveScheduleList, error) {
	schedules := s.app.PredictiveSchedules()
	result := make([]*starfabricv1.PredictiveSchedule, 0, len(schedules))
	for _, schedule := range schedules {
		result = append(result, scheduleToProto(schedule))
	}
	return &starfabricv1.PredictiveScheduleList{Schedules: result}, nil
}

func appStatus(err error) error {
	switch {
	case errors.Is(err, app.ErrNotLeader):
		return status.Error(codes.Unavailable, err.Error())
	case errors.Is(err, topology.ErrDuplicateEvent):
		return status.Error(codes.AlreadyExists, err.Error())
	case errors.Is(err, topology.ErrStaleEvent):
		return status.Error(codes.FailedPrecondition, err.Error())
	default:
		return status.Error(codes.InvalidArgument, err.Error())
	}
}

func timestamp(value time.Time) *timestamppb.Timestamp {
	if value.IsZero() {
		return nil
	}
	return timestamppb.New(value)
}

func timeFromTimestamp(value *timestamppb.Timestamp, field string) (time.Time, error) {
	if value == nil {
		return time.Time{}, nil
	}
	if err := value.CheckValid(); err != nil {
		return time.Time{}, status.Errorf(codes.InvalidArgument, "%s: %v", field, err)
	}
	return value.AsTime(), nil
}

func nodeToProto(value model.Node) *starfabricv1.Node {
	return &starfabricv1.Node{Id: value.ID, Kind: value.Kind, Loopback: value.Loopback, Labels: value.Labels, Enabled: value.Enabled}
}

func nodeFromProto(value *starfabricv1.Node) *model.Node {
	if value == nil {
		return nil
	}
	return &model.Node{ID: value.GetId(), Kind: value.GetKind(), Loopback: value.GetLoopback(), Labels: value.GetLabels(), Enabled: value.GetEnabled()}
}

func linkToProto(value model.Link) *starfabricv1.Link {
	return &starfabricv1.Link{
		Id: value.ID, Source: value.Source, Target: value.Target, AdminUp: value.AdminUp, OperationalUp: value.OperationalUp,
		LatencyUs: value.LatencyUS, CapacityBps: value.CapacityBPS, LossPpm: value.LossPPM, ReliabilityPpm: value.ReliabilityPPM,
		TelemetryAt: timestamp(value.TelemetryAt), ValidUntil: timestamp(value.ValidUntil), AvailabilityEnd: timestamp(value.AvailabilityEnd),
		RiskGroups: value.RiskGroups, LinkType: value.LinkType, AcquisitionState: value.AcquisitionState, RangeKm: value.RangeKM, DopplerHz: value.DopplerHz,
	}
}

func linkFromProto(value *starfabricv1.Link) (*model.Link, error) {
	if value == nil {
		return nil, nil
	}
	telemetryAt, err := timeFromTimestamp(value.GetTelemetryAt(), "link.telemetry_at")
	if err != nil {
		return nil, err
	}
	validUntil, err := timeFromTimestamp(value.GetValidUntil(), "link.valid_until")
	if err != nil {
		return nil, err
	}
	availabilityEnd, err := timeFromTimestamp(value.GetAvailabilityEnd(), "link.availability_end")
	if err != nil {
		return nil, err
	}
	return &model.Link{
		ID: value.GetId(), Source: value.GetSource(), Target: value.GetTarget(), AdminUp: value.GetAdminUp(), OperationalUp: value.GetOperationalUp(),
		LatencyUS: value.GetLatencyUs(), CapacityBPS: value.GetCapacityBps(), LossPPM: value.GetLossPpm(), ReliabilityPPM: value.GetReliabilityPpm(),
		TelemetryAt: telemetryAt, ValidUntil: validUntil, AvailabilityEnd: availabilityEnd,
		RiskGroups: append([]string(nil), value.GetRiskGroups()...), LinkType: value.GetLinkType(), AcquisitionState: value.GetAcquisitionState(), RangeKM: value.GetRangeKm(), DopplerHz: value.GetDopplerHz(),
	}, nil
}

func topologyToProto(value model.TopologySnapshot) *starfabricv1.TopologySnapshot {
	nodes := make([]*starfabricv1.Node, 0, len(value.Nodes))
	for _, node := range value.Nodes {
		nodes = append(nodes, nodeToProto(node))
	}
	links := make([]*starfabricv1.Link, 0, len(value.Links))
	for _, link := range value.Links {
		links = append(links, linkToProto(link))
	}
	return &starfabricv1.TopologySnapshot{
		Version: value.Version, GeneratedAt: timestamp(value.GeneratedAt), ValidFrom: timestamp(value.ValidFrom),
		ValidUntil: timestamp(value.ValidUntil), Nodes: nodes, Links: links,
	}
}

func eventFromProto(value *starfabricv1.TopologyEvent) (model.TopologyEvent, error) {
	if value == nil {
		return model.TopologyEvent{}, errors.New("topology event is required")
	}
	observedAt, err := timeFromTimestamp(value.GetObservedAt(), "event.observed_at")
	if err != nil {
		return model.TopologyEvent{}, err
	}
	effectiveAt, err := timeFromTimestamp(value.GetEffectiveAt(), "event.effective_at")
	if err != nil {
		return model.TopologyEvent{}, err
	}
	types := map[starfabricv1.EventType]model.EventType{
		starfabricv1.EventType_EVENT_TYPE_LINK_UP: model.EventLinkUp, starfabricv1.EventType_EVENT_TYPE_LINK_DOWN: model.EventLinkDown,
		starfabricv1.EventType_EVENT_TYPE_LINK_UPDATE: model.EventLinkUpdate, starfabricv1.EventType_EVENT_TYPE_NODE_UP: model.EventNodeUp,
		starfabricv1.EventType_EVENT_TYPE_NODE_DOWN: model.EventNodeDown,
	}
	eventType, exists := types[value.GetType()]
	if !exists {
		return model.TopologyEvent{}, errors.New("supported topology event type is required")
	}
	link, err := linkFromProto(value.GetLink())
	if err != nil {
		return model.TopologyEvent{}, err
	}
	return model.TopologyEvent{
		EventID: value.GetEventId(), Subject: value.GetSubject(), Sequence: value.GetSequence(), Type: eventType,
		ObservedAt: observedAt, EffectiveAt: effectiveAt, Link: link, Node: nodeFromProto(value.GetNode()),
	}, nil
}

func intentToProto(value model.RouteIntent) *starfabricv1.RouteIntent {
	return &starfabricv1.RouteIntent{
		Id: value.ID, Source: value.Source, Destination: value.Destination, DestinationPrefix: value.DestinationPrefix,
		Policy: value.Policy, MaxLatencyUs: value.MaxLatencyUS, MinCapacityBps: value.MinCapacityBPS,
		MaxLossPpm: value.MaxLossPPM, RequiredReliabilityPpm: value.RequiredReliability, Redundancy: uint32(value.Redundancy),
		TrafficClass: value.Class, Priority: int32(value.Priority), Labels: value.Labels, DemandBps: value.DemandBPS,
		GatewayCandidates: append([]string(nil), value.GatewayCandidates...),
	}
}

func intentFromProto(value *starfabricv1.RouteIntent) model.RouteIntent {
	return model.RouteIntent{
		ID: value.GetId(), Source: value.GetSource(), Destination: value.GetDestination(), DestinationPrefix: value.GetDestinationPrefix(),
		Policy: value.GetPolicy(), MaxLatencyUS: value.GetMaxLatencyUs(), MinCapacityBPS: value.GetMinCapacityBps(),
		MaxLossPPM: value.GetMaxLossPpm(), RequiredReliability: value.GetRequiredReliabilityPpm(), Redundancy: int(value.GetRedundancy()),
		Class: value.GetTrafficClass(), Priority: int(value.GetPriority()), Labels: value.GetLabels(), DemandBPS: value.GetDemandBps(),
		GatewayCandidates: append([]string(nil), value.GetGatewayCandidates()...),
	}
}

func intentsToProto(values []model.RouteIntent) *starfabricv1.IntentList {
	result := make([]*starfabricv1.RouteIntent, 0, len(values))
	for _, value := range values {
		result = append(result, intentToProto(value))
	}
	return &starfabricv1.IntentList{Intents: result}
}

func intentsFromProto(values []*starfabricv1.RouteIntent) []model.RouteIntent {
	result := make([]model.RouteIntent, 0, len(values))
	for _, value := range values {
		result = append(result, intentFromProto(value))
	}
	return result
}

func pathToProto(value model.Path) *starfabricv1.Path {
	return &starfabricv1.Path{
		Nodes: value.Nodes, Links: value.Links, LatencyUs: value.LatencyUS, CapacityBps: value.CapacityBPS,
		LossPpm: value.LossPPM, Cost: value.Cost, RiskGroups: value.RiskGroups,
	}
}

func routeToProto(value model.RouteOperation) *starfabricv1.RouteOperation {
	return &starfabricv1.RouteOperation{
		Device: value.Device, Prefix: value.Prefix, NextHop: value.NextHop, NextHopNode: value.NextHopNode,
		Metric: int32(value.Metric), IntentId: value.IntentID, PathRole: value.PathRole,
	}
}

func planToProto(value model.RoutePlan) *starfabricv1.RoutePlan {
	intents := intentsToProto(value.Intents).GetIntents()
	pathIDs := make([]string, 0, len(value.Paths))
	for id := range value.Paths {
		pathIDs = append(pathIDs, id)
	}
	sort.Strings(pathIDs)
	paths := make([]*starfabricv1.IntentPaths, 0, len(pathIDs))
	for _, id := range pathIDs {
		converted := make([]*starfabricv1.Path, 0, len(value.Paths[id]))
		for _, path := range value.Paths[id] {
			converted = append(converted, pathToProto(path))
		}
		paths = append(paths, &starfabricv1.IntentPaths{IntentId: id, Paths: converted})
	}
	routes := make([]*starfabricv1.RouteOperation, 0, len(value.Routes))
	for _, route := range value.Routes {
		routes = append(routes, routeToProto(route))
	}
	return &starfabricv1.RoutePlan{
		Id: value.ID, TopologyVersion: value.TopologyVersion, CreatedAt: timestamp(value.CreatedAt), ExpiresAt: timestamp(value.ExpiresAt),
		Policy: value.Policy, Intents: intents, Paths: paths, Routes: routes, Preconditions: value.Preconditions, RollbackPlanId: value.RollbackPlanID,
	}
}

func reconcileStatusToProto(value model.ReconcileStatus) *starfabricv1.ReconcileStatus {
	return &starfabricv1.ReconcileStatus{
		Phase: string(value.Phase), PlanId: value.PlanID, TopologyVersion: value.TopologyVersion,
		StartedAt: timestamp(value.StartedAt), FinishedAt: timestamp(value.FinishedAt), PreparedDevices: value.PreparedDevices,
		CommittedDevices: value.CommittedDevices, FailedDevices: value.FailedDevices, Error: value.Error,
		RollbackReason: value.RollbackReason, DeferredDevices: value.DeferredDevices,
	}
}

func deviceStateToProto(value model.DeviceState) *starfabricv1.DeviceState {
	routes := make([]*starfabricv1.RouteOperation, 0, len(value.Routes))
	for _, route := range value.Routes {
		routes = append(routes, routeToProto(route))
	}
	return &starfabricv1.DeviceState{
		Device: value.Device, Healthy: value.Healthy, AppliedPlanId: value.AppliedPlanID,
		Routes: routes, ObservedAt: timestamp(value.ObservedAt), Message: value.Message,
	}
}

func predictiveRequestFromProto(value *starfabricv1.PredictiveRequest) ([]predictive.ContactWindow, time.Duration, time.Duration, error) {
	if value == nil || value.GetHorizonSeconds() <= 0 || value.GetHorizonSeconds() > int64((24*time.Hour)/time.Second) {
		return nil, 0, 0, errors.New("horizon_seconds must be in 1..86400")
	}
	if value.GetLeadSeconds() < 0 || value.GetLeadSeconds() > int64((24*time.Hour)/time.Second) {
		return nil, 0, 0, errors.New("lead_seconds must be in 0..86400")
	}
	windows := make([]predictive.ContactWindow, 0, len(value.GetWindows()))
	for index, input := range value.GetWindows() {
		link, err := linkFromProto(input.GetLink())
		if err != nil {
			return nil, 0, 0, err
		}
		if link == nil {
			return nil, 0, 0, errors.New("contact window link is required")
		}
		start, err := timeFromTimestamp(input.GetStart(), "contact.start")
		if err != nil {
			return nil, 0, 0, err
		}
		end, err := timeFromTimestamp(input.GetEnd(), "contact.end")
		if err != nil {
			return nil, 0, 0, err
		}
		window := predictive.ContactWindow{Link: *link, Start: start, End: end}
		if err := window.Validate(); err != nil {
			return nil, 0, 0, status.Errorf(codes.InvalidArgument, "contact window %d: %v", index, err)
		}
		windows = append(windows, window)
	}
	return windows, time.Duration(value.GetHorizonSeconds()) * time.Second, time.Duration(value.GetLeadSeconds()) * time.Second, nil
}

func activationToProto(value predictive.Activation) *starfabricv1.Activation {
	return &starfabricv1.Activation{
		TopologyAt: timestamp(value.TopologyAt), ActivateAt: timestamp(value.ActivateAt), Topology: topologyToProto(value.Topology),
		Plan: planToProto(value.Plan), ShadowValidated: value.ShadowValidated, ChangedPrefixes: value.ChangedPrefixes,
	}
}

func activationsToProto(values []predictive.Activation) []*starfabricv1.Activation {
	result := make([]*starfabricv1.Activation, 0, len(values))
	for _, value := range values {
		result = append(result, activationToProto(value))
	}
	return result
}

func scheduleToProto(value app.PredictiveSchedule) *starfabricv1.PredictiveSchedule {
	return &starfabricv1.PredictiveSchedule{
		Id: value.ID, State: value.State, BaseTopologyVersion: value.BaseTopologyVersion,
		ExpectedTopologyVersion: value.ExpectedTopologyVersion, CreatedAt: timestamp(value.CreatedAt), UpdatedAt: timestamp(value.UpdatedAt),
		NextActivation: uint32(value.NextActivation), Activations: activationsToProto(value.Activations), Error: value.Error,
	}
}
