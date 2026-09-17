// sf-openconfig-probe executes the management RPC surface used by the
// integration gate. It is intentionally separate from route reconciliation so
// gNMI streaming and gNOI Ping cannot be accidentally claimed from gRIBI alone.
package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math/big"
	"os"
	"sync/atomic"
	"time"

	gpb "github.com/openconfig/gnmi/proto/gnmi"
	spb "github.com/openconfig/gnoi/system"
	openconfigadapter "github.com/starfabric/starfabric/internal/adapter/openconfig"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type report struct {
	Success            bool   `json:"success"`
	Capabilities       bool   `json:"gnmi_capabilities"`
	Set                bool   `json:"gnmi_set_json_ietf"`
	Get                bool   `json:"gnmi_get_json_ietf"`
	SubscribeSamples   int32  `json:"gnmi_subscribe_samples"`
	PingReplies        int    `json:"gnoi_ping_replies"`
	TimeNanoseconds    uint64 `json:"gnoi_time_nanoseconds"`
	RebootScheduled    bool   `json:"gnoi_reboot_scheduled"`
	RebootCancelled    bool   `json:"gnoi_reboot_cancelled"`
	BERTZeroErrors     bool   `json:"gnoi_bert_zero_errors"`
	CertificateCount   int    `json:"gnoi_certificate_count"`
	CapturedPackets    int    `json:"gnoi_captured_packets"`
	OSVersion          string `json:"gnoi_os_version"`
	Target             string `json:"target"`
	OpenConfigAFTModel bool   `json:"openconfig_aft_model"`
}

func main() {
	endpoint := flag.String("endpoint", "127.0.0.1:9339", "gNMI/gNOI endpoint")
	flag.Parse()
	if err := run(*endpoint); err != nil {
		fmt.Fprintln(os.Stderr, "sf-openconfig-probe:", err)
		os.Exit(1)
	}
}

func run(address string) error {
	client, err := openconfigadapter.NewClient(openconfigadapter.Endpoint{Address: address, Insecure: true})
	if err != nil {
		return err
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	capabilities, err := client.Capabilities(ctx)
	if err != nil {
		return fmt.Errorf("Capabilities: %w", err)
	}
	aft := false
	for _, model := range capabilities.GetSupportedModels() {
		if model.GetName() == "openconfig-aft" {
			aft = true
		}
	}
	path := "/system/config/hostname"
	if _, err := client.SetJSONIETF(ctx, path, json.RawMessage(`"sat-edge"`), true); err != nil {
		return fmt.Errorf("Set: %w", err)
	}
	get, err := client.GetJSON(ctx, path)
	if err != nil {
		return fmt.Errorf("Get: %w", err)
	}
	if len(get.GetNotification()) != 1 || len(get.GetNotification()[0].GetUpdate()) != 1 ||
		string(get.GetNotification()[0].GetUpdate()[0].GetVal().GetJsonIetfVal()) != `"sat-edge"` {
		return errors.New("Get did not return the JSON_IETF value written by Set")
	}

	subscribeCtx, stopSubscribe := context.WithCancel(ctx)
	var samples atomic.Int32
	subscriptionDone := make(chan error, 1)
	go func() {
		subscriptionDone <- client.Subscribe(
			subscribeCtx,
			[]string{"/interfaces/interface[name=eth0]/state/counters"},
			10*time.Millisecond,
			func(response *gpb.SubscribeResponse) error {
				if response.GetUpdate() != nil && samples.Add(1) >= 2 {
					stopSubscribe()
				}
				return nil
			},
		)
	}()
	select {
	case err = <-subscriptionDone:
		if err != nil && !errors.Is(err, context.Canceled) && status.Code(err) != codes.Canceled {
			return fmt.Errorf("Subscribe: %w", err)
		}
	case <-ctx.Done():
		return errors.New("Subscribe did not deliver two samples")
	}
	if samples.Load() < 2 {
		return fmt.Errorf("Subscribe samples=%d, want at least 2", samples.Load())
	}

	ping, err := client.Ping(ctx, "192.0.2.1", "", "DEFAULT", 2)
	if err != nil {
		return fmt.Errorf("Ping: %w", err)
	}
	if len(ping) != 2 || ping[1].GetReceived() != 2 {
		return fmt.Errorf("Ping replies=%d or final receive count is wrong", len(ping))
	}
	targetTime, err := client.Time(ctx)
	if err != nil || targetTime == 0 {
		return fmt.Errorf("Time: value=%d error=%w", targetTime, err)
	}
	const rebootReason = "change-ticket-SF-LAB-001"
	if err := client.ScheduleReboot(ctx, 30*time.Second, rebootReason); err != nil {
		return fmt.Errorf("schedule Reboot: %w", err)
	}
	rebootStatus, err := client.RebootStatus(ctx)
	if err != nil || !rebootStatus.GetActive() || rebootStatus.GetReason() != rebootReason || rebootStatus.GetWait() == 0 {
		return fmt.Errorf("RebootStatus: status=%v error=%w", rebootStatus, err)
	}
	if err := client.CancelReboot(ctx, "pre-activation safety verification complete"); err != nil {
		return fmt.Errorf("CancelReboot: %w", err)
	}
	cancelled, err := client.RebootStatus(ctx)
	if err != nil || cancelled.GetActive() || cancelled.GetStatus().GetStatus() != spb.RebootStatus_STATUS_SUCCESS {
		return fmt.Errorf("cancelled RebootStatus: status=%v error=%w", cancelled, err)
	}
	bertErrors, err := client.RunBERT(ctx, "eth0", "bert-SF-LAB-001")
	if err != nil || bertErrors != 0 {
		return fmt.Errorf("BERT: total_errors=%d error=%w", bertErrors, err)
	}
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return fmt.Errorf("generate certificate key: %w", err)
	}
	now := time.Now()
	certificateDER, err := x509.CreateCertificate(rand.Reader, &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "sf-openconfig-emulator"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, BasicConstraintsValid: true,
	}, &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "sf-openconfig-emulator"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, BasicConstraintsValid: true,
	}, publicKey, privateKey)
	if err != nil {
		return fmt.Errorf("create certificate: %w", err)
	}
	certificateCount, err := client.LoadAndListCertificate(ctx, "sf-lab-management", certificateDER)
	if err != nil || certificateCount != 1 {
		return fmt.Errorf("certificate lifecycle: count=%d error=%w", certificateCount, err)
	}
	packets, err := client.CapturePackets(ctx, "eth0", 2)
	if err != nil {
		return fmt.Errorf("packet capture: %w", err)
	}
	for index, packet := range packets {
		if len(packet) < 14 || packet[12] != 0x08 || packet[13] != 0x00 {
			return fmt.Errorf("captured packet %d is not an Ethernet IPv4 frame", index)
		}
	}
	osVersion, err := client.InstallActivateVerifyOS(ctx, "emulator-v2", []byte("starfabric-signed-emulated-software-package-v2"))
	if err != nil || osVersion != "emulator-v2" {
		return fmt.Errorf("OS install/activate/verify: version=%q error=%w", osVersion, err)
	}
	result := report{
		Success: true, Capabilities: capabilities.GetGNMIVersion() != "", Set: true, Get: true,
		SubscribeSamples: samples.Load(), PingReplies: len(ping), TimeNanoseconds: targetTime,
		Target: address, OpenConfigAFTModel: aft, RebootScheduled: true, RebootCancelled: true,
		BERTZeroErrors: bertErrors == 0, CertificateCount: certificateCount,
		CapturedPackets: len(packets), OSVersion: osVersion,
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
