package frr

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Agent executes bounded vtysh and packet probes on an explicitly registered
// router. It lets an unprivileged controller pod retain the real FRR adapter.
type Agent struct {
	endpoint string
	token    string
	client   *http.Client
}

func NewAgent(endpoint, token string, insecure bool) (*Agent, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("FRR agent requires an absolute HTTP(S) origin")
	}
	if u.Scheme != "https" && !(insecure && u.Scheme == "http") {
		return nil, errors.New("FRR agent requires HTTPS; plaintext SIL requires --frr-agent-insecure")
	}
	if strings.TrimSpace(token) == "" {
		return nil, errors.New("FRR agent token is empty")
	}
	return &Agent{endpoint: strings.TrimRight(endpoint, "/"), token: token,
		client: &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

type agentRunner struct {
	agent         *Agent
	node, program string
}

func (a *Agent) Runner(node, program string) Runner { return &agentRunner{a, node, program} }

func (r *agentRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	body, err := json.Marshal(struct {
		Node    string   `json:"node"`
		Program string   `json:"program"`
		Args    []string `json:"args"`
	}{r.node, r.program, args})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.agent.endpoint+"/v1/execute", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+r.agent.token)
	req.Header.Set("Content-Type", "application/json")
	response, err := r.agent.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("FRR agent transport: %w", err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > 4*1024*1024 {
		return nil, errors.New("FRR agent response too large")
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("FRR agent HTTP %d", response.StatusCode)
	}
	var result struct {
		Output   string `json:"output"`
		ExitCode *int   `json:"exit_code"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, err
	}
	if result.ExitCode == nil {
		return nil, errors.New("FRR agent response missing exit_code")
	}
	if *result.ExitCode != 0 {
		return []byte(result.Output), fmt.Errorf("FRR agent %s/%s exited %d", r.node, r.program, *result.ExitCode)
	}
	return []byte(result.Output), nil
}
