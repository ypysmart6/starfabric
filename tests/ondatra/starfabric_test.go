// Integration tests require the KNE topology in lab/kne. They intentionally
// use Ondatra's standard DUT/ATE abstraction so the same assertions can target
// Lemming, SONiC, a vendor virtual NOS, or physical hardware.
package ondatra_test

import (
	"context"
	"testing"
	"time"

	"github.com/open-traffic-generator/snappi/gosnappi"
	gribipb "github.com/openconfig/gribi/v1/proto/service"
	gribiclient "github.com/openconfig/gribigo/client"
	"github.com/openconfig/gribigo/fluent"
	"github.com/openconfig/ondatra"
	"github.com/openconfig/ondatra/gnmi"
	"github.com/openconfig/ondatra/gnmi/oc"
	kinit "github.com/openconfig/ondatra/knebind/init"
)

func TestMain(m *testing.M) { ondatra.RunTests(m, kinit.Init) }

func TestOpenConfigAndOTGContracts(t *testing.T) {
	dut := ondatra.DUT(t, "dut")
	port := dut.Port(t, "ate")
	iface := &oc.Interface{}
	iface.SetName(port.Name())
	iface.SetType(oc.IETFInterfaces_InterfaceType_ethernetCsmacd)
	address := iface.GetOrCreateSubinterface(0).GetOrCreateIpv4().GetOrCreateAddress("192.0.2.1")
	address.SetPrefixLength(30)
	gnmi.Replace(t, dut, gnmi.OC().Interface(port.Name()).Config(), iface)
	if got := gnmi.Get(t, dut, gnmi.OC().Interface(port.Name()).Name().State()); got != port.Name() {
		t.Fatalf("gNMI interface name = %q, want %q", got, port.Name())
	}

	ate := ondatra.ATE(t, "ate")
	config := gosnappi.NewConfig()
	config.Ports().Add().SetName("dut")
	config.Ports().Add().SetName("peer")
	ate.OTG().PushConfig(t, config)
	if names := gnmi.GetAll(t, ate.OTG(), gnmi.OTG().PortAny().Name().State()); len(names) != 2 {
		t.Fatalf("OTG port telemetry count = %d, want 2", len(names))
	}
}

func TestGRIBIFIBAcknowledgement(t *testing.T) {
	dut := ondatra.DUT(t, "dut")
	client, err := gribiclient.New(gribiclient.AllPrimaryClients(), gribiclient.FIBACK())
	if err != nil {
		t.Fatal(err)
	}
	if err := client.UseStub(dut.RawAPIs().GRIBI(t)); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	client.StartSending()
	t.Cleanup(func() { _ = client.Close() })

	nh, _ := fluent.NextHopEntry().WithNetworkInstance("DEFAULT").WithIndex(100).WithIPAddress("192.0.2.2").OpProto()
	nhg, _ := fluent.NextHopGroupEntry().WithNetworkInstance("DEFAULT").WithID(100).AddNextHop(100, 1).OpProto()
	ip, _ := fluent.IPv4Entry().WithNetworkInstance("DEFAULT").WithPrefix("198.51.100.0/24").WithNextHopGroup(100).OpProto()
	for index, operation := range []*gribipb.AFTOperation{nh, nhg, ip} {
		operation.Id = uint64(index + 1)
		operation.Op = gribipb.AFTOperation_ADD
	}
	client.Q(&gribipb.ModifyRequest{Operation: []*gribipb.AFTOperation{nh, nhg, ip}})
	if err := client.AwaitConverged(ctx); err != nil {
		t.Fatal(err)
	}
	results, err := client.Results()
	if err != nil {
		t.Fatal(err)
	}
	fibAcks := 0
	for _, result := range results {
		if result.ProgrammingResult == gribipb.AFTResult_FIB_PROGRAMMED {
			fibAcks++
		}
	}
	if fibAcks != 3 {
		t.Fatalf("FIB acknowledgements = %d, want 3; results=%v", fibAcks, results)
	}
}
