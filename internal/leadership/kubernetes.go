// Package leadership implements Kubernetes Lease based single-writer
// election without putting high-frequency route state in CRDs.
package leadership

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"time"
)

const (
	defaultTokenPath     = "/var/run/secrets/kubernetes.io/serviceaccount/token"
	defaultNamespacePath = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
	defaultCAPath        = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
)

type Config struct {
	APIEndpoint   string
	Namespace     string
	NamespacePath string
	TokenPath     string
	CAPath        string
	LeaseName     string
	Identity      string
	LeaseDuration time.Duration
	RetryPeriod   time.Duration
}

type Elector struct {
	config Config
	client *http.Client
	token  string
	leader atomic.Bool
}

type lease struct {
	APIVersion string    `json:"apiVersion,omitempty"`
	Kind       string    `json:"kind,omitempty"`
	Metadata   metadata  `json:"metadata"`
	Spec       leaseSpec `json:"spec"`
}

type metadata struct {
	Name            string `json:"name"`
	Namespace       string `json:"namespace,omitempty"`
	ResourceVersion string `json:"resourceVersion,omitempty"`
}

type leaseSpec struct {
	HolderIdentity       string `json:"holderIdentity,omitempty"`
	LeaseDurationSeconds int32  `json:"leaseDurationSeconds,omitempty"`
	AcquireTime          string `json:"acquireTime,omitempty"`
	RenewTime            string `json:"renewTime,omitempty"`
	LeaseTransitions     int32  `json:"leaseTransitions,omitempty"`
}

func New(config Config) (*Elector, error) {
	if config.TokenPath == "" {
		config.TokenPath = defaultTokenPath
	}
	if config.NamespacePath == "" {
		config.NamespacePath = defaultNamespacePath
	}
	if config.CAPath == "" {
		config.CAPath = defaultCAPath
	}
	if config.APIEndpoint == "" {
		host, port := os.Getenv("KUBERNETES_SERVICE_HOST"), os.Getenv("KUBERNETES_SERVICE_PORT_HTTPS")
		if port == "" {
			port = "443"
		}
		if host == "" {
			return nil, errors.New("KUBERNETES_SERVICE_HOST is not set")
		}
		config.APIEndpoint = "https://" + host + ":" + port
	}
	endpoint, err := url.Parse(config.APIEndpoint)
	if err != nil || endpoint.Scheme == "" || endpoint.Host == "" {
		return nil, errors.New("Kubernetes API endpoint must be an absolute URL")
	}
	loopbackHTTP := endpoint.Scheme == "http" && (endpoint.Hostname() == "127.0.0.1" || endpoint.Hostname() == "localhost" || endpoint.Hostname() == "::1")
	if endpoint.Scheme != "https" && !loopbackHTTP {
		return nil, errors.New("Kubernetes API endpoint requires HTTPS except for a loopback test endpoint")
	}
	if config.Namespace == "" {
		value, err := os.ReadFile(config.NamespacePath)
		if err != nil {
			return nil, fmt.Errorf("read Kubernetes namespace: %w", err)
		}
		config.Namespace = strings.TrimSpace(string(value))
	}
	if config.LeaseName == "" || config.Identity == "" || config.Namespace == "" {
		return nil, errors.New("lease name, identity, and namespace are required")
	}
	if config.LeaseDuration <= 0 {
		config.LeaseDuration = 15 * time.Second
	}
	if config.RetryPeriod <= 0 {
		config.RetryPeriod = 2 * time.Second
	}
	if config.RetryPeriod >= config.LeaseDuration/2 {
		return nil, errors.New("leader retry period must be less than half the lease duration")
	}
	token, err := os.ReadFile(config.TokenPath)
	if err != nil {
		return nil, fmt.Errorf("read Kubernetes service-account token: %w", err)
	}
	if strings.TrimSpace(string(token)) == "" {
		return nil, errors.New("Kubernetes service-account token is empty")
	}
	transport := &http.Transport{}
	if !loopbackHTTP {
		ca, readErr := os.ReadFile(config.CAPath)
		if readErr != nil {
			return nil, fmt.Errorf("read Kubernetes CA: %w", readErr)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(ca) {
			return nil, errors.New("Kubernetes CA contains no certificate")
		}
		transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: pool}
	}
	client := &http.Client{Transport: transport, Timeout: config.RetryPeriod}
	return newWithClient(config, client, strings.TrimSpace(string(token)))
}

func newWithClient(config Config, client *http.Client, token string) (*Elector, error) {
	if _, err := url.ParseRequestURI(config.APIEndpoint); err != nil {
		return nil, fmt.Errorf("invalid Kubernetes API endpoint: %w", err)
	}
	return &Elector{config: config, client: client, token: token}, nil
}

func (e *Elector) IsLeader() bool { return e.leader.Load() }

