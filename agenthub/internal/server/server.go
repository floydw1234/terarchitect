package server

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"

	"agenthub/internal/auth"
	"agenthub/internal/db"
	"agenthub/internal/gitrepo"
)

type Config struct {
	MaxBundleSize             int64  // max bundle upload size in bytes
	MaxPushesPerHour          int    // per agent
	MaxPostsPerHour           int    // per agent
	ListenAddr                string // e.g. ":8080"
	AllowUnauthenticatedReads bool   // dev-only read bypass for GET graph/board endpoints
}

type Server struct {
	db       *db.DB
	repo     *gitrepo.Repo
	adminKey string
	mux      *http.ServeMux
	handler  http.Handler
	config   Config
}

func New(database *db.DB, repo *gitrepo.Repo, adminKey string, cfg Config) *Server {
	s := &Server{
		db:       database,
		repo:     repo,
		adminKey: adminKey,
		mux:      http.NewServeMux(),
		config:   cfg,
	}
	s.setupRoutes()
	s.handler = s.withAPICORS(s.mux)
	return s
}

func (s *Server) setupRoutes() {
	authMw := auth.Middleware(s.db)
	readMw := authMw
	if s.config.AllowUnauthenticatedReads {
		readMw = auth.OptionalMiddleware(s.db)
	}
	adminMw := auth.AdminMiddleware(s.adminKey)

	// Git endpoints
	s.mux.Handle("POST /api/git/push", authMw(http.HandlerFunc(s.handleGitPush)))
	s.mux.Handle("POST /api/git/seed", authMw(http.HandlerFunc(s.handleGitSeed)))
	s.mux.Handle("POST /api/git/import/github", authMw(http.HandlerFunc(s.handleGitImportGitHub)))
	s.mux.Handle("GET /api/git/fetch/{hash}", authMw(http.HandlerFunc(s.handleGitFetch)))
	s.mux.Handle("GET /api/git/commits", readMw(http.HandlerFunc(s.handleListCommits)))
	s.mux.Handle("GET /api/git/commits/{hash}", readMw(http.HandlerFunc(s.handleGetCommit)))
	s.mux.Handle("GET /api/git/receipts/{hash}", readMw(http.HandlerFunc(s.handleGetCommitReceipt)))
	s.mux.Handle("GET /api/git/commits/{hash}/children", readMw(http.HandlerFunc(s.handleGetChildren)))
	s.mux.Handle("GET /api/git/commits/{hash}/lineage", readMw(http.HandlerFunc(s.handleGetLineage)))
	s.mux.Handle("GET /api/git/leaves", readMw(http.HandlerFunc(s.handleGetLeaves)))
	s.mux.Handle("GET /api/git/diff/{hash_a}/{hash_b}", authMw(http.HandlerFunc(s.handleDiff)))
	s.mux.Handle("GET /api/doctor", readMw(http.HandlerFunc(s.handleDoctor)))

	// Message board endpoints
	s.mux.Handle("GET /api/channels", readMw(http.HandlerFunc(s.handleListChannels)))
	s.mux.Handle("POST /api/channels", authMw(http.HandlerFunc(s.handleCreateChannel)))
	s.mux.Handle("GET /api/channels/{name}/posts", readMw(http.HandlerFunc(s.handleListPosts)))
	s.mux.Handle("GET /api/channels/{name}/events", readMw(http.HandlerFunc(s.handleListChannelEvents)))
	s.mux.Handle("POST /api/channels/{name}/posts", authMw(http.HandlerFunc(s.handleCreatePost)))
	s.mux.Handle("GET /api/events", readMw(http.HandlerFunc(s.handleListRecentEvents)))
	s.mux.Handle("GET /api/posts/{id}", readMw(http.HandlerFunc(s.handleGetPost)))
	s.mux.Handle("GET /api/posts/{id}/replies", readMw(http.HandlerFunc(s.handleGetReplies)))

	// Admin endpoints
	s.mux.Handle("POST /api/admin/agents", adminMw(http.HandlerFunc(s.handleCreateAgent)))

	// Public registration (no auth, rate-limited by IP)
	s.mux.HandleFunc("POST /api/register", s.handleRegister)

	// Health check (no auth)
	s.mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})

	// Dashboard (no auth, public read-only)
	s.mux.HandleFunc("GET /", s.handleDashboard)
}

func (s *Server) ListenAndServe() error {
	log.Printf("listening on %s", s.config.ListenAddr)
	return http.ListenAndServe(s.config.ListenAddr, s.handler)
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.handler.ServeHTTP(w, r)
}

func (s *Server) withAPICORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/") {
			next.ServeHTTP(w, r)
			return
		}

		origin := r.Header.Get("Origin")
		if origin != "" {
			// Reflect the browser origin for local UI development so localhost,
			// 127.0.0.1, and LAN-served pages can talk to AgentHub without a
			// separate allowlist. Auth still applies to non-preflight requests.
			headers := w.Header()
			headers.Set("Access-Control-Allow-Origin", origin)
			headers.Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
			headers.Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
			headers.Add("Vary", "Origin")
			headers.Add("Vary", "Access-Control-Request-Method")
			headers.Add("Vary", "Access-Control-Request-Headers")
		}

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		next.ServeHTTP(w, r)
	})
}

// JSON helpers

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func decodeJSON(r *http.Request, v any) error {
	defer r.Body.Close()
	// Limit request body to 64KB for JSON endpoints
	limited := io.LimitReader(r.Body, 64*1024)
	return json.NewDecoder(limited).Decode(v)
}
