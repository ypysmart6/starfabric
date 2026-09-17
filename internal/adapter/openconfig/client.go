package openconfig

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	gpb "github.com/openconfig/gnmi/proto/gnmi"
	certpb "github.com/openconfig/gnoi/cert"
	diagpb "github.com/openconfig/gnoi/diag"
	ospb "github.com/openconfig/gnoi/os"
	pcappb "github.com/openconfig/gnoi/packet_capture"
	spb "github.com/openconfig/gnoi/system"
	typespb "github.com/openconfig/gnoi/types"
	"google.golang.org/grpc"
)

// Client exposes standards-based OpenConfig configuration, telemetry and
// operational RPCs. Route programming is implemented separately by Programmer.
type Client struct {
	endpoint Endpoint
	conn     *grpc.ClientConn
	gnmi     gpb.GNMIClient
	gnoi     spb.SystemClient
	diag     diagpb.DiagClient
	cert     certpb.CertificateManagementClient
	pcap     pcappb.PacketCaptureClient
	os       ospb.OSClient
}

func NewClient(endpoint Endpoint) (*Client, error) {
	conn, err := dial(endpoint)
	if err != nil {
		return nil, err
	}
	return &Client{
		endpoint: endpoint,
		conn:     conn,
		gnmi:     gpb.NewGNMIClient(conn),
		gnoi:     spb.NewSystemClient(conn),
		diag:     diagpb.NewDiagClient(conn),
		cert:     certpb.NewCertificateManagementClient(conn),
		pcap:     pcappb.NewPacketCaptureClient(conn),
		os:       ospb.NewOSClient(conn),
	}, nil
}

func (c *Client) Close() error { return c.conn.Close() }

func (c *Client) Capabilities(ctx context.Context) (*gpb.CapabilityResponse, error) {
	return c.gnmi.Capabilities(c.endpoint.authenticated(ctx), &gpb.CapabilityRequest{})
}

func (c *Client) GetJSON(ctx context.Context, paths ...string) (*gpb.GetResponse, error) {
	parsed := make([]*gpb.Path, 0, len(paths))
	for _, value := range paths {
		path, err := ParsePath(value)
		if err != nil {
			return nil, err
		}
		parsed = append(parsed, path)
	}
	return c.gnmi.Get(c.endpoint.authenticated(ctx), &gpb.GetRequest{
		Path: parsed, Type: gpb.GetRequest_ALL, Encoding: gpb.Encoding_JSON_IETF,
	})
}

// SetJSONIETF performs an OpenConfig replace or update using RFC 7951 JSON.
func (c *Client) SetJSONIETF(ctx context.Context, path string, value json.RawMessage, replace bool) (*gpb.SetResponse, error) {
	parsed, err := ParsePath(path)
	if err != nil {
		return nil, err
	}
	if !json.Valid(value) {
		return nil, errors.New("gNMI JSON_IETF value is invalid JSON")
	}
	update := &gpb.Update{Path: parsed, Val: &gpb.TypedValue{Value: &gpb.TypedValue_JsonIetfVal{JsonIetfVal: value}}}
	request := &gpb.SetRequest{}
	if replace {
		request.Replace = []*gpb.Update{update}
	} else {
		request.Update = []*gpb.Update{update}
	}
	return c.gnmi.Set(c.endpoint.authenticated(ctx), request)
}

// Subscribe streams OpenConfig updates until ctx is cancelled or the target
// closes the stream. The callback owns no protobuf state after it returns.
func (c *Client) Subscribe(ctx context.Context, paths []string, sampleInterval time.Duration, callback func(*gpb.SubscribeResponse) error) error {
	if len(paths) == 0 {
		return errors.New("at least one gNMI subscription path is required")
	}
	if sampleInterval <= 0 {
		sampleInterval = time.Second
	}
	subscriptions := make([]*gpb.Subscription, 0, len(paths))
	for _, value := range paths {
		path, err := ParsePath(value)
		if err != nil {
			return err
		}
		subscriptions = append(subscriptions, &gpb.Subscription{
			Path: path, Mode: gpb.SubscriptionMode_SAMPLE, SampleInterval: uint64(sampleInterval),
		})
	}
	stream, err := c.gnmi.Subscribe(c.endpoint.authenticated(ctx))
	if err != nil {
		return err
	}
	request := &gpb.SubscribeRequest{Request: &gpb.SubscribeRequest_Subscribe{Subscribe: &gpb.SubscriptionList{
		Mode: gpb.SubscriptionList_STREAM, Encoding: gpb.Encoding_JSON_IETF, Subscription: subscriptions,
	}}}
	if err := stream.Send(request); err != nil {
		return err
	}
	for {
		response, recvErr := stream.Recv()
		if errors.Is(recvErr, io.EOF) {
			return nil
		}
		if recvErr != nil {
			return recvErr
		}
		if callback != nil {
			if callbackErr := callback(response); callbackErr != nil {
				return callbackErr
			}
		}
	}
}

