package main

import (
	"bytes"
	"os"
	"strings"
	"testing"
)

func TestPrintUsageIncludesReceiptDoctorAndSeed(t *testing.T) {
	origStdout := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w
	defer func() {
		os.Stdout = origStdout
	}()

	printUsage()
	_ = w.Close()

	var buf bytes.Buffer
	if _, err := buf.ReadFrom(r); err != nil {
		t.Fatalf("read usage: %v", err)
	}
	out := buf.String()
	for _, expected := range []string{"receipt <hash>", "doctor", "seed --repo <path> --commit <hash>"} {
		if !strings.Contains(out, expected) {
			t.Fatalf("usage missing %q in output:\n%s", expected, out)
		}
	}
}
