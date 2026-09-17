// sf-openconfig-emulator is a single-process standards target for integration
// tests. It exposes gNMI, gNOI system/diagnostic/certificate/packet-capture/OS,
// and a complete in-memory gRIBI RIB/FIB-ACK service on one gRPC socket.
package main

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"

	gpb "github.com/openconfig/gnmi/proto/gnmi"
	certpb "github.com/openconfig/gnoi/cert"
	diagpb "github.com/openconfig/gnoi/diag"
	ospb "github.com/openconfig/gnoi/os"
	pcappb "github.com/openconfig/gnoi/packet_capture"
	spb "github.com/openconfig/gnoi/system"
	typespb "github.com/openconfig/gnoi/types"
	gribipb "github.com/openconfig/gribi/v1/proto/service"
	gribiserver "github.com/openconfig/gribigo/server"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type managementServer struct {
	gpb.UnimplementedGNMIServer
	spb.UnimplementedSystemServer
	node         string
	mu           sync.RWMutex
	datastore    map[string]*gpb.TypedValue
	sequences    map[string]uint64
	rebootAt     time.Time
	rebootReason string
	rebootMethod spb.RebootMethod
	rebootCount  uint32
	rebootStatus *spb.RebootStatus
}

func (s *managementServer) Capabilities(context.Context, *gpb.CapabilityRequest) (*gpb.CapabilityResponse, error) {
	return &gpb.CapabilityResponse{
		GNMIVersion:        "0.10.0",
		SupportedEncodings: []gpb.Encoding{gpb.Encoding_JSON_IETF, gpb.Encoding_PROTO},
		SupportedModels: []*gpb.ModelData{{
			Name: "openconfig-aft", Organization: "OpenConfig", Version: "integration-emulator",
		}},
	}, nil
}

func (s *managementServer) Time(context.Context, *spb.TimeRequest) (*spb.TimeResponse, error) {
	return &spb.TimeResponse{Time: uint64(time.Now().UnixNano())}, nil
}

func (s *managementServer) Get(_ context.Context, request *gpb.GetRequest) (*gpb.GetResponse, error) {
	if len(request.GetPath()) == 0 {
		return nil, status.Error(codes.InvalidArgument, "at least one path is required")
	}
	updates := make([]*gpb.Update, 0, len(request.GetPath()))
	for _, path := range request.GetPath() {
		updates = append(updates, &gpb.Update{Path: path, Val: s.value(path)})
	}
	return &gpb.GetResponse{Notification: []*gpb.Notification{{
		Timestamp: time.Now().UnixNano(), Update: updates,
	}}}, nil
}

func (s *managementServer) Set(_ context.Context, request *gpb.SetRequest) (*gpb.SetResponse, error) {
	now := time.Now().UnixNano()
	results := make([]*gpb.UpdateResult, 0, len(request.GetDelete())+len(request.GetReplace())+len(request.GetUpdate()))
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, path := range request.GetDelete() {
		delete(s.datastore, pathKey(path))
		results = append(results, &gpb.UpdateResult{Timestamp: now, Path: path, Op: gpb.UpdateResult_DELETE})
	}
	apply := func(updates []*gpb.Update, operation gpb.UpdateResult_Operation) error {
		for _, update := range updates {
			if update.GetPath() == nil || len(update.GetVal().GetJsonIetfVal()) == 0 || !json.Valid(update.GetVal().GetJsonIetfVal()) {
				return status.Error(codes.InvalidArgument, "Set requires a path and valid JSON_IETF value")
			}
			s.datastore[pathKey(update.GetPath())] = cloneValue(update.GetVal())
			results = append(results, &gpb.UpdateResult{Timestamp: now, Path: update.GetPath(), Op: operation})
		}
		return nil
	}
	if err := apply(request.GetReplace(), gpb.UpdateResult_REPLACE); err != nil {
		return nil, err
	}
	if err := apply(request.GetUpdate(), gpb.UpdateResult_UPDATE); err != nil {
		return nil, err
	}
	return &gpb.SetResponse{Timestamp: now, Response: results}, nil
}

