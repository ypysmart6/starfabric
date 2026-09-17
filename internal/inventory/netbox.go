package inventory

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/netip"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/starfabric/starfabric/internal/model"
)

// NetBoxClient supports the common REST shape shared by NetBox and Nautobot.
// It imports slow-changing inventory and cable metadata only; operational link
// telemetry remains in StarFabric's versioned topology stream.
type NetBoxClient struct {
	base  *url.URL
	token string
	http  *http.Client
	tag   string
}

func NewNetBoxClient(baseURL, token, tag string, client *http.Client) (*NetBoxClient, error) {
	base, err := url.Parse(baseURL)
	if err != nil || base.Scheme == "" || base.Host == "" {
		return nil, errors.New("NetBox base URL must be an absolute http(s) URL")
	}
	if base.Scheme != "https" && base.Hostname() != "127.0.0.1" && base.Hostname() != "localhost" {
		return nil, errors.New("NetBox requires HTTPS except for a loopback development endpoint")
	}
	if client == nil {
		client = &http.Client{Timeout: 15 * time.Second}
	}
	return &NetBoxClient{base: base, token: token, http: client, tag: tag}, nil
}

type apiPage[T any] struct {
	Next    string `json:"next"`
	Results []T    `json:"results"`
}

type nestedName struct {
	Name string `json:"name"`
	Slug string `json:"slug"`
}

type address struct {
	Address string `json:"address"`
}

type device struct {
	Name         string         `json:"name"`
	Role         nestedName     `json:"role"`
	DeviceRole   nestedName     `json:"device_role"`
	Platform     nestedName     `json:"platform"`
	Site         nestedName     `json:"site"`
	PrimaryIPv4  *address       `json:"primary_ip4"`
	PrimaryIPv6  *address       `json:"primary_ip6"`
	CustomFields map[string]any `json:"custom_fields"`
}

type termination struct {
	ObjectType string `json:"object_type"`
	Object     struct {
		Device nestedName `json:"device"`
	} `json:"object"`
}

type cable struct {
	ID            int64          `json:"id"`
	Status        nestedName     `json:"status"`
	ATerminations []termination  `json:"a_terminations"`
	BTerminations []termination  `json:"b_terminations"`
	CustomFields  map[string]any `json:"custom_fields"`
}

func (c *NetBoxClient) Snapshot(ctx context.Context) (model.TopologySnapshot, error) {
	var devices []device
	if err := c.pages(ctx, "api/dcim/devices/", &devices); err != nil {
		return model.TopologySnapshot{}, fmt.Errorf("load devices: %w", err)
	}
	var cables []cable
	if err := c.pages(ctx, "api/dcim/cables/", &cables); err != nil {
		return model.TopologySnapshot{}, fmt.Errorf("load cables: %w", err)
	}
	now := time.Now().UTC()
	snapshot := model.TopologySnapshot{Version: 1, GeneratedAt: now, ValidFrom: now}
	for _, value := range devices {
		if value.Name == "" {
			continue
		}
		role := value.Role.Slug
		if role == "" {
			role = value.DeviceRole.Slug
		}
		labels := map[string]string{"source_of_truth": c.base.Host, "site": value.Site.Slug, "platform": value.Platform.Slug}
		copyStringFields(labels, value.CustomFields, "gnmi_target", "gribi_target", "tls_server_name", "network_instance", "orbital_plane")
		snapshot.Nodes = append(snapshot.Nodes, model.Node{ID: value.Name, Kind: role, Loopback: preferredAddress(value.PrimaryIPv6, value.PrimaryIPv4), Labels: labels, Enabled: true})
	}
	for _, value := range cables {
		if len(value.ATerminations) == 0 || len(value.BTerminations) == 0 {
			continue
		}
		a, b := value.ATerminations[0].Object.Device.Name, value.BTerminations[0].Object.Device.Name
		if a == "" || b == "" || a == b {
			continue
		}
		up := value.Status.Slug == "connected" || value.Status.Slug == "active" || value.Status.Slug == ""
		latency := intField(value.CustomFields, "latency_us", 1000)
		capacity := intField(value.CustomFields, "capacity_bps", 1_000_000_000)
		linkType := stringField(value.CustomFields, "link_type")
		riskGroups := stringListField(value.CustomFields, "risk_groups")
		baseID := "netbox-cable-" + strconv.FormatInt(value.ID, 10)
		snapshot.Links = append(snapshot.Links,
			model.Link{ID: baseID + "-a-b", Source: a, Target: b, LinkType: linkType, AdminUp: true, OperationalUp: up, LatencyUS: latency, CapacityBPS: capacity, RiskGroups: riskGroups},
			model.Link{ID: baseID + "-b-a", Source: b, Target: a, LinkType: linkType, AdminUp: true, OperationalUp: up, LatencyUS: latency, CapacityBPS: capacity, RiskGroups: append([]string(nil), riskGroups...)},
		)
	}
	if err := snapshot.Validate(); err != nil {
		return model.TopologySnapshot{}, fmt.Errorf("NetBox inventory is not a valid topology: %w", err)
	}
	return snapshot, nil
}

