package openconfig

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"os"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

// Endpoint configures one OpenConfig gRPC endpoint. Insecure is deliberately
// explicit and is intended only for local emulators such as KNE/Lemming.
type Endpoint struct {
	Address    string
	Username   string
	Password   string
	CAFile     string
	CertFile   string
	KeyFile    string
	ServerName string
	Insecure   bool
}

func (e Endpoint) validate() error {
	if e.Address == "" {
		return errors.New("OpenConfig endpoint address is required")
	}
	if (e.CertFile == "") != (e.KeyFile == "") {
		return errors.New("OpenConfig client certificate and key must be supplied together")
	}
	if e.Insecure && (e.CAFile != "" || e.CertFile != "" || e.ServerName != "") {
		return errors.New("insecure transport cannot be combined with TLS settings")
	}
	return nil
}

func dial(endpoint Endpoint) (*grpc.ClientConn, error) {
	if err := endpoint.validate(); err != nil {
		return nil, err
	}
	var transport credentials.TransportCredentials
	if endpoint.Insecure {
		transport = insecure.NewCredentials()
	} else {
		tlsConfig := &tls.Config{MinVersion: tls.VersionTLS13, ServerName: endpoint.ServerName}
		if endpoint.CAFile != "" {
			pem, err := os.ReadFile(endpoint.CAFile)
			if err != nil {
				return nil, fmt.Errorf("read OpenConfig CA: %w", err)
			}
			pool, err := x509.SystemCertPool()
			if err != nil || pool == nil {
				pool = x509.NewCertPool()
			}
			if !pool.AppendCertsFromPEM(pem) {
				return nil, errors.New("OpenConfig CA file contains no valid certificate")
			}
			tlsConfig.RootCAs = pool
		}
		if endpoint.CertFile != "" {
			certificate, err := tls.LoadX509KeyPair(endpoint.CertFile, endpoint.KeyFile)
			if err != nil {
				return nil, fmt.Errorf("load OpenConfig client certificate: %w", err)
			}
			tlsConfig.Certificates = []tls.Certificate{certificate}
		}
		transport = credentials.NewTLS(tlsConfig)
	}
	return grpc.NewClient(endpoint.Address, grpc.WithTransportCredentials(transport))
}

func (e Endpoint) authenticated(ctx context.Context) context.Context {
	if e.Username == "" && e.Password == "" {
		return ctx
	}
	return metadata.AppendToOutgoingContext(ctx, "username", e.Username, "password", e.Password)
}
