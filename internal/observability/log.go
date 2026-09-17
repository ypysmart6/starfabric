package observability

import (
	"encoding/json"
	"io"
	"sync"
	"time"
)

type Logger struct {
	mu sync.Mutex
	w  io.Writer
}

func NewLogger(w io.Writer) *Logger { return &Logger{w: w} }

func (l *Logger) Event(level, message string, fields map[string]any) {
	entry := make(map[string]any, len(fields)+3)
	entry["timestamp"] = time.Now().UTC().Format(time.RFC3339Nano)
	entry["level"] = level
	entry["message"] = message
	for key, value := range fields {
		entry[key] = value
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = json.NewEncoder(l.w).Encode(entry)
}