// Ping invokes the standardized gNOI System.Ping operation and returns all
// streamed replies for use by data-plane verification.
func (c *Client) Ping(ctx context.Context, destination, source, networkInstance string, count int32) ([]*spb.PingResponse, error) {
	if destination == "" {
		return nil, errors.New("gNOI ping destination is required")
	}
	if count <= 0 {
		count = 3
	}
	stream, err := c.gnoi.Ping(c.endpoint.authenticated(ctx), &spb.PingRequest{
		Destination: destination, Source: source, NetworkInstance: networkInstance, Count: count,
	})
	if err != nil {
		return nil, err
	}
	var responses []*spb.PingResponse
	for {
		response, recvErr := stream.Recv()
		if errors.Is(recvErr, io.EOF) {
			return responses, nil
		}
		if recvErr != nil {
			return nil, recvErr
		}
		responses = append(responses, response)
	}
}

func (c *Client) Time(ctx context.Context) (uint64, error) {
	response, err := c.gnoi.Time(c.endpoint.authenticated(ctx), &spb.TimeRequest{})
	if err != nil {
		return 0, err
	}
	return response.GetTime(), nil
}

// ScheduleReboot exposes the operator-gated gNOI maintenance primitive. It is
// deliberately not called by reconciliation; callers must provide a positive
// delay so the operation can be inspected and cancelled before activation.
func (c *Client) ScheduleReboot(ctx context.Context, delay time.Duration, reason string) error {
	if delay <= 0 {
		return errors.New("gNOI reboot requires a positive safety delay")
	}
	if strings.TrimSpace(reason) == "" {
		return errors.New("gNOI reboot requires an audit reason")
	}
	_, err := c.gnoi.Reboot(c.endpoint.authenticated(ctx), &spb.RebootRequest{
		Method: spb.RebootMethod_COLD, Delay: uint64(delay), Message: reason,
	})
	return err
}

func (c *Client) RebootStatus(ctx context.Context) (*spb.RebootStatusResponse, error) {
	return c.gnoi.RebootStatus(c.endpoint.authenticated(ctx), &spb.RebootStatusRequest{})
}

func (c *Client) CancelReboot(ctx context.Context, reason string) error {
	if strings.TrimSpace(reason) == "" {
		return errors.New("gNOI reboot cancellation requires an audit reason")
	}
	_, err := c.gnoi.CancelReboot(c.endpoint.authenticated(ctx), &spb.CancelRebootRequest{Message: reason})
	return err
}

// RunBERT exercises the complete gNOI diagnostic lifecycle and returns the
// reported bit-error count. A result without peer lock is rejected even if the
// target reports an OK status.
func (c *Client) RunBERT(ctx context.Context, interfaceName, operationID string) (uint64, error) {
	if strings.TrimSpace(interfaceName) == "" || strings.TrimSpace(operationID) == "" {
		return 0, errors.New("gNOI BERT requires an interface and operation ID")
	}
	path := &typespb.Path{Origin: "openconfig-interfaces", Elem: []*typespb.PathElem{
		{Name: "interfaces"}, {Name: "interface", Key: map[string]string{"name": interfaceName}},
	}}
	start, err := c.diag.StartBERT(c.endpoint.authenticated(ctx), &diagpb.StartBERTRequest{
		BertOperationId: operationID,
		PerPortRequests: []*diagpb.StartBERTRequest_PerPortRequest{{
			Interface: path, PrbsPolynomial: diagpb.PrbsPolynomial_PRBS_POLYNOMIAL_PRBS31, TestDurationInSecs: 1,
		}},
	})
	if err != nil {
		return 0, err
	}
	if start.GetBertOperationId() != operationID || len(start.GetPerPortResponses()) != 1 ||
		start.GetPerPortResponses()[0].GetStatus() != diagpb.BertStatus_BERT_STATUS_OK {
		return 0, fmt.Errorf("gNOI BERT start was not accepted: %v", start)
	}
	result, err := c.diag.GetBERTResult(c.endpoint.authenticated(ctx), &diagpb.GetBERTResultRequest{
		BertOperationId: operationID,
		PerPortRequests: []*diagpb.GetBERTResultRequest_PerPortRequest{{Interface: path}},
	})
	if err != nil {
		return 0, err
	}
	if len(result.GetPerPortResponses()) != 1 {
		return 0, fmt.Errorf("gNOI BERT returned %d port results, want 1", len(result.GetPerPortResponses()))
	}
	portResult := result.GetPerPortResponses()[0]
	if portResult.GetStatus() != diagpb.BertStatus_BERT_STATUS_OK || !portResult.GetPeerLockEstablished() || portResult.GetPeerLockLost() {
		return 0, fmt.Errorf("gNOI BERT result failed validation: %v", portResult)
	}
	stop, err := c.diag.StopBERT(c.endpoint.authenticated(ctx), &diagpb.StopBERTRequest{
		BertOperationId: operationID,
		PerPortRequests: []*diagpb.StopBERTRequest_PerPortRequest{{Interface: path}},
	})
	if err != nil {
		return 0, err
	}
	if len(stop.GetPerPortResponses()) != 1 || stop.GetPerPortResponses()[0].GetStatus() != diagpb.BertStatus_BERT_STATUS_OK {
		return 0, fmt.Errorf("gNOI BERT stop failed: %v", stop)
	}
	return portResult.GetTotalErrors(), nil
}

