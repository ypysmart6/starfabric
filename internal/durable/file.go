// Package durable contains the small crash-consistent file primitive shared by
// controller state stores and software device adapters.
package durable

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// WriteFile atomically replaces path and fsyncs both the file and its parent
// directory. The directory sync is required for the rename itself to survive a
// power loss on filesystems that otherwise only persist file contents.
func WriteFile(path string, data []byte, mode os.FileMode) error {
	if path == "" {
		return nil
	}
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, ".starfabric-*.tmp")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	fail := func(operationErr error) error {
		closeErr := temporary.Close()
		if closeErr != nil {
			return fmt.Errorf("%w; close temporary state: %v", operationErr, closeErr)
		}
		return operationErr
	}
	if _, err = temporary.Write(data); err != nil {
		return fail(err)
	}
	if err = temporary.Sync(); err != nil {
		return fail(err)
	}
	if err = temporary.Chmod(mode); err != nil {
		return fail(err)
	}
	if err = temporary.Close(); err != nil {
		return err
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	dir, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

// WriteJSON is WriteFile with deterministic indented JSON encoding.
func WriteJSON(path string, value any, mode os.FileMode) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return WriteFile(path, data, mode)
}