func (s *managementServer) Subscribe(stream gpb.GNMI_SubscribeServer) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	list := first.GetSubscribe()
	if list == nil || list.GetMode() != gpb.SubscriptionList_STREAM || len(list.GetSubscription()) == 0 {
		return status.Error(codes.InvalidArgument, "a STREAM subscription is required")
	}
	interval := time.Second
	for _, subscription := range list.GetSubscription() {
		candidate := time.Duration(subscription.GetSampleInterval())
		if candidate > 0 && candidate < interval {
			interval = candidate
		}
	}
	if interval < 10*time.Millisecond {
		interval = 10 * time.Millisecond
	}
	if err := stream.Send(&gpb.SubscribeResponse{Response: &gpb.SubscribeResponse_SyncResponse{SyncResponse: true}}); err != nil {
		return err
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-stream.Context().Done():
			return stream.Context().Err()
		case at := <-ticker.C:
			updates := make([]*gpb.Update, 0, len(list.GetSubscription()))
			for _, subscription := range list.GetSubscription() {
				updates = append(updates, &gpb.Update{Path: subscription.GetPath(), Val: s.sample(subscription.GetPath())})
			}
			response := &gpb.SubscribeResponse{Response: &gpb.SubscribeResponse_Update{Update: &gpb.Notification{
				Timestamp: at.UnixNano(), Update: updates,
			}}}
			if err := stream.Send(response); err != nil {
				return err
			}
		}
	}
}

func (s *managementServer) Ping(request *spb.PingRequest, stream spb.System_PingServer) error {
	if strings.TrimSpace(request.GetDestination()) == "" {
		return status.Error(codes.InvalidArgument, "ping destination is required")
	}
	count := request.GetCount()
	if count <= 0 {
		count = 3
	}
	source := request.GetSource()
	if source == "" {
		source = s.node
	}
	for sequence := int32(1); sequence <= count; sequence++ {
		if err := stream.Send(&spb.PingResponse{
			Source: source, Time: 1000, Sent: sequence, Received: sequence,
			MinTime: 1000, AvgTime: 1000, MaxTime: 1000, Bytes: 64, Sequence: sequence, Ttl: 64,
		}); err != nil {
			return err
		}
	}
	return nil
}