// LoadAndListCertificate verifies certificate-generation capability, loads an
// X.509 DER certificate, and reads it back through the standardized gNOI API.
func (c *Client) LoadAndListCertificate(ctx context.Context, certificateID string, der []byte) (int, error) {
	if strings.TrimSpace(certificateID) == "" || len(der) == 0 {
		return 0, errors.New("gNOI certificate load requires an ID and DER certificate")
	}
	capability, err := c.cert.CanGenerateCSR(c.endpoint.authenticated(ctx), &certpb.CanGenerateCSRRequest{
		KeyType: certpb.KeyType_KT_RSA, CertificateType: certpb.CertificateType_CT_X509, KeySize: 2048,
	})
	if err != nil {
		return 0, err
	}
	if !capability.GetCanGenerate() {
		return 0, errors.New("gNOI target cannot generate the required X.509 CSR")
	}
	if _, err := c.cert.LoadCertificate(c.endpoint.authenticated(ctx), &certpb.LoadCertificateRequest{
		CertificateId: certificateID,
		Certificate:   &certpb.Certificate{Type: certpb.CertificateType_CT_X509, Certificate: append([]byte(nil), der...)},
	}); err != nil {
		return 0, err
	}
	listed, err := c.cert.GetCertificates(c.endpoint.authenticated(ctx), &certpb.GetCertificatesRequest{})
	if err != nil {
		return 0, err
	}
	found := false
	for _, info := range listed.GetCertificateInfo() {
		if info.GetCertificateId() == certificateID && info.GetCertificate().GetType() == certpb.CertificateType_CT_X509 &&
			bytes.Equal(info.GetCertificate().GetCertificate(), der) {
			found = true
		}
	}
	if !found {
		return 0, fmt.Errorf("gNOI certificate %q was not returned byte-for-byte", certificateID)
	}
	return len(listed.GetCertificateInfo()), nil
}

// CapturePackets streams bounded raw Ethernet frames from gNOI PacketCapture.
func (c *Client) CapturePackets(ctx context.Context, interfaceName string, count uint32) ([][]byte, error) {
	if strings.TrimSpace(interfaceName) == "" || count == 0 || count > 16 {
		return nil, errors.New("gNOI packet capture requires an interface and 1..16 packets")
	}
	stream, err := c.pcap.Pcap(c.endpoint.authenticated(ctx), &pcappb.PcapRequest{
		RequestType: &pcappb.PcapRequest_WiredRequest{WiredRequest: &pcappb.WiredRequest{
			Ifname: interfaceName, Direction: pcappb.Direction_BOTH,
			FilterType: &pcappb.WiredRequest_TcpdumpExpression{TcpdumpExpression: "ip"},
		}},
		PacketCount: count, Duration: uint64(time.Second),
	})
	if err != nil {
		return nil, err
	}
	packets := make([][]byte, 0, count)
	for {
		response, recvErr := stream.Recv()
		if errors.Is(recvErr, io.EOF) {
			break
		}
		if recvErr != nil {
			return nil, recvErr
		}
		for _, packet := range response.GetPackets() {
			packets = append(packets, append([]byte(nil), packet.GetData()...))
		}
	}
	if len(packets) != int(count) {
		return nil, fmt.Errorf("gNOI packet capture returned %d packets, want %d", len(packets), count)
	}
	return packets, nil
}

