package server

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"agenthub/internal/auth"
)

type githubImportRequest struct {
	RepositoryURL string `json:"repository_url"`
	GitHubURL     string `json:"github_url"`
	Ref           string `json:"ref"`
	BaseRef       string `json:"base_ref"`
}

type githubImportResponse struct {
	LeafID            string `json:"leaf_id"`
	Source            string `json:"source"`
	RepositoryURL     string `json:"repository_url"`
	RequestedRef      string `json:"requested_ref"`
	ResolvedCommitSHA string `json:"resolved_commit_sha"`
}

type httpError struct {
	status  int
	message string
}

func (e *httpError) Error() string {
	return e.message
}

func (s *Server) handleGitImportGitHub(w http.ResponseWriter, r *http.Request) {
	agent := auth.AgentFromContext(r.Context())

	allowed, err := s.db.CheckRateLimit(agent.ID, "push", s.config.MaxPushesPerHour)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "rate limit check failed")
		return
	}
	if !allowed {
		writeError(w, http.StatusTooManyRequests, "push rate limit exceeded")
		return
	}

	var req githubImportRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json")
		return
	}

	repositoryURL := strings.TrimSpace(req.RepositoryURL)
	if repositoryURL == "" {
		repositoryURL = strings.TrimSpace(req.GitHubURL)
	}
	requestedRef := strings.TrimSpace(req.Ref)
	if requestedRef == "" {
		requestedRef = strings.TrimSpace(req.BaseRef)
	}

	if repositoryURL == "" {
		writeError(w, http.StatusBadRequest, "repository_url is required")
		return
	}
	if requestedRef == "" {
		writeError(w, http.StatusBadRequest, "ref is required")
		return
	}

	resolvedCommitSHA, bundlePath, err := prepareGitImportBundle(repositoryURL, requestedRef)
	if err != nil {
		var httpErr *httpError
		if errors.As(err, &httpErr) {
			writeError(w, httpErr.status, httpErr.message)
			return
		}
		writeError(w, http.StatusInternalServerError, "failed to import repository")
		return
	}
	defer os.Remove(bundlePath)

	if _, err := s.importBundle(bundlePath, agent.ID); err != nil {
		writeError(w, http.StatusBadRequest, "invalid bundle: "+err.Error())
		return
	}

	if err := s.db.IncrementRateLimit(agent.ID, "push"); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to update rate limit")
		return
	}

	writeJSON(w, http.StatusCreated, githubImportResponse{
		LeafID:            resolvedCommitSHA,
		Source:            "github",
		RepositoryURL:     repositoryURL,
		RequestedRef:      requestedRef,
		ResolvedCommitSHA: resolvedCommitSHA,
	})
}

