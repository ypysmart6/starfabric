package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/starfabric/starfabric/internal/app"
	"github.com/starfabric/starfabric/internal/planner"
	"github.com/starfabric/starfabric/internal/report"
	"github.com/starfabric/starfabric/internal/scenario"
)

var version = "dev"

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "sfctl:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		usage()
		return errors.New("command required")
	}
	switch args[0] {
	case "version":
		fmt.Println(version)
		return nil
	case "scenario":
		return scenarioCommand(args[1:])
	case "constellation":
		return constellationCommand(args[1:])
	case "experiment":
		return experimentCommand(args[1:])
	case "report":
		return reportCommand(args[1:])
	case "get":
		return getCommand(args[1:])
	case "reconcile":
		return requestCommand(args[1:], http.MethodPost, "/api/v1/reconcile", nil)
	case "event":
		return eventCommand(args[1:])
	case "fault":
		return faultCommand(args[1:])
	default:
		usage()
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `StarFabric control CLI

Usage:
  sfctl constellation generate --satellites 120 --gateways 4 --planes 12 --output scenarios/leo-120.json
  sfctl scenario validate --file scenarios/leo-resilient.json
  sfctl experiment run --scenario scenarios/leo-resilient.json --output reports/run.json --html reports/run.html
  sfctl get status|topology|devices [--endpoint http://localhost:8080]
  sfctl reconcile [--endpoint http://localhost:8080]
  sfctl event --file event.json [--reconcile]
  sfctl fault device --device sat-02 --mode commit
  sfctl report export --input reports/run.json --output reports/run.html`)
}

func scenarioCommand(args []string) error {
	if len(args) == 0 || args[0] != "validate" {
		return errors.New("usage: sfctl scenario validate --file PATH")
	}
	flags := flag.NewFlagSet("scenario validate", flag.ContinueOnError)
	checkPaths := flags.Bool("check-paths", false, "also verify initial intent primary and backup paths with the controller planner")
	path := flags.String("file", "", "scenario file")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if *path == "" {
		return errors.New("--file is required")
	}
	value, err := scenario.Load(*path)
	if err != nil {
		return err
	}
	if *checkPaths {
		at := value.Topology.ValidFrom
		if at.IsZero() {
			at = time.Now().UTC()
		}
		if _, err := planner.New(planner.Config{}).Build(value.Topology, value.Intents, at); err != nil {
			return fmt.Errorf("initial path preflight: %w", err)
		}
	}
	fmt.Printf("PASS: scenario %s, topology v%d, %d nodes, %d links, %d intents, %d actions\n", value.ID, value.Topology.Version, len(value.Topology.Nodes), len(value.Topology.Links), len(value.Intents), len(value.Timeline))
	return nil
}

func experimentCommand(args []string) error {
	if len(args) == 0 || args[0] != "run" {
		return errors.New("usage: sfctl experiment run --scenario PATH [--output PATH] [--html PATH]")
	}
	flags := flag.NewFlagSet("experiment run", flag.ContinueOnError)
	path := flags.String("scenario", "", "scenario file")
	output := flags.String("output", "reports/latest.json", "JSON report")
	htmlOutput := flags.String("html", "reports/latest.html", "HTML report")
	realTime := flags.Bool("real-time", false, "honor timeline delays")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if *path == "" {
		return errors.New("--scenario is required")
	}
	definition, err := scenario.Load(*path)
	if err != nil {
		return err
	}
	runner := scenario.Runner{Config: app.Config{PlanTTL: 30 * time.Second, OperationTimeout: 2 * time.Second, RetryCount: 1, BatchSize: 4}, Log: os.Stderr, RealTime: *realTime}
	result, err := runner.Run(context.Background(), definition)
	if err != nil {
		return err
	}
	if err := writeJSONFile(*output, result); err != nil {
		return err
	}
	if err := writeHTMLFile(*htmlOutput, result); err != nil {
		return err
	}
	verdict := "PASS"
	if !result.Success {
		verdict = "FAIL"
	}
	fmt.Printf("%s: scenario=%s steps=%d failures=%d rollbacks=%d report=%s\n", verdict, result.ScenarioID, len(result.Steps), result.FailureCount, result.RollbackCount, *htmlOutput)
	if !result.Success {
		return errors.New("experiment assertions failed")
	}
	return nil
}