// InstallActivateVerifyOS transfers a bounded software package and executes
// the gNOI validation, activation and post-activation verification sequence.
func (c *Client) InstallActivateVerifyOS(ctx context.Context, version string, content []byte) (string, error) {
	if strings.TrimSpace(version) == "" || len(content) == 0 || len(content) > 1<<20 {
		return "", errors.New("gNOI OS install requires a version and a package no larger than 1 MiB")
	}
	stream, err := c.os.Install(c.endpoint.authenticated(ctx))
	if err != nil {
		return "", err
	}
	if err := stream.Send(&ospb.InstallRequest{Request: &ospb.InstallRequest_TransferRequest{TransferRequest: &ospb.TransferRequest{
		Version: version, PackageSize: uint64(len(content)),
	}}}); err != nil {
		return "", err
	}
	ready, err := stream.Recv()
	if err != nil || ready.GetTransferReady() == nil {
		return "", fmt.Errorf("gNOI OS target did not become transfer-ready: response=%v error=%w", ready, err)
	}
	if err := stream.Send(&ospb.InstallRequest{Request: &ospb.InstallRequest_TransferContent{TransferContent: content}}); err != nil {
		return "", err
	}
	progress, err := stream.Recv()
	if err != nil || progress.GetTransferProgress() == nil || progress.GetTransferProgress().GetBytesReceived() != uint64(len(content)) {
		return "", fmt.Errorf("gNOI OS transfer progress mismatch: response=%v error=%w", progress, err)
	}
	if err := stream.Send(&ospb.InstallRequest{Request: &ospb.InstallRequest_TransferEnd{TransferEnd: &ospb.TransferEnd{}}}); err != nil {
		return "", err
	}
	validated, err := stream.Recv()
	if err != nil || validated.GetValidated() == nil || validated.GetValidated().GetVersion() != version {
		return "", fmt.Errorf("gNOI OS validation failed: response=%v error=%w", validated, err)
	}
	if err := stream.CloseSend(); err != nil {
		return "", err
	}
	activated, err := c.os.Activate(c.endpoint.authenticated(ctx), &ospb.ActivateRequest{Version: version, NoReboot: true})
	if err != nil || activated.GetActivateOk() == nil {
		return "", fmt.Errorf("gNOI OS activation failed: response=%v error=%w", activated, err)
	}
	verified, err := c.os.Verify(c.endpoint.authenticated(ctx), &ospb.VerifyRequest{})
	if err != nil || verified.GetVersion() != version || verified.GetActivationFailMessage() != "" {
		return "", fmt.Errorf("gNOI OS verify failed: response=%v error=%w", verified, err)
	}
	return verified.GetVersion(), nil
}

// ParsePath supports the common OpenConfig path form
// /interfaces/interface[name=eth0]/state/counters. It rejects ambiguous or
// malformed predicates instead of silently targeting a broader subtree.
func ParsePath(value string) (*gpb.Path, error) {
	value = strings.TrimSpace(value)
	if value == "" || value == "/" {
		return &gpb.Path{}, nil
	}
	parts := strings.Split(strings.Trim(value, "/"), "/")
	path := &gpb.Path{Origin: "openconfig"}
	for _, part := range parts {
		if part == "" {
			return nil, fmt.Errorf("invalid empty gNMI path element in %q", value)
		}
		element := &gpb.PathElem{Key: map[string]string{}}
		open := strings.IndexByte(part, '[')
		if open < 0 {
			element.Name = part
		} else {
			element.Name = part[:open]
			rest := part[open:]
			for rest != "" {
				if rest[0] != '[' {
					return nil, fmt.Errorf("malformed gNMI predicate %q", part)
				}
				close := strings.IndexByte(rest, ']')
				if close < 0 {
					return nil, fmt.Errorf("unterminated gNMI predicate %q", part)
				}
				key, val, found := strings.Cut(rest[1:close], "=")
				if !found || key == "" || val == "" {
					return nil, fmt.Errorf("invalid gNMI predicate %q", rest[:close+1])
				}
				if _, duplicate := element.Key[key]; duplicate {
					return nil, fmt.Errorf("duplicate gNMI key %q", key)
				}
				element.Key[key] = val
				rest = rest[close+1:]
			}
		}
		if element.Name == "" {
			return nil, fmt.Errorf("empty gNMI element name in %q", value)
		}
		path.Elem = append(path.Elem, element)
	}
	return path, nil
}
