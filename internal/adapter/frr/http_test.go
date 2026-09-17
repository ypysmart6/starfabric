package frr

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestAgentExecutionContract(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer secret" || r.URL.Path != "/v1/execute" {
			t.Error("missing authenticated execution")
		}
		var v struct {
			Node, Program string
			Args          []string
		}
		if err := json.NewDecoder(r.Body).Decode(&v); err != nil {
			t.Error(err)
		}
		if v.Node != "sat-1" || v.Program != "vtysh" || len(v.Args) != 2 || v.Args[1] != "show ip route json" {
			t.Errorf("unexpected request: %+v", v)
		}
		_, _ = w.Write([]byte(`{"output":"actual-rib","exit_code":0}`))
	}))
	defer s.Close()
	a, err := NewAgent(s.URL, "secret", true)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := a.Runner("sat-1", "vtysh").Run(context.Background(), "-c", "show ip route json")
	if err != nil || string(raw) != "actual-rib" {
		t.Fatalf("%q %v", raw, err)
	}
}

func TestAgentFailsClosed(t *testing.T) {
	for _, body := range []string{`{}`, `{"exit_code":1,"output":"failed"}`, `not-json`} {
		t.Run(body, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
			defer s.Close()
			a, _ := NewAgent(s.URL, "secret", true)
			if _, err := a.Runner("r1", "vtysh").Run(context.Background()); err == nil {
				t.Fatal("accepted invalid/failed agent result")
			}
		})
	}
	if _, err := NewAgent("http://example.com", "secret", false); err == nil {
		t.Fatal("allowed implicit plaintext")
	}
	if _, err := NewAgent("https://example.com", "", false); err == nil {
		t.Fatal("allowed empty token")
	}
	if _, err := NewAgent("https://user:secret@example.com", "secret", false); err == nil {
		t.Fatal("allowed URL credentials")
	}
}

func TestAgentCancellationAndRedirect(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/other", http.StatusTemporaryRedirect)
	}))
	defer s.Close()
	a, _ := NewAgent(s.URL, "secret", true)
	if _, err := a.Runner("r1", "ping").Run(context.Background()); err == nil {
		t.Fatal("followed execution redirect")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Nanosecond)
	defer cancel()
	time.Sleep(time.Millisecond)
	if _, err := a.Runner("r1", "ping").Run(ctx); err == nil {
		t.Fatal("ignored cancellation")
	}
}
