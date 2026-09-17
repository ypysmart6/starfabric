package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/starfabric/starfabric/internal/durable"
	"github.com/starfabric/starfabric/internal/inventory"
	"github.com/starfabric/starfabric/internal/scenario"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "sf-inventory:", err)
		os.Exit(1)
	}
}
func run() error {
	base := flag.String("url", "", "NetBox or Nautobot base URL")
	tokenFile := flag.String("token-file", "", "API token file")
	tag := flag.String("tag", "starfabric", "inventory tag selector")
	output := flag.String("output", "", "scenario JSON output; stdout when empty")
	flag.Parse()
	if *base == "" {
		return errors.New("--url is required")
	}
	token := ""
	if *tokenFile != "" {
		data, err := os.ReadFile(*tokenFile)
		if err != nil {
			return err
		}
		token = strings.TrimSpace(string(data))
	}
	client, err := inventory.NewNetBoxClient(*base, token, *tag, nil)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	topology, err := client.Snapshot(ctx)
	if err != nil {
		return err
	}
	value := scenario.Scenario{ID: "netbox-import", Seed: 1, Topology: topology, Intents: nil, Timeline: nil}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if *output == "" {
		_, err = os.Stdout.Write(data)
		return err
	}
	return durable.WriteFile(*output, data, 0o640)
}
