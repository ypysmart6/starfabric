package observability

import (
	"fmt"
	"io"
	"sort"
	"strings"
	"sync"
	"time"
)

// Metrics is a dependency-free Prometheus text collector. Keeping collection
// in-process lets control continue if an external telemetry backend fails.
type Metrics struct {
	mu       sync.RWMutex
	counters map[string]float64
	gauges   map[string]float64
}

func NewMetrics() *Metrics {
	return &Metrics{counters: make(map[string]float64), gauges: make(map[string]float64)}
}

func (m *Metrics) Inc(name string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.counters[name]++
}

func (m *Metrics) Add(name string, amount float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.counters[name] += amount
}

func (m *Metrics) Set(name string, value float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.gauges[name] = value
}

func (m *Metrics) ObserveDuration(name string, started time.Time) {
	m.Set(name+"_seconds", time.Since(started).Seconds())
}

func (m *Metrics) WritePrometheus(w io.Writer) error {
	m.mu.RLock()
	defer m.mu.RUnlock()
	names := make([]string, 0, len(m.counters)+len(m.gauges))
	for name := range m.counters {
		names = append(names, name)
	}
	for name := range m.gauges {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		metricName := sanitize(name)
		if value, ok := m.counters[name]; ok {
			if _, err := fmt.Fprintf(w, "# TYPE %s counter\n%s %g\n", metricName, metricName, value); err != nil {
				return err
			}
		} else if value, ok := m.gauges[name]; ok {
			if _, err := fmt.Fprintf(w, "# TYPE %s gauge\n%s %g\n", metricName, metricName, value); err != nil {
				return err
			}
		}
	}
	return nil
}

func sanitize(name string) string {
	name = strings.ToLower(name)
	var builder strings.Builder
	for _, char := range name {
		if (char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') || char == '_' || char == ':' {
			builder.WriteRune(char)
		} else {
			builder.WriteByte('_')
		}
	}
	return "starfabric_" + builder.String()
}
