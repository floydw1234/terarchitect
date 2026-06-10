package server

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"

	"agenthub/internal/auth"
	"agenthub/internal/db"
	"agenthub/internal/gitrepo"
)

func (s *Server) handleGitPush(w http.ResponseWriter, r *http.Request) {
	agent := auth.AgentFromContext(r.Context())

	// Rate limit check
	allowed, err := s.db.CheckRateLimit(agent.ID, "push", s.config.MaxPushesPerHour)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "rate limit check failed")
		return
	}
	if !allowed {
		writeError(w, http.StatusTooManyRequests, "push rate limit exceeded")
		return
	}

	// Read bundle with size limit
	r.Body = http.MaxBytesReader(w, r.Body, s.config.MaxBundleSize)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "bundle too large")
		return
	}

	// Write to temp file
	tmpFile, err := os.CreateTemp("", "arhub-push-*.bundle")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create temp file")
		return
	}
	defer os.Remove(tmpFile.Name())

	if _, err := tmpFile.Write(body); err != nil {
		tmpFile.Close()
		writeError(w, http.StatusInternalServerError, "failed to write bundle")
		return
	}
	tmpFile.Close()

	// Unbundle into bare repo
	hashes, err := s.repo.Unbundle(tmpFile.Name())
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid bundle: "+err.Error())
		return
	}

	// Index each new commit in the database
	var indexed []string
	for _, hash := range hashes {
		// Skip if already indexed
		existing, _ := s.db.GetCommit(hash)
		if existing != nil {
			indexed = append(indexed, hash)
			continue
		}

		parentHash, message, err := s.repo.GetCommitInfo(hash)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to read commit info")
			return
		}

		// Validate parent exists (unless root commit)
		if parentHash != "" && !s.repo.CommitExists(parentHash) {
			writeError(w, http.StatusBadRequest, "parent commit not found: "+parentHash)
			return
		}

		// Also index the parent if it's not in DB yet (e.g. seed repo commits)
		if parentHash != "" {
			if pc, _ := s.db.GetCommit(parentHash); pc == nil {
				pParent, pMsg, _ := s.repo.GetCommitInfo(parentHash)
				s.db.InsertCommit(parentHash, pParent, "", pMsg)
			}
		}

		if err := s.db.InsertCommit(hash, parentHash, agent.ID, message); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to index commit")
			return
		}
		indexed = append(indexed, hash)
	}

	// Increment rate limit
	s.db.IncrementRateLimit(agent.ID, "push")

	writeJSON(w, http.StatusCreated, map[string]any{
		"hashes": indexed,
	})
}

func (s *Server) handleGetCommitReceipt(w http.ResponseWriter, r *http.Request) {
	hash := r.PathValue("hash")
	if !gitrepo.IsValidHash(hash) {
		writeError(w, http.StatusBadRequest, "invalid hash")
		return
	}

	receipt := map[string]any{
		"hash":             hash,
		"exists":           false,
		"parents":          []string{},
		"channels":         []string{},
		"mentions":         []normalizedEvent{},
		"bundle_fetchable": false,
	}

	details, detailsErr := s.repo.GetCommitDetails(hash)
	commit, dbErr := s.db.GetCommit(hash)
	if dbErr != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	existsInRepo := s.repo.CommitExists(hash)
	if detailsErr == nil && details != nil {
		receipt["author_name"] = details.AuthorName
		receipt["author_email"] = details.AuthorEmail
		receipt["summary"] = details.Summary
		receipt["parents"] = details.Parents
		if len(details.Parents) > 0 {
			receipt["base"] = details.Parents[0]
		}
	}
	if commit != nil && receipt["summary"] == nil {
		receipt["summary"] = commit.Message
	}
	if commit != nil && receipt["base"] == nil && commit.ParentHash != "" {
		receipt["base"] = commit.ParentHash
		receipt["parents"] = []string{commit.ParentHash}
	}
	if commit != nil || existsInRepo {
		receipt["exists"] = true
	}

	hasChildren := false
	if commit != nil {
		childValue, err := s.db.HasChildren(hash)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "database error")
			return
		}
		hasChildren = childValue
	}
	receipt["is_leaf"] = receipt["exists"] == true && !hasChildren
	receipt["bundle_fetchable"] = existsInRepo

	mentions, err := s.db.FindPostsMentioningCommit(hash, 20)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	normalized := make([]normalizedEvent, 0, len(mentions))
	for _, post := range mentions {
		normalized = append(normalized, normalizePostEvent(post))
	}
	receipt["mentions"] = normalized
	receipt["channels"] = dedupeChannels(mentions)

	writeJSON(w, http.StatusOK, receipt)
}

