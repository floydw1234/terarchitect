package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"agenthub/internal/db"
	"agenthub/internal/gitrepo"
)

func TestCommitReceiptIncludesMentionsAndMetadata(t *testing.T) {
	srv, database, authHeader, hashes := newTestServer(t)

	channelName := "ticket-123456789012345678901234"
	if err := database.CreateChannel(channelName, ""); err != nil {
		t.Fatalf("create channel: %v", err)
	}
	channel, err := database.GetChannelByName(channelName)
	if err != nil {
		t.Fatalf("get channel: %v", err)
	}
	if _, err := database.CreatePost(channel.ID, "agent-1", nil, fmt.Sprintf(`{"terarchitect_event":1,"type":"attempt_published","message":"Published %s","metadata":{"commit_hash":"%s"}}`, hashes.child, hashes.child)); err != nil {
		t.Fatalf("create structured post: %v", err)
	}
	if _, err := database.CreatePost(channel.ID, "agent-1", nil, "discussion mentions "+hashes.child); err != nil {
		t.Fatalf("create legacy post: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/git/receipts/"+hashes.child, nil)
	req.Header.Set("Authorization", authHeader)
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var receipt map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &receipt); err != nil {
		t.Fatalf("decode receipt: %v", err)
	}

	if receipt["exists"] != true {
		t.Fatalf("expected exists=true, got %#v", receipt["exists"])
	}
	if receipt["base"] != hashes.root {
		t.Fatalf("expected base %s, got %#v", hashes.root, receipt["base"])
	}
	if receipt["is_leaf"] != true {
		t.Fatalf("expected leaf receipt, got %#v", receipt["is_leaf"])
	}
	if receipt["summary"] != "child commit" {
		t.Fatalf("expected summary, got %#v", receipt["summary"])
	}
	if receipt["bundle_fetchable"] != true {
		t.Fatalf("expected bundle_fetchable=true, got %#v", receipt["bundle_fetchable"])
	}

	parents, ok := receipt["parents"].([]any)
	if !ok || len(parents) != 1 || parents[0] != hashes.root {
		t.Fatalf("unexpected parents %#v", receipt["parents"])
	}
	channels, ok := receipt["channels"].([]any)
	if !ok || len(channels) == 0 || channels[0] != channelName {
		t.Fatalf("unexpected channels %#v", receipt["channels"])
	}
	mentions, ok := receipt["mentions"].([]any)
	if !ok || len(mentions) != 2 {
		t.Fatalf("expected 2 mentions, got %#v", receipt["mentions"])
	}

	firstMention := mentions[0].(map[string]any)
	if firstMention["event_type"] != "attempt_published" {
		t.Fatalf("expected structured event type, got %#v", firstMention["event_type"])
	}
}

func TestCommitReceiptForMissingCommitReturnsExistsFalse(t *testing.T) {
	srv, _, authHeader, _ := newTestServer(t)
	missing := strings.Repeat("a", 40)

	req := httptest.NewRequest(http.MethodGet, "/api/git/receipts/"+missing, nil)
	req.Header.Set("Authorization", authHeader)
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var receipt map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &receipt); err != nil {
		t.Fatalf("decode receipt: %v", err)
	}
	if receipt["exists"] != false {
		t.Fatalf("expected exists=false, got %#v", receipt["exists"])
	}
}

func TestRecentEventsEndpointReturnsTypedEventsWithChannelFilter(t *testing.T) {
	srv, database, authHeader, hashes := newTestServer(t)

	ticketChannel := "ticket-123456789012345678901234"
	waveChannel := "wave-demo-1"
	if err := database.CreateChannel(ticketChannel, ""); err != nil {
		t.Fatalf("create ticket channel: %v", err)
	}
	if err := database.CreateChannel(waveChannel, ""); err != nil {
		t.Fatalf("create wave channel: %v", err)
	}
	ticket, _ := database.GetChannelByName(ticketChannel)
	wave, _ := database.GetChannelByName(waveChannel)
	if _, err := database.CreatePost(ticket.ID, "agent-1", nil, fmt.Sprintf(`{"terarchitect_event":1,"type":"attempt_published","message":"Published %s","metadata":{"commit_hash":"%s"}}`, hashes.child, hashes.child)); err != nil {
		t.Fatalf("create ticket post: %v", err)
	}
	if _, err := database.CreatePost(wave.ID, "agent-1", nil, "release_pr_opened: PR #12"); err != nil {
		t.Fatalf("create wave post: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/events?channel_prefix=wave-&limit=10", nil)
	req.Header.Set("Authorization", authHeader)
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var events []map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &events); err != nil {
		t.Fatalf("decode events: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	if events[0]["channel_name"] != waveChannel {
		t.Fatalf("unexpected channel %#v", events[0]["channel_name"])
	}
	if events[0]["event_type"] != "release_pr_opened" {
		t.Fatalf("unexpected event type %#v", events[0]["event_type"])
	}
}

func TestDoctorEndpointReportsHealthyChecks(t *testing.T) {
	srv, _, authHeader, _ := newTestServer(t)

	req := httptest.NewRequest(http.MethodGet, "/api/doctor", nil)
	req.Header.Set("Authorization", authHeader)
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode doctor payload: %v", err)
	}
	if payload["status"] != "ok" {
		t.Fatalf("expected ok status, got %#v", payload["status"])
	}
	checks, ok := payload["checks"].([]any)
	if !ok || len(checks) < 3 {
		t.Fatalf("expected checks, got %#v", payload["checks"])
	}
}

