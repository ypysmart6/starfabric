package verification

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

// OTGProbe makes packet delivery an in-transaction postcondition. Reconcile
// rolls the device plan back when this verifier observes no traffic or an SLO
// breach, instead of relying on a later lab script to notice the failure.
type OTGProbe struct {
	endpoint       string
	client         *http.Client
	sampleWindow   time.Duration
	maxLossPercent float64
	minFrames      uint64
	flowNames      map[string]bool
}

type OTGConfig struct {
	Endpoint       string
	InsecureTLS    bool
	SampleWindow   time.Duration
	MaxLossPercent float64
	MinFrames      uint64
	FlowNames      []string
	HTTPTransport  http.RoundTripper
}

func NewOTGProbe(config OTGConfig) (*OTGProbe, error) {
	parsed, err := url.Parse(config.Endpoint)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("invalid OTG endpoint %q", config.Endpoint)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, errors.New("OTG endpoint scheme must be http or https")
	}
	if config.InsecureTLS && parsed.Scheme != "https" {
		return nil, errors.New("OTG insecure TLS requires an https endpoint")
	}
	if config.SampleWindow <= 0 {
		config.SampleWindow = 250 * time.Millisecond
	}
	if config.MaxLossPercent < 0 || config.MaxLossPercent > 100 {
		return nil, errors.New("OTG maximum loss percent must be in 0..100")
	}
	if config.MinFrames == 0 {
		config.MinFrames = 10
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if config.InsecureTLS {
		transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: true} // #nosec G402 -- explicit local-emulator option
	}
	var roundTripper http.RoundTripper = transport
	if config.HTTPTransport != nil {
		roundTripper = config.HTTPTransport
	}
	names := make(map[string]bool, len(config.FlowNames))
	for _, name := range config.FlowNames {
		if trimmed := strings.TrimSpace(name); trimmed != "" {
			names[trimmed] = true
		}
	}
	return &OTGProbe{
		endpoint: strings.TrimRight(config.Endpoint, "/"), client: &http.Client{Transport: roundTripper},
		sampleWindow: config.SampleWindow, maxLossPercent: config.MaxLossPercent, minFrames: config.MinFrames, flowNames: names,
	}, nil
}

type otgMetric struct {
	Name     string         `json:"name"`
	FramesTX flexibleUint64 `json:"frames_tx"`
	FramesRX flexibleUint64 `json:"frames_rx"`
}

type otgMetrics struct {
	FlowMetrics []otgMetric `json:"flow_metrics"`
}

type flexibleUint64 uint64

func (value *flexibleUint64) UnmarshalJSON(data []byte) error {
	var number json.Number
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&number); err == nil {
		parsed, err := strconv.ParseUint(string(number), 10, 64)
		if err == nil {
			*value = flexibleUint64(parsed)
			return nil
		}
	}
	var text string
	if err := json.Unmarshal(data, &text); err != nil {
		return errors.New("OTG frame counter must be an unsigned integer or integer string")
	}
	parsed, err := strconv.ParseUint(text, 10, 64)
	if err != nil {
		return fmt.Errorf("invalid OTG frame counter %q: %w", text, err)
	}
	*value = flexibleUint64(parsed)
	return nil
}

func (p *OTGProbe) Verify(ctx context.Context, _ model.RoutePlan) error {
	before, err := p.metrics(ctx)
	if err != nil {
		return err
	}
	timer := time.NewTimer(p.sampleWindow)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
	}
	after, err := p.metrics(ctx)
	if err != nil {
		return err
	}
	beforeByName := make(map[string]otgMetric, len(before))
	for _, metric := range before {
		beforeByName[metric.Name] = metric
	}
	var failures []error
	checked := 0
	for _, metric := range after {
		if len(p.flowNames) > 0 && !p.flowNames[metric.Name] {
			continue
		}
		checked++
		prior, exists := beforeByName[metric.Name]
		if !exists {
			failures = append(failures, fmt.Errorf("OTG flow %q appeared without a baseline", metric.Name))
			continue
		}
		tx, txUnderflow := subtract(uint64(metric.FramesTX), uint64(prior.FramesTX))
		rx, rxUnderflow := subtract(uint64(metric.FramesRX), uint64(prior.FramesRX))
		if txUnderflow || rxUnderflow {
			failures = append(failures, fmt.Errorf("OTG flow %q counters reset during verification", metric.Name))
			continue
		}
		if tx < p.minFrames {
			failures = append(failures, fmt.Errorf("OTG flow %q transmitted %d frames, require at least %d", metric.Name, tx, p.minFrames))
			continue
		}
		loss := float64(0)
		if rx < tx {
			loss = float64(tx-rx) * 100 / float64(tx)
		}
		if loss > p.maxLossPercent {
			failures = append(failures, fmt.Errorf("OTG flow %q loss %.3f%% exceeds %.3f%% (%d tx/%d rx)", metric.Name, loss, p.maxLossPercent, tx, rx))
		}
	}
	if checked == 0 {
		if len(p.flowNames) > 0 {
			return fmt.Errorf("OTG returned none of the required flows: %v", sortedKeys(p.flowNames))
		}
		return errors.New("OTG returned no flow metrics")
	}
	return errors.Join(failures...)
}

func (p *OTGProbe) metrics(ctx context.Context) ([]otgMetric, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, p.endpoint+"/monitor/metrics", strings.NewReader(`{"choice":"flow","flow":{}}`))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := p.client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("query OTG metrics: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return nil, fmt.Errorf("query OTG metrics: HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	var payload otgMetrics
	decoder := json.NewDecoder(io.LimitReader(response.Body, 4<<20))
	if err := decoder.Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode OTG metrics: %w", err)
	}
	return payload.FlowMetrics, nil
}

func subtract(current, previous uint64) (uint64, bool) {
	if current < previous {
		return 0, true
	}
	return current - previous, false
}

func sortedKeys(values map[string]bool) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}