func (s *Server) handleDoctor(w http.ResponseWriter, r *http.Request) {
	type doctorCheck struct {
		Name   string `json:"name"`
		OK     bool   `json:"ok"`
		Detail string `json:"detail"`
		Hint   string `json:"hint,omitempty"`
	}

	var checks []doctorCheck
	checks = append(checks, doctorCheck{
		Name:   "auth",
		OK:     true,
		Detail: "API key accepted by server.",
	})

	if _, err := s.db.GetStats(); err != nil {
		checks = append(checks, doctorCheck{Name: "database", OK: false, Detail: fmt.Sprintf("database check failed: %v", err), Hint: "Check the AgentHub data directory and SQLite file permissions."})
	} else {
		checks = append(checks, doctorCheck{Name: "database", OK: true, Detail: "SQLite database is reachable."})
	}

	repoCheck := doctorCheck{Name: "repo", OK: true, Detail: "Bare git repository is reachable."}
	commits, err := s.db.ListCommits("", 1, 0)
	if err != nil {
		repoCheck = doctorCheck{Name: "repo", OK: false, Detail: fmt.Sprintf("failed to inspect repo state: %v", err), Hint: "Check database connectivity and repository state."}
	} else if len(commits) > 0 {
		bundlePath, bundleErr := s.repo.CreateBundle(commits[0].Hash)
		if bundleErr != nil {
			repoCheck = doctorCheck{Name: "repo", OK: false, Detail: fmt.Sprintf("bundle smoke test failed for %s: %v", commits[0].Hash, bundleErr), Hint: "Verify the git repo exists and the referenced commits are present on disk."}
		} else {
			_ = os.Remove(bundlePath)
			repoCheck.Detail = "Bundle smoke test succeeded."
		}
	}
	checks = append(checks, repoCheck)

	status := "ok"
	for _, check := range checks {
		if !check.OK {
			status = "degraded"
			break
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status": status,
		"checks": checks,
	})
}

func (s *Server) handleGitSeed(w http.ResponseWriter, r *http.Request) {
	var req struct {
		RepoPath   string `json:"repo_path"`
		CommitHash string `json:"commit_hash"`
	}
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json")
		return
	}
	writeJSON(w, http.StatusNotImplemented, map[string]any{
		"error": "server-side seed is not yet supported for arbitrary repo paths",
		"hint":  "Run `ah push` from the source repository instead.",
	})
}

func (s *Server) handleGitFetch(w http.ResponseWriter, r *http.Request) {
	hash := r.PathValue("hash")
	if !gitrepo.IsValidHash(hash) {
		writeError(w, http.StatusBadRequest, "invalid hash")
		return
	}

	if !s.repo.CommitExists(hash) {
		writeError(w, http.StatusNotFound, "commit not found")
		return
	}

	bundlePath, err := s.repo.CreateBundle(hash)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create bundle")
		return
	}
	defer os.Remove(bundlePath)

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", "attachment; filename="+hash+".bundle")
	http.ServeFile(w, r, bundlePath)
}

func (s *Server) handleListCommits(w http.ResponseWriter, r *http.Request) {
	agentID := r.URL.Query().Get("agent")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))

	commits, err := s.db.ListCommits(agentID, limit, offset)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if commits == nil {
		commits = []db.Commit{}
	}
	writeJSON(w, http.StatusOK, commits)
}

func (s *Server) handleGetCommit(w http.ResponseWriter, r *http.Request) {
	hash := r.PathValue("hash")
	if !gitrepo.IsValidHash(hash) {
		writeError(w, http.StatusBadRequest, "invalid hash")
		return
	}

	commit, err := s.db.GetCommit(hash)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if commit == nil {
		writeError(w, http.StatusNotFound, "commit not found")
		return
	}
	writeJSON(w, http.StatusOK, commit)
}

func (s *Server) handleGetChildren(w http.ResponseWriter, r *http.Request) {
	hash := r.PathValue("hash")
	if !gitrepo.IsValidHash(hash) {
		writeError(w, http.StatusBadRequest, "invalid hash")
		return
	}

	children, err := s.db.GetChildren(hash)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if children == nil {
		children = []db.Commit{}
	}
	writeJSON(w, http.StatusOK, children)
}

func (s *Server) handleGetLineage(w http.ResponseWriter, r *http.Request) {
	hash := r.PathValue("hash")
	if !gitrepo.IsValidHash(hash) {
		writeError(w, http.StatusBadRequest, "invalid hash")
		return
	}

	lineage, err := s.db.GetLineage(hash)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if lineage == nil {
		lineage = []db.Commit{}
	}
	writeJSON(w, http.StatusOK, lineage)
}

func (s *Server) handleGetLeaves(w http.ResponseWriter, r *http.Request) {
	leaves, err := s.db.GetLeaves()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "database error")
		return
	}
	if leaves == nil {
		leaves = []db.Commit{}
	}
	writeJSON(w, http.StatusOK, leaves)
}

func (s *Server) handleDiff(w http.ResponseWriter, r *http.Request) {
	agent := auth.AgentFromContext(r.Context())
	// Rate limit diffs (CPU-expensive)
	allowed, _ := s.db.CheckRateLimit(agent.ID, "diff", 60)
	if !allowed {
		writeError(w, http.StatusTooManyRequests, "diff rate limit exceeded")
		return
	}

	hashA := r.PathValue("hash_a")
	hashB := r.PathValue("hash_b")
	if !gitrepo.IsValidHash(hashA) || !gitrepo.IsValidHash(hashB) {
		writeError(w, http.StatusBadRequest, "invalid hash")
		return
	}

	diff, err := s.repo.Diff(hashA, hashB)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "diff failed")
		return
	}

	s.db.IncrementRateLimit(agent.ID, "diff")
	w.Header().Set("Content-Type", "text/plain")
	w.Write([]byte(diff))
}
