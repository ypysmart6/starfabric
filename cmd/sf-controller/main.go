package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"

	"github.com/starfabric/starfabric/internal/adapter"
	"github.com/starfabric/starfabric/internal/adapter/frr"
	openconfigadapter "github.com/starfabric/starfabric/internal/adapter/openconfig"
	"github.com/starfabric/starfabric/internal/api"
	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/leadership"
	"github.com/starfabric/starfabric/internal/model"
	"github.com/starfabric/starfabric/internal/observability"
	"github.com/starfabric/starfabric/internal/scenario"
	"github.com/starfabric/starfabric/internal/verification"
)

var version = "dev"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "sf-controller:", err)
		os.Exit(1)
	}
}

func run() error {
	var (
		scenarioPath     = flag.String("scenario", "scenarios/leo-resilient.json", "initial topology and intents")
		listen           = flag.String("listen", "127.0.0.1:8080", "HTTP listen address")
		grpcListen       = flag.String("grpc-listen", "", "optional versioned gRPC API listen address")
		healthListen     = flag.String("health-listen", "", "optional cluster-internal health/readiness listen address")
		stateDir         = flag.String("state-dir", "data", "durable state directory")
		planTTL          = flag.Duration("plan-ttl", 30*time.Second, "maximum lifetime of a computed forwarding plan")
		operationTimeout = flag.Duration("operation-timeout", 2*time.Second, "timeout of each device operation")
		httpWriteTimeout = flag.Duration("http-write-timeout", 30*time.Second, "HTTP response deadline including reconciliation")
		logFile          = flag.String("log-file", "", "optional JSON log file for collector tailing")
		otlpEndpoint     = flag.String("otlp-endpoint", "", "OTLP/HTTP collector host:port; empty disables traces")
		otlpInsecure     = flag.Bool("otlp-insecure", false, "use plaintext OTLP transport inside a trusted cluster")
		tokenFile        = flag.String("token-file", "", "file containing API bearer token")
		tlsCert          = flag.String("tls-cert", "", "TLS certificate path")
		tlsKey           = flag.String("tls-key", "", "TLS private key path")
		tlsClientCA      = flag.String("tls-client-ca", "", "CA bundle used to require and verify mTLS clients")
		showVersion      = flag.Bool("version", false, "print version and exit")
		adapterKind      = flag.String("adapter", "memory", "device adapter: memory, frr, or openconfig")
		frrPrefix        = flag.String("frr-container-prefix", "clab-starfabric-", "FRR container name prefix")
		frrEndpoint      = flag.String("frr-agent-endpoint", "", "remote FRR execution agent origin for unprivileged controller pods")
		frrTokenFile     = flag.String("frr-agent-token-file", "", "FRR execution agent bearer token file")
		frrInsecure      = flag.Bool("frr-agent-insecure", false, "allow plaintext FRR agent transport in an isolated SIL network")
		ocUsername       = flag.String("openconfig-username", "", "gNMI/gNOI/gRIBI username")
		ocPasswordFile   = flag.String("openconfig-password-file", "", "file containing OpenConfig password")
		ocCA             = flag.String("openconfig-ca", "", "OpenConfig server CA bundle")
		ocCert           = flag.String("openconfig-client-cert", "", "OpenConfig mTLS client certificate")
		ocKey            = flag.String("openconfig-client-key", "", "OpenConfig mTLS client key")
		ocServerName     = flag.String("openconfig-server-name", "", "OpenConfig TLS server name")
		ocInsecure       = flag.Bool("openconfig-insecure", false, "disable OpenConfig TLS for local emulators only")
		requireGNOI      = flag.Bool("openconfig-require-gnoi", true, "require successful gNOI Time health check")
		maxTelemetryAge  = flag.Duration("max-telemetry-age", 30*time.Second, "reject nonzero link telemetry timestamps older than this; 0 disables")
		otgAPI           = flag.String("otg-api", "", "OTG API base URL; enables packet-loss verification inside each transaction")
		otgInsecure      = flag.Bool("otg-insecure", false, "disable OTG TLS certificate verification for local labs only")
		otgWindow        = flag.Duration("otg-sample-window", 250*time.Millisecond, "OTG counter sampling window")
		otgMaxLoss       = flag.Float64("otg-max-loss-percent", 6, "maximum packet loss accepted during commit verification")
		otgMinFrames     = flag.Uint64("otg-min-frames", 50, "minimum transmitted frames required during commit verification")
		otgFlows         = flag.String("otg-flow-names", "", "optional comma-separated OTG flow allowlist")
		reconcileEvery   = flag.Duration("reconcile-interval", 5*time.Second, "continuous reconciliation interval; 0 disables")
		leaderElection   = flag.Bool("leader-election", false, "use a Kubernetes Lease to enforce a single writer")
		leaderAPI        = flag.String("leader-api-endpoint", "", "Kubernetes API URL; defaults to in-cluster discovery")
		leaseName        = flag.String("leader-lease-name", "starfabric-controller", "Kubernetes Lease name")
		leaseNamespace   = flag.String("leader-namespace", "", "Kubernetes namespace; defaults to service-account namespace")
		leaseTokenFile   = flag.String("leader-token-file", "", "Kubernetes service-account token path")
		leaseCAFile      = flag.String("leader-ca-file", "", "Kubernetes CA bundle path")
		leaderIdentity   = flag.String("leader-identity", os.Getenv("HOSTNAME"), "unique controller identity")
		leaseDuration    = flag.Duration("leader-lease-duration", 15*time.Second, "Kubernetes Lease duration")
		leaseRetry       = flag.Duration("leader-retry-period", 2*time.Second, "Kubernetes Lease retry period")
	)
	flag.Parse()
	if *showVersion {
		fmt.Println(version)
		return nil
	}
	definition, err := scenario.Load(*scenarioPath)
	if err != nil {
		return err
	}
	if (*tlsCert == "") != (*tlsKey == "") {
		return errors.New("tls-cert and tls-key must be supplied together")
	}
	if *tlsClientCA != "" && *tlsCert == "" {
		return errors.New("tls-client-ca requires tls-cert and tls-key")
	}
	if err := os.MkdirAll(*stateDir, 0o750); err != nil {
		return fmt.Errorf("create state directory: %w", err)
	}
	token := ""
	if *tokenFile != "" {
		data, readErr := os.ReadFile(*tokenFile)
		if readErr != nil {
			return fmt.Errorf("read token: %w", readErr)
		}
		token = strings.TrimSpace(string(data))
		if token == "" {
			return errors.New("token file is empty")
		}
	}
	ocPassword, err := readSecret(*ocPasswordFile, "OpenConfig password")
	if err != nil {
		return err
	}
	config := app.Config{
		TopologyStatePath: filepath.Join(*stateDir, "topology.json"), ReconcileStatePath: filepath.Join(*stateDir, "reconciler.json"),
		IntentsStatePath: filepath.Join(*stateDir, "intents.json"), PredictiveStatePath: filepath.Join(*stateDir, "predictive.json"),
		PlanTTL: *planTTL, MaxTelemetryAge: *maxTelemetryAge, NodeDisjoint: false,
		OperationTimeout: *operationTimeout, RetryCount: 2, RetryBackoff: 50 * time.Millisecond, BatchSize: 4,
	}
	var elector *leadership.Elector
	if *leaderElection {
		if *leaderIdentity == "" {
			return errors.New("leader-identity is required when leader election is enabled")
		}
		elector, err = leadership.New(leadership.Config{
			APIEndpoint: *leaderAPI, Namespace: *leaseNamespace, LeaseName: *leaseName, Identity: *leaderIdentity,
			TokenPath: *leaseTokenFile, CAPath: *leaseCAFile,
			LeaseDuration: *leaseDuration, RetryPeriod: *leaseRetry,
		})
		if err != nil {
			return fmt.Errorf("initialize leader election: %w", err)
		}
		config.CanMutate = elector.IsLeader
	}
	var dataPlaneVerifiers verification.Multi
	switch *adapterKind {
	case "memory":
	case "frr":
		var remote *frr.Agent
		if *frrEndpoint != "" {
			secret, err := readSecret(*frrTokenFile, "FRR agent token")
			if err != nil {
				return err
			}
			remote, err = frr.NewAgent(*frrEndpoint, secret, *frrInsecure)
			if err != nil {
				return err
			}
		}
		dataPlaneVerifiers = append(dataPlaneVerifiers, frr.NewAgentPingProbe(definition.Topology.Nodes, remote))
		config.DeviceFactory = func(node model.Node) (adapter.DeviceAdapter, error) {
			container := ""
			if node.Labels != nil {
				container = node.Labels["frr_container"]
			}
			if container == "" {
				container = *frrPrefix + node.ID
			}
			var runner frr.Runner = frr.NewExecRunner("docker", "exec", container, "vtysh")
			if remote != nil {
				runner = remote.Runner(node.ID, "vtysh")
			}
			return frr.NewPersistentDevice(node.ID, runner, adapterStatePath(*stateDir, "frr", node.ID))
		}
	case "openconfig":
		config.DeviceFactory = func(node model.Node) (adapter.DeviceAdapter, error) {
			gnmiTarget := node.Labels["gnmi_target"]
			gribiTarget := node.Labels["gribi_target"]
			if gnmiTarget == "" || gribiTarget == "" {
				return nil, fmt.Errorf("node %s requires gnmi_target and gribi_target labels", node.ID)
			}
			serverName := *ocServerName
			if node.Labels["tls_server_name"] != "" {
				serverName = node.Labels["tls_server_name"]
			}
			management := openconfigadapter.Endpoint{
				Address: gnmiTarget, Username: *ocUsername, Password: ocPassword, CAFile: *ocCA,
				CertFile: *ocCert, KeyFile: *ocKey, ServerName: serverName, Insecure: *ocInsecure,
			}
			gribiEndpoint := management
			gribiEndpoint.Address = gribiTarget
			return openconfigadapter.NewDevice(openconfigadapter.DeviceConfig{
				Name: node.ID, Management: management, GRIBI: gribiEndpoint,
				NetworkInstance: node.Labels["network_instance"], RequireGNOITime: *requireGNOI,
				StatePath: adapterStatePath(*stateDir, "openconfig", node.ID),
			})
		}
	default:
		return fmt.Errorf("unsupported adapter %q", *adapterKind)
	}
	if *otgAPI != "" {
		probe, probeErr := verification.NewOTGProbe(verification.OTGConfig{
			Endpoint: *otgAPI, InsecureTLS: *otgInsecure, SampleWindow: *otgWindow,
			MaxLossPercent: *otgMaxLoss, MinFrames: *otgMinFrames, FlowNames: strings.Split(*otgFlows, ","),
		})
		if probeErr != nil {
			return probeErr
		}
		dataPlaneVerifiers = append(dataPlaneVerifiers, probe)
	}
	if len(dataPlaneVerifiers) > 0 {
		config.DataPlaneVerifier = dataPlaneVerifiers
	}
	logWriter := io.Writer(os.Stdout)
	var logHandle *os.File
	if *logFile != "" {
		if err := os.MkdirAll(filepath.Dir(*logFile), 0o750); err != nil {
			return fmt.Errorf("create log directory: %w", err)
		}
		logHandle, err = os.OpenFile(*logFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
		if err != nil {
			return fmt.Errorf("open log file: %w", err)
		}
		defer logHandle.Close()
		logWriter = io.MultiWriter(os.Stdout, logHandle)
	}
	application, err := app.Load(config, definition.Topology, definition.Intents, logWriter)
	if err != nil {
		return err
	}
	defer application.Close()
	logger := observability.NewLogger(logWriter)
	traceShutdown, err := observability.SetupTracing(context.Background(), observability.TraceConfig{
		Endpoint: *otlpEndpoint, Insecure: *otlpInsecure, ServiceName: "starfabric-controller", Version: version,
	})
	if err != nil {
		return err
	}
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = traceShutdown(shutdownCtx)
	}()
	handler := api.New(application, token, logger).Handler()
	if *otlpEndpoint != "" {
		handler = otelhttp.NewHandler(handler, "starfabric.api")
	}
	server := &http.Server{
		Addr: *listen, Handler: handler, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 15 * time.Second, WriteTimeout: *httpWriteTimeout, IdleTimeout: 60 * time.Second,
	}
	if *tlsCert != "" {
		server.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS13}
	}
	if *tlsClientCA != "" {
		clientCAs, caErr := loadCertPool(*tlsClientCA)
		if caErr != nil {
			return caErr
		}
		server.TLSConfig.ClientAuth = tls.RequireAndVerifyClientCert
		server.TLSConfig.ClientCAs = clientCAs
	}
	var healthServer *http.Server
	if *healthListen != "" {
		healthServer = &http.Server{
			Addr: *healthListen, Handler: api.NewHealthHandler(application), ReadHeaderTimeout: 2 * time.Second,
			ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second,
		}
	}
	var grpcServer *grpc.Server
	var grpcListener net.Listener
	if *grpcListen != "" {
		grpcOptions := []grpc.ServerOption{}
		if *tlsCert != "" {
			certificate, certificateErr := tls.LoadX509KeyPair(*tlsCert, *tlsKey)
			if certificateErr != nil {
				return fmt.Errorf("load gRPC TLS certificate: %w", certificateErr)
			}
			grpcTLS := server.TLSConfig.Clone()
			grpcTLS.Certificates = []tls.Certificate{certificate}
			grpcOptions = append(grpcOptions, grpc.Creds(credentials.NewTLS(grpcTLS)))
		}
		grpcServer = api.NewGRPCServer(application, token, logger, grpcOptions...)
		grpcListener, err = net.Listen("tcp", *grpcListen)
		if err != nil {
			return fmt.Errorf("listen for gRPC API: %w", err)
		}
		defer grpcListener.Close()
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if elector != nil {
		go elector.Run(ctx, application.Reload, func(leader bool, electionErr error) {
			application.Metrics().Set("leader", map[bool]float64{true: 1, false: 0}[leader])
			fields := map[string]any{"leader": leader, "identity": *leaderIdentity}
			if electionErr != nil {
				fields["error"] = electionErr.Error()
			}
			logger.Event(map[bool]string{true: "info", false: "warn"}[leader], "leadership changed", fields)
		})
	}
	if *reconcileEvery < 0 {
		return errors.New("reconcile-interval cannot be negative")
	}
	if *reconcileEvery > 0 {
		go reconcileLoop(ctx, application, logger, *reconcileEvery)
	}
	errChannel := make(chan error, 3)
	go func() {
		logger.Event("info", "controller listening", map[string]any{"address": *listen, "version": version, "tls": *tlsCert != ""})
		if *tlsCert != "" {
			errChannel <- server.ListenAndServeTLS(*tlsCert, *tlsKey)
		} else {
			errChannel <- server.ListenAndServe()
		}
	}()
	if healthServer != nil {
		go func() {
			logger.Event("info", "health listener ready", map[string]any{"address": *healthListen})
			errChannel <- healthServer.ListenAndServe()
		}()
	}
	if grpcServer != nil {
		go func() {
			logger.Event("info", "gRPC controller listening", map[string]any{"address": *grpcListen, "version": version, "tls": *tlsCert != ""})
			errChannel <- grpcServer.Serve(grpcListener)
		}()
	}
	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		logger.Event("info", "controller shutting down", nil)
		var shutdownErr error
		if healthServer != nil {
			shutdownErr = errors.Join(shutdownErr, healthServer.Shutdown(shutdownCtx))
		}
		if grpcServer != nil {
			grpcServer.GracefulStop()
		}
		shutdownErr = errors.Join(shutdownErr, server.Shutdown(shutdownCtx))
		return shutdownErr
	case serverErr := <-errChannel:
		if errors.Is(serverErr, http.ErrServerClosed) || errors.Is(serverErr, grpc.ErrServerStopped) {
			return nil
		}
		return serverErr
	}
}

func adapterStatePath(stateDir, kind, nodeID string) string {
	digest := sha256.Sum256([]byte(nodeID))
	return filepath.Join(stateDir, "adapters", kind, fmt.Sprintf("%x.json", digest[:12]))
}

func loadCertPool(path string) (*x509.CertPool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read TLS client CA: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(data) {
		return nil, errors.New("TLS client CA contains no valid certificates")
	}
	return pool, nil
}

func readSecret(path, description string) (string, error) {
	if path == "" {
		return "", nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read %s: %w", description, err)
	}
	value := strings.TrimSpace(string(data))
	if value == "" {
		return "", fmt.Errorf("%s file is empty", description)
	}
	return value, nil
}

func reconcileLoop(ctx context.Context, application *app.App, logger *observability.Logger, interval time.Duration) {
	reconcile := func() {
		if !application.IsLeader() {
			return
		}
		plan, status, err := application.Reconcile(ctx)
		if err != nil {
			logger.Event("error", "continuous reconciliation failed", map[string]any{"plan_id": plan.ID, "phase": status.Phase, "error": err.Error()})
		}
	}
	reconcile()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			reconcile()
		}
	}
}
