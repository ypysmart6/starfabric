package api_test

import (
	"context"
	"io"
	"net"
	"testing"
	"time"

	starfabricv1 "github.com/starfabric/starfabric/gen/starfabric/v1"
	"github.com/starfabric/starfabric/internal/api"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/testutil"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/types/known/emptypb"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func TestGeneratedGRPCClientExecutesEveryV1RPC(t *testing.T) {
	application, err := app.New(app.Config{}, testutil.Diamond(), []model.RouteIntent{testutil.Intent()}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = application.Close() })

	listener := bufconn.Listen(1 << 20)
	server := api.NewGRPCServer(application, "secret", observability.NewLogger(io.Discard))
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(server.Stop)
	connection, err := grpc.NewClient("passthrough:///starfabric",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }),
	)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = connection.Close() })
	client := starfabricv1.NewStarFabricControlClient(connection)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := client.GetTopology(ctx, &emptypb.Empty{}); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("GetTopology without token code=%s error=%v", status.Code(err), err)
	}
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer secret")
	topology, err := client.GetTopology(ctx, &emptypb.Empty{})
	if err != nil || topology.GetVersion() == 0 || len(topology.GetNodes()) == 0 || len(topology.GetLinks()) == 0 {
		t.Fatalf("GetTopology response=%v error=%v", topology, err)
	}
	intents, err := client.GetIntents(ctx, &emptypb.Empty{})
	if err != nil || len(intents.GetIntents()) != 1 {
		t.Fatalf("GetIntents response=%v error=%v", intents, err)
	}
	if _, err := client.PutIntents(ctx, intents); err != nil {
		t.Fatalf("PutIntents: %v", err)
	}
	preview, err := client.PreviewPlan(ctx, &emptypb.Empty{})
	if err != nil || preview.GetId() == "" || len(preview.GetRoutes()) == 0 {
		t.Fatalf("PreviewPlan response=%v error=%v", preview, err)
	}
	reconciled, err := client.Reconcile(ctx, &emptypb.Empty{})
	if err != nil || reconciled.GetStatus().GetPhase() != "committed" {
		t.Fatalf("Reconcile response=%v error=%v", reconciled, err)
	}
	currentStatus, err := client.GetStatus(ctx, &emptypb.Empty{})
	if err != nil || currentStatus.GetCommittedPlan().GetId() != reconciled.GetPlan().GetId() {
		t.Fatalf("GetStatus response=%v error=%v", currentStatus, err)
	}
	devices, err := client.GetDevices(ctx, &emptypb.Empty{})
	if err != nil || len(devices.GetDevices()) != len(topology.GetNodes()) {
		t.Fatalf("GetDevices response=%v error=%v", devices, err)
	}
	device := topology.GetNodes()[0].GetId()
	if _, err := client.SetDeviceFault(ctx, &starfabricv1.DeviceFaultRequest{Device: device, Mode: ""}); err != nil {
		t.Fatalf("SetDeviceFault: %v", err)
	}
	predictiveRequest := &starfabricv1.PredictiveRequest{HorizonSeconds: 60, LeadSeconds: 2}
	forecast, err := client.Forecast(ctx, predictiveRequest)
	if err != nil || len(forecast.GetActivations()) == 0 || !forecast.GetActivations()[0].GetShadowValidated() {
		t.Fatalf("Forecast response=%v error=%v", forecast, err)
	}
	schedule, err := client.CreatePredictiveSchedule(ctx, predictiveRequest)
	if err != nil || schedule.GetId() == "" || schedule.GetState() != "completed" {
		t.Fatalf("CreatePredictiveSchedule response=%v error=%v", schedule, err)
	}
	loadedSchedule, err := client.GetPredictiveSchedule(ctx, &starfabricv1.PredictiveScheduleRequest{Id: schedule.GetId()})
	if err != nil || loadedSchedule.GetId() != schedule.GetId() {
		t.Fatalf("GetPredictiveSchedule response=%v error=%v", loadedSchedule, err)
	}
	schedules, err := client.ListPredictiveSchedules(ctx, &emptypb.Empty{})
	if err != nil || len(schedules.GetSchedules()) != 1 {
		t.Fatalf("ListPredictiveSchedules response=%v error=%v", schedules, err)
	}

	now := time.Now().UTC()
	event := &starfabricv1.TopologyEvent{
		EventId: "grpc-contract-event", Subject: topology.GetLinks()[0].GetId(), Sequence: 1,
		Type: starfabricv1.EventType_EVENT_TYPE_LINK_DOWN, ObservedAt: timestamppb.New(now), EffectiveAt: timestamppb.New(now),
		Link: topology.GetLinks()[0],
	}
	updated, err := client.ApplyTopologyEvent(ctx, event)
	if err != nil || updated.GetTopology().GetVersion() != topology.GetVersion()+1 {
		t.Fatalf("ApplyTopologyEvent response=%v error=%v", updated, err)
	}
}