func prepareGitImportBundle(repositoryURL, requestedRef string) (string, string, error) {
	if err := validateGitImportURL(repositoryURL); err != nil {
		return "", "", err
	}

	tmpDir, err := os.MkdirTemp("", "agenthub-github-import-*")
	if err != nil {
		return "", "", fmt.Errorf("create temp dir: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	repoDir := filepath.Join(tmpDir, "repo")
	if err := os.MkdirAll(repoDir, 0o755); err != nil {
		return "", "", fmt.Errorf("create repo dir: %w", err)
	}

	if _, err := runImportGit(repoDir, repositoryURL, "init"); err != nil {
		return "", "", err
	}
	if _, err := runImportGit(repoDir, repositoryURL, "remote", "add", "origin", repositoryURL); err != nil {
		return "", "", fmt.Errorf("configure remote: %w", err)
	}
	if _, err := runImportGit(repoDir, repositoryURL, "fetch", "--tags", "origin", requestedRef); err != nil {
		return "", "", classifyImportGitError(repositoryURL, err)
	}
	if _, err := runImportGit(repoDir, repositoryURL, "checkout", "--detach", "FETCH_HEAD"); err != nil {
		return "", "", classifyImportGitError(repositoryURL, err)
	}

	resolvedCommitSHA, err := runImportGit(repoDir, repositoryURL, "rev-parse", "HEAD")
	if err != nil {
		return "", "", fmt.Errorf("resolve commit sha: %w", err)
	}
	resolvedCommitSHA = strings.TrimSpace(resolvedCommitSHA)

	bundlePath := filepath.Join(tmpDir, "import.bundle")
	if _, err := runImportGit(repoDir, repositoryURL, "bundle", "create", bundlePath, "HEAD"); err != nil {
		return "", "", fmt.Errorf("create bundle: %w", err)
	}

	persistedBundle, err := os.CreateTemp("", "agenthub-import-*.bundle")
	if err != nil {
		return "", "", fmt.Errorf("create temp bundle: %w", err)
	}
	persistedPath := persistedBundle.Name()
	if err := persistedBundle.Close(); err != nil {
		_ = os.Remove(persistedPath)
		return "", "", fmt.Errorf("close temp bundle: %w", err)
	}
	data, err := os.ReadFile(bundlePath)
	if err != nil {
		_ = os.Remove(persistedPath)
		return "", "", fmt.Errorf("read bundle: %w", err)
	}
	if err := os.WriteFile(persistedPath, data, 0o600); err != nil {
		_ = os.Remove(persistedPath)
		return "", "", fmt.Errorf("persist bundle: %w", err)
	}

	return resolvedCommitSHA, persistedPath, nil
}

func validateGitImportURL(rawURL string) error {
	u, err := url.Parse(rawURL)
	if err != nil {
		return &httpError{status: http.StatusBadRequest, message: "invalid repository_url"}
	}
	switch u.Scheme {
	case "https", "http":
		if !strings.EqualFold(u.Hostname(), "github.com") {
			return &httpError{status: http.StatusBadRequest, message: "repository_url must be a github.com URL"}
		}
		if strings.Trim(u.Path, "/") == "" {
			return &httpError{status: http.StatusBadRequest, message: "repository_url must include owner and repo"}
		}
		return nil
	case "file":
		if u.Path == "" {
			return &httpError{status: http.StatusBadRequest, message: "invalid repository_url"}
		}
		return nil
	default:
		return &httpError{status: http.StatusBadRequest, message: "repository_url must use https://github.com/..."}
	}
}

func classifyImportGitError(repositoryURL string, err error) error {
	msg := strings.ToLower(err.Error())
	switch {
	case strings.Contains(msg, "couldn't find remote ref"),
		strings.Contains(msg, "not our ref"),
		strings.Contains(msg, "reference is not a tree"),
		strings.Contains(msg, "pathspec"):
		return &httpError{status: http.StatusBadRequest, message: "requested ref was not found"}
	case strings.Contains(msg, "authentication failed"),
		strings.Contains(msg, "could not read username"),
		strings.Contains(msg, "access denied"),
		strings.Contains(msg, "invalid username or password"),
		strings.Contains(msg, "403"):
		return &httpError{status: http.StatusUnauthorized, message: "unauthorized to access repository"}
	case strings.Contains(msg, "repository not found"),
		strings.Contains(msg, "not found"):
		if githubToken() != "" && isGitHubHTTPSURL(repositoryURL) {
			return &httpError{status: http.StatusUnauthorized, message: "unauthorized to access repository"}
		}
		return &httpError{status: http.StatusNotFound, message: "repository not found"}
	default:
		return err
	}
}

func runImportGit(dir, repositoryURL string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()

	cmdArgs := importGitConfigArgs(repositoryURL)
	cmdArgs = append(cmdArgs, args...)
	cmd := exec.CommandContext(ctx, "git", cmdArgs...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("%w: %s", err, strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

func importGitConfigArgs(repositoryURL string) []string {
	token := githubToken()
	if token == "" || !isGitHubHTTPSURL(repositoryURL) {
		return nil
	}
	auth := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	return []string{"-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic " + auth}
}

func githubToken() string {
	if token := strings.TrimSpace(os.Getenv("GITHUB_TOKEN")); token != "" {
		return token
	}
	return strings.TrimSpace(os.Getenv("GH_TOKEN"))
}

func isGitHubHTTPSURL(rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	return (u.Scheme == "https" || u.Scheme == "http") && strings.EqualFold(u.Hostname(), "github.com")
}
