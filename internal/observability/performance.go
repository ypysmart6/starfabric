package observability

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

type performanceKey struct{}
type fieldsKey struct{}

// Correlation fields are copied at each boundary, never mutated by workers.
var CorrelationHeaders = map[string]string{
	"request_id": "X-Request-ID", "run_id": "X-Starfabric-Run-ID",
	"frame_id": "X-Starfabric-Frame-ID", "plan_id": "X-Starfabric-Plan-ID",
	"phase": "X-Starfabric-Phase", "attempt": "X-Starfabric-Attempt",
}

func WithFields(ctx context.Context, values map[string]string) context.Context {
	fields := Fields(ctx)
	for key, value := range values {
		fields[key] = value
	}
	return context.WithValue(ctx, fieldsKey{}, fields)
}

func Fields(ctx context.Context) map[string]string {
	result := make(map[string]string)
	fields, _ := ctx.Value(fieldsKey{}).(map[string]string)
	for key, value := range fields {
		result[key] = value
	}
	return result
}

type Timing struct {
	Calls         int     `json:"calls"`
	Failures      int     `json:"failures"`
	TotalSeconds  float64 `json:"total_seconds"`
	MaxSeconds    float64 `json:"max_seconds"`
	SlowestDevice string  `json:"slowest_device,omitempty"`
}

// Phase times are wall time. Work times may overlap, and must not be added to
// phase times. One bounded aggregate per transaction avoids per-device logs.
type Performance struct {
	mu     sync.Mutex
	Phases map[string]Timing `json:"phases"`
	Work   map[string]Timing `json:"work"`
	Counts map[string]int64  `json:"counts"`
	Maxima map[string]int64  `json:"maxima"`
}

func NewPerformance(ctx context.Context) (context.Context, *Performance) {
	p := &Performance{Phases: map[string]Timing{}, Work: map[string]Timing{}, Counts: map[string]int64{}, Maxima: map[string]int64{}}
	return context.WithValue(ctx, performanceKey{}, p), p
}

func Record(ctx context.Context, name, device string, phase bool, started time.Time, err error) {
	p, _ := ctx.Value(performanceKey{}).(*Performance)
	if p == nil {
		return
	}
	seconds := time.Since(started).Seconds()
	p.mu.Lock()
	defer p.mu.Unlock()
	target := p.Work
	if phase {
		target = p.Phases
	}
	v := target[name]
	v.Calls++
	if err != nil {
		v.Failures++
	}
	v.TotalSeconds += seconds
	if seconds > v.MaxSeconds {
		v.MaxSeconds, v.SlowestDevice = seconds, device
	}
	target[name] = v
}

func Count(ctx context.Context, name string, value int64) {
	p, _ := ctx.Value(performanceKey{}).(*Performance)
	if p == nil {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.Counts[name] += value
}

func Maximum(ctx context.Context, name string, value int64) {
	p, _ := ctx.Value(performanceKey{}).(*Performance)
	if p == nil {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.Maxima[name] = max(p.Maxima[name], value)
}

func Phase(ctx context.Context, name string, operation func(context.Context) error) error {
	ctx = WithFields(ctx, map[string]string{"phase": name})
	started := time.Now()
	err := operation(ctx)
	Record(ctx, name, "", true, started, err)
	return err
}

// CPU counters are cumulative; before/after samples permit interval deltas.
// Missing cgroup files are explicit, rather than interpreted as unlimited CPU.
func CPUResources() map[string]any {
	result := map[string]any{"gomaxprocs": runtime.GOMAXPROCS(0), "num_cpu": runtime.NumCPU()}
	if data, err := os.ReadFile("/proc/self/status"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "Cpus_allowed_list:") {
				result["cpus_allowed_list"] = strings.TrimSpace(strings.TrimPrefix(line, "Cpus_allowed_list:"))
			}
		}
	}
	paths := []string{"/sys/fs/cgroup"}
	if data, err := os.ReadFile("/proc/self/cgroup"); err == nil {
		result["proc_cgroup"] = string(data)
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "0::/") {
				path := filepath.Join("/sys/fs/cgroup", strings.TrimPrefix(line, "0::"))
				for path != "/sys/fs/cgroup" && strings.HasPrefix(path, "/sys/fs/cgroup/") {
					paths = append(paths, path)
					path = filepath.Dir(path)
				}
			}
		}
	}
	groups := map[string]any{}
	for _, path := range paths {
		files := map[string]string{}
		for _, name := range []string{"cpu.max", "cpu.stat", "cpuset.cpus.effective"} {
			if data, err := os.ReadFile(filepath.Join(path, name)); err == nil {
				files[name] = strings.TrimSpace(string(data))
			}
		}
		if len(files) > 0 {
			groups[path] = files
		}
	}
	result["cgroups"] = groups
	return result
}