func reportCommand(args []string) error {
	if len(args) == 0 || args[0] != "export" {
		return errors.New("usage: sfctl report export --input PATH --output PATH")
	}
	flags := flag.NewFlagSet("report export", flag.ContinueOnError)
	input := flags.String("input", "", "JSON report")
	output := flags.String("output", "", "HTML report")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if *input == "" || *output == "" {
		return errors.New("--input and --output are required")
	}
	data, err := os.ReadFile(*input)
	if err != nil {
		return err
	}
	var value scenario.Report
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	return writeHTMLFile(*output, value)
}

func getCommand(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: sfctl get status|topology|devices")
	}
	path := ""
	switch args[0] {
	case "status":
		path = "/api/v1/status"
	case "topology":
		path = "/api/v1/topology"
	case "devices":
		path = "/api/v1/devices"
	default:
		return fmt.Errorf("unknown resource %q", args[0])
	}
	return requestCommand(args[1:], http.MethodGet, path, nil)
}

func eventCommand(args []string) error {
	flags := flag.NewFlagSet("event", flag.ContinueOnError)
	path := flags.String("file", "", "topology event JSON")
	reconcile := flags.Bool("reconcile", false, "run reconciliation after event")
	endpoint := flags.String("endpoint", envDefault("SF_ENDPOINT", "http://127.0.0.1:8080"), "controller URL")
	token := flags.String("token", os.Getenv("SF_TOKEN"), "bearer token")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *path == "" {
		return errors.New("--file is required")
	}
	body, err := os.ReadFile(*path)
	if err != nil {
		return err
	}
	urlPath := "/api/v1/topology/events"
	if *reconcile {
		urlPath += "?reconcile=true"
	}
	return doRequest(http.MethodPost, *endpoint+urlPath, *token, body)
}

func faultCommand(args []string) error {
	if len(args) == 0 || args[0] != "device" {
		return errors.New("usage: sfctl fault device --device NAME --mode health|prepare|commit|state")
	}
	flags := flag.NewFlagSet("fault device", flag.ContinueOnError)
	device := flags.String("device", "", "device name")
	mode := flags.String("mode", "", "fault mode; empty clears")
	endpoint := flags.String("endpoint", envDefault("SF_ENDPOINT", "http://127.0.0.1:8080"), "controller URL")
	token := flags.String("token", os.Getenv("SF_TOKEN"), "bearer token")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if *device == "" {
		return errors.New("--device is required")
	}
	body, _ := json.Marshal(map[string]string{"device": *device, "mode": *mode})
	return doRequest(http.MethodPost, *endpoint+"/api/v1/faults/device", *token, body)
}

func requestCommand(args []string, method, path string, body []byte) error {
	flags := flag.NewFlagSet(strings.Trim(path, "/"), flag.ContinueOnError)
	endpoint := flags.String("endpoint", envDefault("SF_ENDPOINT", "http://127.0.0.1:8080"), "controller URL")
	token := flags.String("token", os.Getenv("SF_TOKEN"), "bearer token")
	if err := flags.Parse(args); err != nil {
		return err
	}
	return doRequest(method, *endpoint+path, *token, body)
}

func doRequest(method, url, token string, body []byte) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, method, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	if len(body) > 0 {
		request.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return err
	}
	var formatted bytes.Buffer
	if json.Indent(&formatted, data, "", "  ") == nil {
		fmt.Println(formatted.String())
	} else {
		fmt.Print(string(data))
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("controller returned %s", response.Status)
	}
	return nil
}

func writeJSONFile(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o640)
}

func writeHTMLFile(path string, value scenario.Report) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	if err := file.Chmod(0o640); err != nil {
		file.Close()
		return err
	}
	if err := report.HTML(file, value); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}

func envDefault(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
