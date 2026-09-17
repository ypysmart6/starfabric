// sf-quic-probe provides a one-connection QUIC/TLS 1.3 echo endpoint for the
// single-PC packet path gate. Its deterministic test CA is scoped to this lab.
package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/big"
	"os"
	"time"

	"github.com/quic-go/quic-go"
)

const (
	protocol   = "starfabric-quic-v1"
	serverName = "starfabric.test"
	maxPayload = 1 << 20
)

type result struct {
	Success  bool   `json:"success"`
	Mode     string `json:"mode"`
	Bytes    int    `json:"bytes"`
	Protocol string `json:"protocol"`
	TLS      string `json:"tls"`
}

func main() {
	mode := flag.String("mode", "client", "server or client")
	address := flag.String("address", "127.0.0.1:19003", "listen or destination address")
	message := flag.String("message", "starfabric-quic-packet-path", "client payload")
	flag.Parse()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	var report result
	var err error
	switch *mode {
	case "server":
		report, err = serve(ctx, *address)
	case "client":
		report, err = call(ctx, *address, []byte(*message))
	default:
		err = errors.New("mode must be server or client")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "sf-quic-probe:", err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(report); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func serve(ctx context.Context, address string) (result, error) {
	serverTLS, _, err := testTLS()
	if err != nil {
		return result{}, err
	}
	listener, err := quic.ListenAddr(address, serverTLS, &quic.Config{HandshakeIdleTimeout: 3 * time.Second})
	if err != nil {
		return result{}, err
	}
	defer listener.Close()
	connection, err := listener.Accept(ctx)
	if err != nil {
		return result{}, err
	}
	defer connection.CloseWithError(0, "complete")
	stream, err := connection.AcceptStream(ctx)
	if err != nil {
		return result{}, err
	}
	payload, err := io.ReadAll(io.LimitReader(stream, maxPayload+1))
	if err != nil {
		return result{}, err
	}
	if len(payload) > maxPayload {
		return result{}, errors.New("QUIC payload exceeds one MiB")
	}
	if _, err := stream.Write(append([]byte("ACK:"), payload...)); err != nil {
		return result{}, err
	}
	if err := stream.Close(); err != nil {
		return result{}, err
	}
	// Keep the connection alive briefly so the stream FIN and ACK payload are
	// transmitted before this single-shot server closes its QUIC connection.
	time.Sleep(200 * time.Millisecond)
	return result{Success: true, Mode: "server", Bytes: len(payload), Protocol: protocol, TLS: "1.3"}, nil
}

func call(ctx context.Context, address string, payload []byte) (result, error) {
	_, clientTLS, err := testTLS()
	if err != nil {
		return result{}, err
	}
	connection, err := quic.DialAddr(ctx, address, clientTLS, &quic.Config{HandshakeIdleTimeout: 3 * time.Second})
	if err != nil {
		return result{}, err
	}
	defer connection.CloseWithError(0, "complete")
	stream, err := connection.OpenStreamSync(ctx)
	if err != nil {
		return result{}, err
	}
	if _, err := stream.Write(payload); err != nil {
		return result{}, err
	}
	if err := stream.Close(); err != nil {
		return result{}, err
	}
	reply := make([]byte, len(payload)+4)
	if _, err := io.ReadFull(stream, reply); err != nil {
		return result{}, err
	}
	if string(reply) != "ACK:"+string(payload) {
		return result{}, fmt.Errorf("unexpected QUIC reply %q", reply)
	}
	return result{Success: true, Mode: "client", Bytes: len(payload), Protocol: protocol, TLS: "1.3"}, nil
}

func testTLS() (*tls.Config, *tls.Config, error) {
	seed := sha256.Sum256([]byte("StarFabric deterministic QUIC lab certificate v1"))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: serverName},
		DNSNames:     []string{serverName},
		NotBefore:    time.Unix(0, 0), NotAfter: time.Unix(4102444800, 0),
		KeyUsage:              x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, privateKey.Public(), privateKey)
	if err != nil {
		return nil, nil, err
	}
	certificate, err := x509.ParseCertificate(certificateDER)
	if err != nil {
		return nil, nil, err
	}
	pool := x509.NewCertPool()
	pool.AddCert(certificate)
	pair := tls.Certificate{Certificate: [][]byte{certificateDER}, PrivateKey: privateKey, Leaf: certificate}
	server := &tls.Config{Certificates: []tls.Certificate{pair}, MinVersion: tls.VersionTLS13, NextProtos: []string{protocol}}
	client := &tls.Config{RootCAs: pool, ServerName: serverName, MinVersion: tls.VersionTLS13, NextProtos: []string{protocol}}
	return server, client, nil
}
