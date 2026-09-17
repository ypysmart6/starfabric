package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/starfabric/starfabric/internal/constellation"
)

func constellationCommand(args []string) error {
	if len(args) == 0 || args[0] != "generate" {
		return errors.New("usage: sfctl constellation generate [--config FILE] [--satellites N --gateways N --planes N --flows N] --output FILE [--summary FILE]")
	}
	defaults := constellation.Defaults()
	flags := flag.NewFlagSet("constellation generate", flag.ContinueOnError)
	configFile := flags.String("config", "", "JSON generator configuration; explicit flags override it")
	output := flags.String("output", "", "generated scenario JSON (required)")
	summaryFile := flags.String("summary", "", "optional topology counts and evidence-boundary JSON")
	satellites := flags.Int("satellites", defaults.Satellites, "number of software satellite nodes")
	gateways := flags.Int("gateways", defaults.Gateways, "number of gateway nodes")
	planes := flags.Int("planes", 0, "logical planes; must divide satellites; 0 selects automatically")
	uplinks := flags.Int("gateway-uplinks", defaults.GatewayUplinks, "distinct satellite attachments per gateway (at least 3)")
	flows := flags.Int("flows", 0, "directed synthetic traffic intents; 0 means twice gateways")
	capacity := flags.Int64("capacity-bps", defaults.CapacityBPS, "synthetic capacity of each directed link")
	demand := flags.Int64("demand-bps", defaults.DemandBPS, "reserved bandwidth per intent")
	islLatency := flags.Int64("isl-latency-us", defaults.ISLLatencyUS, "synthetic inter-satellite link latency")
	feederLatency := flags.Int64("feeder-latency-us", defaults.FeederLatencyUS, "synthetic gateway link latency")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if *output == "" || flags.NArg() != 0 {
		return errors.New("--output is required and positional arguments are not supported")
	}
	paths := make(map[string]bool)
	for _, path := range []string{*configFile, *output, *summaryFile} {
		if path == "" {
			continue
		}
		absolute, err := filepath.Abs(path)
		if err != nil {
			return err
		}
		if paths[absolute] {
			return errors.New("config, output and summary must use different files")
		}
		paths[absolute] = true
	}
	config := defaults
	if *configFile != "" {
		data, err := os.ReadFile(*configFile)
		if err != nil {
			return err
		}
		if bytes.Equal(bytes.TrimSpace(data), []byte("null")) {
			return errors.New("generator config must be a JSON object")
		}
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&config); err != nil {
			return fmt.Errorf("generator config: %w", err)
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			return errors.New("generator config must contain exactly one JSON object")
		}
	}
	flags.Visit(func(f *flag.Flag) {
		switch f.Name {
		case "satellites":
			config.Satellites = *satellites
		case "gateways":
			config.Gateways = *gateways
		case "planes":
			config.Planes = *planes
		case "gateway-uplinks":
			config.GatewayUplinks = *uplinks
		case "flows":
			config.Flows = *flows
		case "capacity-bps":
			config.CapacityBPS = *capacity
		case "demand-bps":
			config.DemandBPS = *demand
		case "isl-latency-us":
			config.ISLLatencyUS = *islLatency
		case "feeder-latency-us":
			config.FeederLatencyUS = *feederLatency
		}
	})
	s, summary, err := constellation.Generate(config)
	if err != nil {
		return err
	}
	if err := writeJSONFile(*output, s); err != nil {
		return err
	}
	if *summaryFile != "" {
		if err := writeJSONFile(*summaryFile, summary); err != nil {
			return err
		}
	}
	fmt.Printf("GENERATED: satellites=%d gateways=%d nodes=%d planes=%d slots=%d directed_links=%d intents=%d steps=%d\n",
		summary.Config.Satellites, summary.Config.Gateways, summary.TotalNodes, summary.Config.Planes,
		summary.SatellitesPerPlane, summary.DirectedLinks, summary.Config.Flows, summary.TimelineSteps)
	fmt.Printf("SCOPE: synthetic topology and planner preflight; live FRR/5G integration is not asserted. Scenario: %s\n", *output)
	return nil
}