// Run renews the Lease until cancellation. onAcquired must reload durable
// state; leadership is not published until that callback succeeds.
func (e *Elector) Run(ctx context.Context, onAcquired func() error, onChange func(bool, error)) {
	ticker := time.NewTicker(e.config.RetryPeriod)
	defer ticker.Stop()
	for {
		leader, err := e.tryAcquireOrRenew(ctx, time.Now().UTC())
		if leader && !e.leader.Load() && onAcquired != nil {
			if reloadErr := onAcquired(); reloadErr != nil {
				leader, err = false, fmt.Errorf("reload durable state before leadership: %w", reloadErr)
			}
		}
		old := e.leader.Swap(leader)
		if old != leader || err != nil {
			if onChange != nil {
				onChange(leader, err)
			}
		}
		select {
		case <-ctx.Done():
			e.leader.Store(false)
			return
		case <-ticker.C:
		}
	}
}

func (e *Elector) tryAcquireOrRenew(ctx context.Context, now time.Time) (bool, error) {
	current, status, err := e.get(ctx)
	if err != nil {
		return false, err
	}
	seconds := int32(e.config.LeaseDuration / time.Second)
	// Lease timestamps are metav1.MicroTime. Its JSON decoder requires
	// exactly six fractional digits; RFC3339Nano intermittently emits more.
	stamp := now.UTC().Format("2006-01-02T15:04:05.000000Z07:00")
	if status == http.StatusNotFound {
		created := lease{APIVersion: "coordination.k8s.io/v1", Kind: "Lease", Metadata: metadata{Name: e.config.LeaseName, Namespace: e.config.Namespace}, Spec: leaseSpec{HolderIdentity: e.config.Identity, LeaseDurationSeconds: seconds, AcquireTime: stamp, RenewTime: stamp}}
		status, err = e.write(ctx, http.MethodPost, e.collectionURL(), created)
		if status == http.StatusConflict {
			return false, nil
		}
		return status == http.StatusCreated, err
	}
	if status != http.StatusOK {
		return false, fmt.Errorf("get Kubernetes Lease returned %d", status)
	}
	expires := time.Time{}
	if current.Spec.RenewTime != "" {
		renewed, parseErr := time.Parse(time.RFC3339Nano, current.Spec.RenewTime)
		if parseErr != nil {
			return false, fmt.Errorf("parse Lease renewTime: %w", parseErr)
		}
		duration := time.Duration(current.Spec.LeaseDurationSeconds) * time.Second
		expires = renewed.Add(duration)
	}
	if current.Spec.HolderIdentity != "" && current.Spec.HolderIdentity != e.config.Identity && now.Before(expires) {
		return false, nil
	}
	if current.Spec.HolderIdentity != e.config.Identity {
		current.Spec.AcquireTime = stamp
		current.Spec.LeaseTransitions++
	}
	current.Spec.HolderIdentity = e.config.Identity
	current.Spec.LeaseDurationSeconds = seconds
	current.Spec.RenewTime = stamp
	status, err = e.write(ctx, http.MethodPut, e.itemURL(), current)
	if status == http.StatusConflict {
		return false, nil
	}
	return status == http.StatusOK, err
}

func (e *Elector) collectionURL() string {
	return strings.TrimRight(e.config.APIEndpoint, "/") + "/apis/coordination.k8s.io/v1/namespaces/" + url.PathEscape(e.config.Namespace) + "/leases"
}

func (e *Elector) itemURL() string {
	return e.collectionURL() + "/" + url.PathEscape(e.config.LeaseName)
}

func (e *Elector) get(ctx context.Context) (lease, int, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, e.itemURL(), nil)
	if err != nil {
		return lease{}, 0, err
	}
	if err := e.authorize(request); err != nil {
		return lease{}, 0, err
	}
	response, err := e.client.Do(request)
	if err != nil {
		return lease{}, 0, err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusNotFound {
		return lease{}, response.StatusCode, nil
	}
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return lease{}, response.StatusCode, fmt.Errorf("Kubernetes Lease GET: %s", strings.TrimSpace(string(body)))
	}
	var value lease
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&value); err != nil {
		return lease{}, response.StatusCode, err
	}
	return value, response.StatusCode, nil
}

func (e *Elector) write(ctx context.Context, method, endpoint string, value lease) (int, error) {
	body, err := json.Marshal(value)
	if err != nil {
		return 0, err
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	request.Header.Set("Content-Type", "application/json")
	if err := e.authorize(request); err != nil {
		return 0, err
	}
	response, err := e.client.Do(request)
	if err != nil {
		return 0, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusCreated && response.StatusCode != http.StatusConflict {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return response.StatusCode, fmt.Errorf("Kubernetes Lease %s: %d %s", method, response.StatusCode, strings.TrimSpace(string(body)))
	}
	return response.StatusCode, nil
}

func (e *Elector) authorize(request *http.Request) error {
	token := e.token
	if e.config.TokenPath != "" {
		// Projected service-account tokens rotate while a Pod is running.
		// Reopen the path so kubelet's atomic symlink replacement is observed.
		value, err := os.ReadFile(e.config.TokenPath)
		if err != nil {
			return fmt.Errorf("read Kubernetes service-account token: %w", err)
		}
		token = strings.TrimSpace(string(value))
	}
	if token == "" {
		return errors.New("Kubernetes service-account token is empty")
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+token)
	return nil
}
