package main

import (
	"bytes"
	"os"
	"reflect"
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

func TestBundleCreateArgsForHeadPrefersExplicitDagBaseEnvOverOriginRefs(t *testing.T) {
	t.Setenv("BASE_LEAF_ID", "leaf-explicit")
	t.Setenv("BASE_HASH", "hash-explicit")
	t.Setenv("AGENTHUB_ROOT_HASH", "root-explicit")

	var checked []string
	args := bundleCreateArgsForHead("/tmp/test.bundle", func(candidate string) bool {
		checked = append(checked, candidate)
		return candidate == "leaf-explicit" || candidate == "origin/main"
	})

	if got, want := checked, []string{"leaf-explicit"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("checked candidates = %v, want %v", got, want)
	}
	if got, want := args, []string{"bundle", "create", "/tmp/test.bundle", "HEAD", "^leaf-explicit"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("bundle args = %v, want %v", got, want)
	}
}

func TestBundleCreateArgsForHeadFallsBackThroughExplicitDagBasesBeforeOrigin(t *testing.T) {
	t.Setenv("BASE_LEAF_ID", "leaf-explicit")
	t.Setenv("BASE_HASH", "hash-explicit")
	t.Setenv("AGENTHUB_ROOT_HASH", "root-explicit")

	var checked []string
	args := bundleCreateArgsForHead("/tmp/test.bundle", func(candidate string) bool {
		checked = append(checked, candidate)
		return candidate == "root-explicit"
	})

	if got, want := checked, []string{"leaf-explicit", "hash-explicit", "root-explicit"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("checked candidates = %v, want %v", got, want)
	}
	if got, want := args, []string{"bundle", "create", "/tmp/test.bundle", "HEAD", "^root-explicit"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("bundle args = %v, want %v", got, want)
	}
}

func TestBundleCreateArgsForHeadUsesFullBundleWhenNoExplicitOrOriginBaseResolves(t *testing.T) {
	t.Setenv("BASE_LEAF_ID", "leaf-explicit")
	t.Setenv("BASE_HASH", "hash-explicit")
	t.Setenv("AGENTHUB_ROOT_HASH", "root-explicit")

	args := bundleCreateArgsForHead("/tmp/test.bundle", func(candidate string) bool {
		return false
	})

	if got, want := args, []string{"bundle", "create", "/tmp/test.bundle", "HEAD"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("bundle args = %v, want %v", got, want)
	}
}