func (s *managementServer) Reboot(_ context.Context, request *spb.RebootRequest) (*spb.RebootResponse, error) {
	if request.GetMethod() != spb.RebootMethod_COLD {
		return nil, status.Error(codes.InvalidArgument, "the emulator supports COLD reboot only")
	}
	if request.GetDelay() == 0 || strings.TrimSpace(request.GetMessage()) == "" {
		return nil, status.Error(codes.InvalidArgument, "a positive delay and audit reason are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.rebootAt.IsZero() {
		return nil, status.Error(codes.FailedPrecondition, "a reboot is already scheduled")
	}
	s.rebootAt = time.Now().Add(time.Duration(request.GetDelay()))
	s.rebootReason = request.GetMessage()
	s.rebootMethod = request.GetMethod()
	s.rebootStatus = nil
	return &spb.RebootResponse{}, nil
}

func (s *managementServer) RebootStatus(_ context.Context, _ *spb.RebootStatusRequest) (*spb.RebootStatusResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.rebootAt.IsZero() && !time.Now().Before(s.rebootAt) {
		s.rebootAt = time.Time{}
		s.rebootCount++
		s.rebootStatus = &spb.RebootStatus{Status: spb.RebootStatus_STATUS_SUCCESS, Message: "emulated reboot completed"}
	}
	response := &spb.RebootStatusResponse{Count: s.rebootCount, Reason: s.rebootReason, Method: s.rebootMethod, Status: s.rebootStatus}
	if !s.rebootAt.IsZero() {
		response.Active = true
		response.When = uint64(s.rebootAt.UnixNano())
		response.Wait = uint64(max(time.Until(s.rebootAt), 0))
	}
	return response, nil
}

func (s *managementServer) CancelReboot(_ context.Context, request *spb.CancelRebootRequest) (*spb.CancelRebootResponse, error) {
	if strings.TrimSpace(request.GetMessage()) == "" {
		return nil, status.Error(codes.InvalidArgument, "a cancellation audit reason is required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.rebootAt.IsZero() {
		return nil, status.Error(codes.FailedPrecondition, "no reboot is scheduled")
	}
	s.rebootAt = time.Time{}
	s.rebootStatus = &spb.RebootStatus{Status: spb.RebootStatus_STATUS_SUCCESS, Message: "scheduled reboot cancelled: " + request.GetMessage()}
	return &spb.CancelRebootResponse{}, nil
}

func (s *managementServer) value(path *gpb.Path) *gpb.TypedValue {
	key := pathKey(path)
	s.mu.RLock()
	value, exists := s.datastore[key]
	s.mu.RUnlock()
	if exists {
		return cloneValue(value)
	}
	data, _ := json.Marshal(map[string]any{"node": s.node, "path": key})
	return jsonValue(data)
}

func (s *managementServer) sample(path *gpb.Path) *gpb.TypedValue {
	key := pathKey(path)
	s.mu.Lock()
	defer s.mu.Unlock()
	if value, exists := s.datastore[key]; exists {
		return cloneValue(value)
	}
	s.sequences[key]++
	data, _ := json.Marshal(map[string]any{"node": s.node, "sequence": s.sequences[key]})
	return jsonValue(data)
}

func jsonValue(data []byte) *gpb.TypedValue {
	return &gpb.TypedValue{Value: &gpb.TypedValue_JsonIetfVal{JsonIetfVal: append([]byte(nil), data...)}}
}

func cloneValue(value *gpb.TypedValue) *gpb.TypedValue {
	return jsonValue(value.GetJsonIetfVal())
}

func pathKey(path *gpb.Path) string {
	var builder strings.Builder
	for _, element := range path.GetElem() {
		builder.WriteByte('/')
		builder.WriteString(element.GetName())
		keys := make([]string, 0, len(element.GetKey()))
		for key := range element.GetKey() {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			fmt.Fprintf(&builder, "[%s=%s]", key, element.GetKey()[key])
		}
	}
	if builder.Len() == 0 {
		return "/"
	}
	return builder.String()
}

type diagnosticServer struct {
	diagpb.UnimplementedDiagServer
	mu        sync.Mutex
	operation string
	port      *typespb.Path
	startedAt uint64
	active    bool
}

func gnoiInterfaceName(path *typespb.Path) string {
	for _, element := range path.GetElem() {
		if element.GetName() == "interface" && element.GetKey()["name"] != "" {
			return element.GetKey()["name"]
		}
	}
	return ""
}

func (s *diagnosticServer) StartBERT(_ context.Context, request *diagpb.StartBERTRequest) (*diagpb.StartBERTResponse, error) {
	if strings.TrimSpace(request.GetBertOperationId()) == "" || len(request.GetPerPortRequests()) != 1 {
		return nil, status.Error(codes.InvalidArgument, "BERT requires an operation ID and exactly one port")
	}
	port := request.GetPerPortRequests()[0]
	if gnoiInterfaceName(port.GetInterface()) == "" || port.GetPrbsPolynomial() != diagpb.PrbsPolynomial_PRBS_POLYNOMIAL_PRBS31 || port.GetTestDurationInSecs() == 0 {
		return nil, status.Error(codes.InvalidArgument, "BERT requires a valid interface, PRBS31 and positive duration")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active {
		return nil, status.Error(codes.FailedPrecondition, "a BERT operation is already active")
	}
	s.operation = request.GetBertOperationId()
	s.port = port.GetInterface()
	s.startedAt = uint64(time.Now().UnixNano())
	s.active = true
	return &diagpb.StartBERTResponse{
		BertOperationId: s.operation,
		PerPortResponses: []*diagpb.StartBERTResponse_PerPortResponse{{
			Interface: s.port, Status: diagpb.BertStatus_BERT_STATUS_OK,
		}},
	}, nil
}

func (s *diagnosticServer) GetBERTResult(_ context.Context, request *diagpb.GetBERTResultRequest) (*diagpb.GetBERTResultResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.active || request.GetBertOperationId() != s.operation {
		return nil, status.Error(codes.NotFound, "BERT operation is not active")
	}
	if len(request.GetPerPortRequests()) != 1 || gnoiInterfaceName(request.GetPerPortRequests()[0].GetInterface()) != gnoiInterfaceName(s.port) {
		return nil, status.Error(codes.InvalidArgument, "BERT result request does not match the active port")
	}
	return &diagpb.GetBERTResultResponse{PerPortResponses: []*diagpb.GetBERTResultResponse_PerPortResponse{{
		Interface: s.port, Status: diagpb.BertStatus_BERT_STATUS_OK, BertOperationId: s.operation,
		PrbsPolynomial:         diagpb.PrbsPolynomial_PRBS_POLYNOMIAL_PRBS31,
		LastBertStartTimestamp: s.startedAt, LastBertGetResultTimestamp: uint64(time.Now().UnixNano()),
		PeerLockEstablished: true, PeerLockLost: false, ErrorCountPerMinute: []uint32{0}, TotalErrors: 0,
	}}}, nil
}

func (s *diagnosticServer) StopBERT(_ context.Context, request *diagpb.StopBERTRequest) (*diagpb.StopBERTResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.active || request.GetBertOperationId() != s.operation {
		return nil, status.Error(codes.NotFound, "BERT operation is not active")
	}
	if len(request.GetPerPortRequests()) != 1 || gnoiInterfaceName(request.GetPerPortRequests()[0].GetInterface()) != gnoiInterfaceName(s.port) {
		return nil, status.Error(codes.InvalidArgument, "BERT stop request does not match the active port")
	}
	s.active = false
	return &diagpb.StopBERTResponse{
		BertOperationId: s.operation,
		PerPortResponses: []*diagpb.StopBERTResponse_PerPortResponse{{
			Interface: s.port, Status: diagpb.BertStatus_BERT_STATUS_OK,
		}},
	}, nil
}

type certificateServer struct {
	certpb.UnimplementedCertificateManagementServer
	mu           sync.RWMutex
	certificates map[string]*certpb.CertificateInfo
}

func (s *certificateServer) CanGenerateCSR(_ context.Context, request *certpb.CanGenerateCSRRequest) (*certpb.CanGenerateCSRResponse, error) {
	supported := request.GetCertificateType() == certpb.CertificateType_CT_X509 &&
		request.GetKeyType() == certpb.KeyType_KT_RSA && request.GetKeySize() >= 2048
	return &certpb.CanGenerateCSRResponse{CanGenerate: supported}, nil
}

func (s *certificateServer) LoadCertificate(_ context.Context, request *certpb.LoadCertificateRequest) (*certpb.LoadCertificateResponse, error) {
	if strings.TrimSpace(request.GetCertificateId()) == "" || request.GetCertificate().GetType() != certpb.CertificateType_CT_X509 {
		return nil, status.Error(codes.InvalidArgument, "an ID and X.509 certificate are required")
	}
	if _, err := x509.ParseCertificate(request.GetCertificate().GetCertificate()); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid DER certificate: %v", err)
	}
	certificate := &certpb.Certificate{
		Type:        request.GetCertificate().GetType(),
		Certificate: append([]byte(nil), request.GetCertificate().GetCertificate()...),
	}
	s.mu.Lock()
	s.certificates[request.GetCertificateId()] = &certpb.CertificateInfo{
		CertificateId: request.GetCertificateId(), Certificate: certificate, ModificationTime: time.Now().UnixNano(),
	}
	s.mu.Unlock()
	return &certpb.LoadCertificateResponse{}, nil
}

func (s *certificateServer) GetCertificates(context.Context, *certpb.GetCertificatesRequest) (*certpb.GetCertificatesResponse, error) {
	s.mu.RLock()
	ids := make([]string, 0, len(s.certificates))
	for id := range s.certificates {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	result := make([]*certpb.CertificateInfo, 0, len(ids))
	for _, id := range ids {
		stored := s.certificates[id]
		result = append(result, &certpb.CertificateInfo{
			CertificateId: stored.GetCertificateId(), ModificationTime: stored.GetModificationTime(),
			Certificate: &certpb.Certificate{
				Type: stored.GetCertificate().GetType(), Certificate: append([]byte(nil), stored.GetCertificate().GetCertificate()...),
			},
		})
	}
	s.mu.RUnlock()
	return &certpb.GetCertificatesResponse{CertificateInfo: result}, nil
}

type packetCaptureServer struct {
	pcappb.UnimplementedPacketCaptureServer
}

func ethernetIPv4UDPFrame(sequence uint16) []byte {
	frame := []byte{
		0x02, 0x00, 0x00, 0x00, 0x00, 0x01,
		0x02, 0x00, 0x00, 0x00, 0x00, 0x02,
		0x08, 0x00,
		0x45, 0x00, 0x00, 0x1c, byte(sequence >> 8), byte(sequence), 0x00, 0x00,
		0x40, 0x11, 0x00, 0x00, 192, 0, 2, 1, 198, 51, 100, 1,
		0x04, 0xd2, 0x10, 0xe1, 0x00, 0x08, 0x00, 0x00,
	}
	var sum uint32
	for index := 14; index < 34; index += 2 {
		sum += uint32(frame[index])<<8 | uint32(frame[index+1])
	}
	for sum > 0xffff {
		sum = (sum & 0xffff) + (sum >> 16)
	}
	checksum := ^uint16(sum)
	frame[24], frame[25] = byte(checksum>>8), byte(checksum)
	return frame
}

func (s *packetCaptureServer) Pcap(request *pcappb.PcapRequest, stream pcappb.PacketCapture_PcapServer) error {
	wired := request.GetWiredRequest()
	if wired == nil || strings.TrimSpace(wired.GetIfname()) == "" || wired.GetDirection() != pcappb.Direction_BOTH || wired.GetTcpdumpExpression() != "ip" {
		return status.Error(codes.InvalidArgument, "bounded wired BOTH-direction ip capture is required")
	}
	if request.GetPacketCount() == 0 || request.GetPacketCount() > 16 {
		return status.Error(codes.InvalidArgument, "packet_count must be in 1..16")
	}
	for index := uint32(0); index < request.GetPacketCount(); index++ {
		if err := stream.Send(&pcappb.PcapResponse{Packets: []*pcappb.Packet{{Data: ethernetIPv4UDPFrame(uint16(index + 1))}}}); err != nil {
			return err
		}
	}
	return nil
}

type osServer struct {
	ospb.UnimplementedOSServer
	mu             sync.RWMutex
	packages       map[string][]byte
	runningVersion string
}

func (s *osServer) Install(stream ospb.OS_InstallServer) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	transfer := first.GetTransferRequest()
	if transfer == nil || strings.TrimSpace(transfer.GetVersion()) == "" || transfer.GetPackageSize() == 0 || transfer.GetPackageSize() > 1<<20 {
		return status.Error(codes.InvalidArgument, "version and package_size in 1..1048576 are required")
	}
	if err := stream.Send(&ospb.InstallResponse{Response: &ospb.InstallResponse_TransferReady{TransferReady: &ospb.TransferReady{}}}); err != nil {
		return err
	}
	content := make([]byte, 0, transfer.GetPackageSize())
	for {
		request, recvErr := stream.Recv()
		if recvErr != nil {
			return recvErr
		}
		if chunk := request.GetTransferContent(); chunk != nil {
			if uint64(len(content)+len(chunk)) > transfer.GetPackageSize() || len(content)+len(chunk) > 1<<20 {
				return status.Error(codes.ResourceExhausted, "software package exceeds declared or maximum size")
			}
			content = append(content, chunk...)
			if err := stream.Send(&ospb.InstallResponse{Response: &ospb.InstallResponse_TransferProgress{
				TransferProgress: &ospb.TransferProgress{BytesReceived: uint64(len(content))},
			}}); err != nil {
				return err
			}
			continue
		}
		if request.GetTransferEnd() == nil {
			return status.Error(codes.InvalidArgument, "unexpected OS install message")
		}
		if uint64(len(content)) != transfer.GetPackageSize() {
			return status.Errorf(codes.InvalidArgument, "received %d bytes, want %d", len(content), transfer.GetPackageSize())
		}
		s.mu.Lock()
		s.packages[transfer.GetVersion()] = append([]byte(nil), content...)
		s.mu.Unlock()
		return stream.Send(&ospb.InstallResponse{Response: &ospb.InstallResponse_Validated{Validated: &ospb.Validated{
			Version: transfer.GetVersion(), Description: "package accepted by integration emulator",
		}}})
	}
}

func (s *osServer) Activate(_ context.Context, request *ospb.ActivateRequest) (*ospb.ActivateResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.packages[request.GetVersion()]; !exists {
		return &ospb.ActivateResponse{Response: &ospb.ActivateResponse_ActivateError{ActivateError: &ospb.ActivateError{
			Type: ospb.ActivateError_NON_EXISTENT_VERSION, Detail: "software version has not been installed",
		}}}, nil
	}
	s.runningVersion = request.GetVersion()
	return &ospb.ActivateResponse{Response: &ospb.ActivateResponse_ActivateOk{ActivateOk: &ospb.ActivateOK{}}}, nil
}

func (s *osServer) Verify(context.Context, *ospb.VerifyRequest) (*ospb.VerifyResponse, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return &ospb.VerifyResponse{Version: s.runningVersion}, nil
}

func main() {
	listenAddress := flag.String("listen", "127.0.0.1:9339", "gNMI/gNOI/gRIBI listen address")
	node := flag.String("node", "openconfig-target", "target identifier")
	flag.Parse()
	if err := run(*listenAddress, *node); err != nil {
		fmt.Fprintln(os.Stderr, "sf-openconfig-emulator:", err)
		os.Exit(1)
	}
}

func run(address, node string) error {
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	grpcServer := grpc.NewServer()
	management := &managementServer{node: node, datastore: make(map[string]*gpb.TypedValue), sequences: make(map[string]uint64)}
	gpb.RegisterGNMIServer(grpcServer, management)
	spb.RegisterSystemServer(grpcServer, management)
	diagpb.RegisterDiagServer(grpcServer, &diagnosticServer{})
	certpb.RegisterCertificateManagementServer(grpcServer, &certificateServer{certificates: make(map[string]*certpb.CertificateInfo)})
	pcappb.RegisterPacketCaptureServer(grpcServer, &packetCaptureServer{})
	ospb.RegisterOSServer(grpcServer, &osServer{packages: make(map[string][]byte), runningVersion: "emulator-v1"})
	rib, err := gribiserver.New(gribiserver.WithNoRIBForwardReferences())
	if err != nil {
		return fmt.Errorf("create gRIBI server: %w", err)
	}
	gribipb.RegisterGRIBIServer(grpcServer, rib)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	errCh := make(chan error, 1)
	go func() { errCh <- grpcServer.Serve(listener) }()
	fmt.Printf("openconfig target %s listening on %s\n", node, listener.Addr())
	select {
	case <-ctx.Done():
		grpcServer.GracefulStop()
		return nil
	case serveErr := <-errCh:
		if errors.Is(serveErr, grpc.ErrServerStopped) {
			return nil
		}
		return serveErr
	}
}
