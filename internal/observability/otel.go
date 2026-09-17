package observability

import (
	"context"
	"errors"
	"fmt"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

type TraceConfig struct {
	Endpoint    string
	Insecure    bool
	ServiceName string
	Version     string
}

// SetupTracing installs a batched OTLP/HTTP exporter. An empty endpoint keeps
// tracing disabled, which lets the control loop operate independently of the
// observability backend.
func SetupTracing(ctx context.Context, config TraceConfig) (func(context.Context) error, error) {
	if config.Endpoint == "" {
		return func(context.Context) error { return nil }, nil
	}
	if config.ServiceName == "" {
		config.ServiceName = "starfabric-controller"
	}
	opts := []otlptracehttp.Option{otlptracehttp.WithEndpoint(config.Endpoint)}
	if config.Insecure {
		opts = append(opts, otlptracehttp.WithInsecure())
	}
	exporter, err := otlptracehttp.New(ctx, opts...)
	if err != nil {
		return nil, fmt.Errorf("create OTLP trace exporter: %w", err)
	}
	res, err := resource.New(ctx, resource.WithAttributes(
		attribute.String("service.name", config.ServiceName),
		attribute.String("service.version", config.Version),
	))
	if err != nil {
		return nil, fmt.Errorf("create OpenTelemetry resource: %w", err)
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithBatcher(exporter), sdktrace.WithResource(res))
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))
	return func(shutdownCtx context.Context) error {
		if err := provider.Shutdown(shutdownCtx); err != nil && !errors.Is(err, context.Canceled) {
			return fmt.Errorf("shutdown OpenTelemetry: %w", err)
		}
		return nil
	}, nil
}