func (c *NetBoxClient) pages(ctx context.Context, path string, destination any) error {
	next := c.base.ResolveReference(&url.URL{Path: strings.TrimSuffix(c.base.Path, "/") + "/" + path})
	query := next.Query()
	query.Set("limit", "200")
	if c.tag != "" {
		query.Set("tag", c.tag)
	}
	next.RawQuery = query.Encode()
	for next != nil {
		if next.Scheme != c.base.Scheme || next.Host != c.base.Host {
			return errors.New("refused cross-origin pagination URL")
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, next.String(), nil)
		if err != nil {
			return err
		}
		request.Header.Set("Accept", "application/json")
		if c.token != "" {
			request.Header.Set("Authorization", "Token "+c.token)
		}
		response, err := c.http.Do(request)
		if err != nil {
			return err
		}
		data, readErr := io.ReadAll(io.LimitReader(response.Body, 16<<20))
		closeErr := response.Body.Close()
		if readErr != nil {
			return readErr
		}
		if closeErr != nil {
			return closeErr
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			return fmt.Errorf("NetBox returned %s", response.Status)
		}
		var envelope struct {
			Next    string          `json:"next"`
			Results json.RawMessage `json:"results"`
		}
		if err := json.Unmarshal(data, &envelope); err != nil {
			return err
		}
		switch out := destination.(type) {
		case *[]device:
			var page []device
			if err := json.Unmarshal(envelope.Results, &page); err != nil {
				return err
			}
			*out = append(*out, page...)
		case *[]cable:
			var page []cable
			if err := json.Unmarshal(envelope.Results, &page); err != nil {
				return err
			}
			*out = append(*out, page...)
		default:
			return errors.New("unsupported pagination destination")
		}
		if envelope.Next == "" {
			next = nil
			continue
		}
		next, err = url.Parse(envelope.Next)
		if err != nil {
			return err
		}
		if !next.IsAbs() {
			next = c.base.ResolveReference(next)
		}
	}
	return nil
}

func preferredAddress(addresses ...*address) string {
	for _, item := range addresses {
		if item == nil {
			continue
		}
		prefix, err := netip.ParsePrefix(item.Address)
		if err == nil {
			return prefix.Addr().String()
		}
	}
	return ""
}
func stringField(fields map[string]any, key string) string {
	value, _ := fields[key].(string)
	return value
}
func intField(fields map[string]any, key string, fallback int64) int64 {
	switch value := fields[key].(type) {
	case float64:
		return int64(value)
	case json.Number:
		result, _ := value.Int64()
		return result
	}
	return fallback
}
func stringListField(fields map[string]any, key string) []string {
	var output []string
	switch value := fields[key].(type) {
	case string:
		for _, item := range strings.Split(value, ",") {
			if item = strings.TrimSpace(item); item != "" {
				output = append(output, item)
			}
		}
	case []any:
		for _, item := range value {
			if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
				output = append(output, strings.TrimSpace(text))
			}
		}
	}
	sort.Strings(output)
	return output
}
func copyStringFields(labels map[string]string, fields map[string]any, keys ...string) {
	for _, key := range keys {
		if value := stringField(fields, key); value != "" {
			labels[key] = value
		}
	}
}