func TestSeedEndpointReturnsExplicitNotSupported(t *testing.T) {
	srv, _, authHeader, hashes := newTestServer(t)
	body := bytes.NewBufferString(fmt.Sprintf(`{"repo_path":"/tmp/source","commit_hash":"%s"}`, hashes.child))

	req := httptest.NewRequest(http.MethodPost, "/api/git/seed", body)
	req.Header.Set("Authorization", authHeader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotImplemented {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode seed payload: %v", err)
	}
	if !strings.Contains(fmt.Sprint(payload["error"]), "not yet supported") {
		t.Fatalf("unexpected error payload %#v", payload)
	}
}

func TestGitHubImportEndpointImportsLocalFixtureBundle(t *testing.T) {
	srv, database, authHeader, _ := newTestServer(t)
	remoteURL, hashes := newImportFixtureRepo(t)

	body := bytes.NewBufferString(fmt.Sprintf(`{"github_url":%q,"base_ref":"main"}`, remoteURL))
	req := httptest.NewRequest(http.MethodPost, "/api/git/import/github", body)
	req.Header.Set("Authorization", authHeader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode import payload: %v", err)
	}
	if payload["leaf_id"] != hashes.child {
		t.Fatalf("expected leaf_id %s, got %#v", hashes.child, payload["leaf_id"])
	}
	if payload["source"] != "github" {
		t.Fatalf("unexpected source %#v", payload["source"])
	}
	if payload["resolved_commit_sha"] != hashes.child {
		t.Fatalf("expected resolved sha %s, got %#v", hashes.child, payload["resolved_commit_sha"])
	}

	leaf, err := database.GetCommit(hashes.child)
	if err != nil {
		t.Fatalf("get imported leaf: %v", err)
	}
	if leaf == nil {
		t.Fatalf("expected imported leaf to be indexed")
	}
	if leaf.AgentID != "agent-1" {
		t.Fatalf("expected leaf agent-1, got %q", leaf.AgentID)
	}

	parent, err := database.GetCommit(hashes.root)
	if err != nil {
		t.Fatalf("get imported parent: %v", err)
	}
	if parent == nil {
		t.Fatalf("expected imported parent to be indexed")
	}
	if parent.AgentID != "" {
		t.Fatalf("expected parent agent to be empty, got %q", parent.AgentID)
	}
}

func TestGitHubImportEndpointRejectsInvalidURL(t *testing.T) {
	srv, _, authHeader, _ := newTestServer(t)

	body := bytes.NewBufferString(`{"repository_url":"https://example.com/not-github/repo","ref":"main"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/git/import/github", body)
	req.Header.Set("Authorization", authHeader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}
}

func TestGitHubImportEndpointRejectsBadRef(t *testing.T) {
	srv, _, authHeader, _ := newTestServer(t)
	remoteURL, _ := newImportFixtureRepo(t)

	body := bytes.NewBufferString(fmt.Sprintf(`{"repository_url":%q,"ref":"does-not-exist"}`, remoteURL))
	req := httptest.NewRequest(http.MethodPost, "/api/git/import/github", body)
	req.Header.Set("Authorization", authHeader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("unexpected status %d: %s", rec.Code, rec.Body.String())
	}

	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode error payload: %v", err)
	}
	if payload["error"] != "requested ref was not found" {
		t.Fatalf("unexpected payload %#v", payload)
	}
}

type testHashes struct {
	root  string
	child string
}

func newTestServer(t *testing.T) (*Server, *db.DB, string, testHashes) {
	t.Helper()

	rootDir := t.TempDir()
	database, err := db.Open(filepath.Join(rootDir, "agenthub.db"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	t.Cleanup(func() { _ = database.Close() })
	if err := database.Migrate(); err != nil {
		t.Fatalf("migrate db: %v", err)
	}
	if err := database.CreateAgent("agent-1", "test-key"); err != nil {
		t.Fatalf("create agent: %v", err)
	}

	repo, hashes := newPopulatedRepo(t, rootDir)
	if err := database.InsertCommit(hashes.root, "", "agent-1", "root commit"); err != nil {
		t.Fatalf("insert root commit: %v", err)
	}
	if err := database.InsertCommit(hashes.child, hashes.root, "agent-1", "child commit"); err != nil {
		t.Fatalf("insert child commit: %v", err)
	}

	srv := New(database, repo, "admin-key", Config{
		MaxBundleSize:    10 << 20,
		MaxPushesPerHour: 100,
		MaxPostsPerHour:  100,
	})
	return srv, database, "Bearer test-key", hashes
}

func newPopulatedRepo(t *testing.T, rootDir string) (*gitrepo.Repo, testHashes) {
	t.Helper()

	barePath := filepath.Join(rootDir, "repo.git")
	repo, err := gitrepo.Init(barePath)
	if err != nil {
		t.Fatalf("init bare repo: %v", err)
	}

	worktree := filepath.Join(rootDir, "worktree")
	if err := os.MkdirAll(worktree, 0o755); err != nil {
		t.Fatalf("mkdir worktree: %v", err)
	}
	runGit(t, worktree, "init")
	runGit(t, worktree, "config", "user.name", "Agent Test")
	runGit(t, worktree, "config", "user.email", "agent@example.com")
	if err := os.WriteFile(filepath.Join(worktree, "file.txt"), []byte("root\n"), 0o644); err != nil {
		t.Fatalf("write root file: %v", err)
	}
	runGit(t, worktree, "add", "file.txt")
	runGit(t, worktree, "commit", "-m", "root commit")
	rootHash := strings.TrimSpace(runGitOutput(t, worktree, "rev-parse", "HEAD"))

	if err := os.WriteFile(filepath.Join(worktree, "file.txt"), []byte("child\n"), 0o644); err != nil {
		t.Fatalf("write child file: %v", err)
	}
	runGit(t, worktree, "commit", "-am", "child commit")
	childHash := strings.TrimSpace(runGitOutput(t, worktree, "rev-parse", "HEAD"))

	runGit(t, worktree, "remote", "add", "origin", barePath)
	runGit(t, worktree, "push", "origin", "HEAD:refs/heads/main")

	return repo, testHashes{root: rootHash, child: childHash}
}

func newImportFixtureRepo(t *testing.T) (string, testHashes) {
	t.Helper()

	rootDir := t.TempDir()
	remotePath := filepath.Join(rootDir, "remote.git")
	runGit(t, rootDir, "init", "--bare", remotePath)

	worktree := filepath.Join(rootDir, "worktree")
	if err := os.MkdirAll(worktree, 0o755); err != nil {
		t.Fatalf("mkdir worktree: %v", err)
	}
	runGit(t, worktree, "init")
	runGit(t, worktree, "config", "user.name", "Import Test")
	runGit(t, worktree, "config", "user.email", "import@example.com")
	if err := os.WriteFile(filepath.Join(worktree, "fixture.txt"), []byte("root\n"), 0o644); err != nil {
		t.Fatalf("write root fixture: %v", err)
	}
	runGit(t, worktree, "add", "fixture.txt")
	runGit(t, worktree, "commit", "-m", "fixture root")
	rootHash := strings.TrimSpace(runGitOutput(t, worktree, "rev-parse", "HEAD"))

	if err := os.WriteFile(filepath.Join(worktree, "fixture.txt"), []byte("child\n"), 0o644); err != nil {
		t.Fatalf("write child fixture: %v", err)
	}
	runGit(t, worktree, "commit", "-am", "fixture child")
	childHash := strings.TrimSpace(runGitOutput(t, worktree, "rev-parse", "HEAD"))

	runGit(t, worktree, "remote", "add", "origin", remotePath)
	runGit(t, worktree, "push", "origin", "HEAD:refs/heads/main")

	return "file://" + remotePath, testHashes{root: rootHash, child: childHash}
}

func runGit(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %s failed: %v\n%s", strings.Join(args, " "), err, string(out))
	}
}

func runGitOutput(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %s failed: %v\n%s", strings.Join(args, " "), err, string(out))
	}
	return string(out)
}
