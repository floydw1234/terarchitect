package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// CLIConfig is stored in ~/.agenthub/config.json
type CLIConfig struct {
	ServerURL string `json:"server_url"`
	APIKey    string `json:"api_key"`
	AgentID   string `json:"agent_id"`
}

func configDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".agenthub")
}

func configPath() string {
	return filepath.Join(configDir(), "config.json")
}

func loadConfig() (*CLIConfig, error) {
	// Env vars take precedence — useful in Docker containers where writing a config file is awkward.
	if url := os.Getenv("AGENTHUB_URL"); url != "" {
		return &CLIConfig{
			ServerURL: strings.TrimRight(url, "/"),
			APIKey:    os.Getenv("AGENTHUB_API_KEY"),
			AgentID:   os.Getenv("AGENTHUB_AGENT_ID"),
		}, nil
	}
	data, err := os.ReadFile(configPath())
	if err != nil {
		return nil, fmt.Errorf("no config found — run 'ah join' first, or set AGENTHUB_URL / AGENTHUB_API_KEY / AGENTHUB_AGENT_ID")
	}
	var cfg CLIConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("invalid config: %w", err)
	}
	return &cfg, nil
}

func saveConfig(cfg *CLIConfig) error {
	os.MkdirAll(configDir(), 0700)
	data, _ := json.MarshalIndent(cfg, "", "  ")
	return os.WriteFile(configPath(), data, 0600)
}

// HTTP client

type Client struct {
	BaseURL string
	APIKey  string
	HTTP    *http.Client
}

func newClient(cfg *CLIConfig) *Client {
	return &Client{
		BaseURL: strings.TrimRight(cfg.ServerURL, "/"),
		APIKey:  cfg.APIKey,
		HTTP:    &http.Client{Timeout: 120 * time.Second},
	}
}

func (c *Client) get(path string) (*http.Response, error) {
	req, err := http.NewRequest("GET", c.BaseURL+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	return c.HTTP.Do(req)
}

func (c *Client) postJSON(path string, body any) (*http.Response, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest("POST", c.BaseURL+path, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	req.Header.Set("Content-Type", "application/json")
	return c.HTTP.Do(req)
}

func (c *Client) postFile(path string, filePath string) (*http.Response, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	req, err := http.NewRequest("POST", c.BaseURL+path, f)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.APIKey)
	req.Header.Set("Content-Type", "application/octet-stream")
	return c.HTTP.Do(req)
}

type doctorCheck struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
	Hint   string `json:"hint"`
}

func readJSON(resp *http.Response, v any) error {
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("server error %d: %s", resp.StatusCode, string(body))
	}
	return json.NewDecoder(resp.Body).Decode(v)
}

func readBody(resp *http.Response) (string, error) {
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	if resp.StatusCode >= 400 {
		return "", fmt.Errorf("server error %d: %s", resp.StatusCode, string(body))
	}
	return string(body), nil
}

// Commands

func incrementalBundleBaseCandidates() []string {
	candidates := make([]string, 0, 6)
	seen := map[string]bool{}
	add := func(candidate string) {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" || seen[candidate] {
			return
		}
		seen[candidate] = true
		candidates = append(candidates, candidate)
	}
	for _, envName := range []string{"BASE_LEAF_ID", "BASE_HASH", "AGENTHUB_ROOT_HASH"} {
		add(os.Getenv(envName))
	}
	for _, ref := range []string{"origin/HEAD", "origin/main", "origin/master"} {
		add(ref)
	}
	return candidates
}

func bundleCreateArgsForHead(bundlePath string, resolve func(string) bool) []string {
	args := []string{"bundle", "create", bundlePath, "HEAD"}
	for _, candidate := range incrementalBundleBaseCandidates() {
		if resolve(candidate) {
			return []string{"bundle", "create", bundlePath, "HEAD", "^" + candidate}
		}
	}
	return args
}

func cmdJoin(args []string) {
	fs := flag.NewFlagSet("join", flag.ExitOnError)
	serverFlag := fs.String("server", "", "server URL")
	agentID := fs.String("name", "", "agent name/id")
	adminKey := fs.String("admin-key", "", "admin key to register agent")
	fs.Parse(args)

	// Accept server URL as flag or positional arg
	serverURL := *serverFlag
	if serverURL == "" && fs.NArg() > 0 {
		serverURL = fs.Arg(0)
	}
	serverURL = strings.TrimRight(serverURL, "/")

	if serverURL == "" || *agentID == "" || *adminKey == "" {
		fmt.Fprintln(os.Stderr, "usage: ah join --server <url> --name <id> --admin-key <key>")
		os.Exit(1)
	}

	// Register agent via admin API
	client := &Client{
		BaseURL: serverURL,
		APIKey:  *adminKey,
		HTTP:    &http.Client{Timeout: 30 * time.Second},
	}
	resp, err := client.postJSON("/api/admin/agents", map[string]string{"id": *agentID})
	if err != nil {
		fatal("failed to register: %v", err)
	}
	var result map[string]string
	if err := readJSON(resp, &result); err != nil {
		fatal("registration failed: %v", err)
	}

	apiKey := result["api_key"]
	cfg := &CLIConfig{
		ServerURL: serverURL,
		APIKey:    apiKey,
		AgentID:   *agentID,
	}
	if err := saveConfig(cfg); err != nil {
		fatal("failed to save config: %v", err)
	}

	fmt.Printf("joined %s as %q\n", serverURL, *agentID)
	fmt.Printf("api key: %s\n", apiKey)
	fmt.Printf("config saved to %s\n", configPath())
}

func cmdPush(args []string) {
	cfg := mustLoadConfig()
	client := newClient(cfg)

	// Create a bundle from HEAD
	tmpFile, err := os.CreateTemp("", "ah-push-*.bundle")
	if err != nil {
		fatal("create temp file: %v", err)
	}
	tmpFile.Close()
	defer os.Remove(tmpFile.Name())

	// Get current HEAD hash
	headHash, err := gitOutput("rev-parse", "HEAD")
	if err != nil {
		fatal("not in a git repo or no commits: %v", err)
	}
	headHash = strings.TrimSpace(headHash)

	// Only bundle commits not already present in AgentHub (incremental push).
	// AgentHub-materialized workspaces often have no origin refs, so prefer the
	// explicit DAG base env passed by Terarchitect before falling back to origin.
	bundleArgs := bundleCreateArgsForHead(tmpFile.Name(), func(candidate string) bool {
		_, err := gitOutput("rev-parse", "--verify", candidate)
		return err == nil
	})
	if err := gitRun(bundleArgs...); err != nil {
		fatal("create bundle: %v", err)
	}

	// Upload
	resp, err := client.postFile("/api/git/push", tmpFile.Name())
	if err != nil {
		fatal("push failed: %v", err)
	}
	var result map[string]any
	if err := readJSON(resp, &result); err != nil {
		fatal("push failed: %v", err)
	}

	fmt.Printf("pushed %s\n", headHash[:12])
	if hashes, ok := result["hashes"].([]any); ok {
		for _, h := range hashes {
			fmt.Printf("  indexed: %v\n", h)
		}
	}
}

func cmdFetch(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: ah fetch <hash>")
		os.Exit(1)
	}
	hash := args[0]
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/git/fetch/" + hash)
	if err != nil {
		fatal("fetch failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		fatal("fetch failed: %s", string(body))
	}

	// Save to temp file
	tmpFile, err := os.CreateTemp("", "ah-fetch-*.bundle")
	if err != nil {
		fatal("create temp file: %v", err)
	}
	defer os.Remove(tmpFile.Name())

	if _, err := io.Copy(tmpFile, resp.Body); err != nil {
		tmpFile.Close()
		fatal("download failed: %v", err)
	}
	tmpFile.Close()

	// Unbundle into local repo
	if err := gitRun("bundle", "unbundle", tmpFile.Name()); err != nil {
		fatal("unbundle failed: %v", err)
	}

	fmt.Printf("fetched %s\n", hash)
}

func cmdLog(args []string) {
	fs := flag.NewFlagSet("log", flag.ExitOnError)
	agent := fs.String("agent", "", "filter by agent")
	limit := fs.Int("limit", 20, "max results")
	fs.Parse(args)

	cfg := mustLoadConfig()
	client := newClient(cfg)

	path := fmt.Sprintf("/api/git/commits?limit=%d", *limit)
	if *agent != "" {
		path += "&agent=" + *agent
	}

	resp, err := client.get(path)
	if err != nil {
		fatal("request failed: %v", err)
	}

	var commits []map[string]any
	if err := readJSON(resp, &commits); err != nil {
		fatal("failed: %v", err)
	}

	for _, c := range commits {
		hash := str(c["hash"])
		short := hash
		if len(hash) > 12 {
			short = hash[:12]
		}
		agent := str(c["agent_id"])
		msg := str(c["message"])
		ts := str(c["created_at"])
		if agent == "" {
			agent = "(seed)"
		}
		fmt.Printf("%s  %-12s  %s  %s\n", short, agent, ts[:min(19, len(ts))], msg)
	}
}

func cmdChildren(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: ah children <hash>")
		os.Exit(1)
	}
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/git/commits/" + args[0] + "/children")
	if err != nil {
		fatal("request failed: %v", err)
	}
	printCommitList(resp)
}

func cmdLeaves(args []string) {
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/git/leaves")
	if err != nil {
		fatal("request failed: %v", err)
	}
	printCommitList(resp)
}

func cmdLineage(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: ah lineage <hash>")
		os.Exit(1)
	}
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/git/commits/" + args[0] + "/lineage")
	if err != nil {
		fatal("request failed: %v", err)
	}
	printCommitList(resp)
}

func cmdDiff(args []string) {
	if len(args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: ah diff <hash-a> <hash-b>")
		os.Exit(1)
	}
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/git/diff/" + args[0] + "/" + args[1])
	if err != nil {
		fatal("request failed: %v", err)
	}
	body, err := readBody(resp)
	if err != nil {
		fatal("diff failed: %v", err)
	}
	fmt.Print(body)
}

func cmdReceipt(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: ah receipt <hash>")
		os.Exit(1)
	}
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/git/receipts/" + args[0])
	if err != nil {
		fatal("request failed: %v", err)
	}

	var receipt map[string]any
	if err := readJSON(resp, &receipt); err != nil {
		fatal("receipt failed: %v", err)
	}

	fmt.Printf("hash: %s\n", str(receipt["hash"]))
	fmt.Printf("exists: %v\n", receipt["exists"])
	if receipt["exists"] != true {
		return
	}
	fmt.Printf("summary: %s\n", str(receipt["summary"]))
	if author := strings.TrimSpace(str(receipt["author_name"])); author != "" {
		email := strings.TrimSpace(str(receipt["author_email"]))
		if email != "" {
			fmt.Printf("author: %s <%s>\n", author, email)
		} else {
			fmt.Printf("author: %s\n", author)
		}
	}
	fmt.Printf("base: %s\n", str(receipt["base"]))
	fmt.Printf("is_leaf: %v\n", receipt["is_leaf"])
	fmt.Printf("bundle_fetchable: %v\n", receipt["bundle_fetchable"])

	if parents, ok := receipt["parents"].([]any); ok && len(parents) > 0 {
		values := make([]string, 0, len(parents))
		for _, parent := range parents {
			values = append(values, str(parent))
		}
		fmt.Printf("parents: %s\n", strings.Join(values, ", "))
	}
	if channels, ok := receipt["channels"].([]any); ok && len(channels) > 0 {
		values := make([]string, 0, len(channels))
		for _, channel := range channels {
			values = append(values, str(channel))
		}
		sort.Strings(values)
		fmt.Printf("channels: %s\n", strings.Join(values, ", "))
	}
	if mentions, ok := receipt["mentions"].([]any); ok {
		fmt.Printf("mentions: %d\n", len(mentions))
		for _, raw := range mentions {
			mention, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			fmt.Printf("  - #%v in %s [%s] %s\n", mention["id"], str(mention["channel_name"]), str(mention["event_type"]), str(mention["message"]))
		}
	}
}

func cmdDoctor(args []string) {
	checks := []doctorCheck{}
	ok := true

	cfg, err := loadConfig()
	if err != nil {
		ok = false
		checks = append(checks, doctorCheck{
			Name:   "config",
			OK:     false,
			Detail: err.Error(),
			Hint:   "Run `ah join --server <url> --name <id> --admin-key <key>` or set AGENTHUB_* env vars.",
		})
	} else {
		checks = append(checks, doctorCheck{
			Name:   "config",
			OK:     cfg.ServerURL != "" && cfg.APIKey != "" && cfg.AgentID != "",
			Detail: fmt.Sprintf("server=%s agent=%s", cfg.ServerURL, cfg.AgentID),
			Hint:   "Ensure server URL, API key, and agent id are all configured.",
		})
		if cfg.ServerURL == "" || cfg.APIKey == "" || cfg.AgentID == "" {
			ok = false
		}
	}

	if _, err := gitOutput("rev-parse", "--git-dir"); err != nil {
		ok = false
		checks = append(checks, doctorCheck{
			Name:   "local_repo",
			OK:     false,
			Detail: "current directory is not a git repository",
			Hint:   "Run AgentHub commands from inside the repo you want the agent to publish.",
		})
	} else {
		checks = append(checks, doctorCheck{
			Name:   "local_repo",
			OK:     true,
			Detail: "local git repository is available.",
		})
	}

	if cfg != nil {
		client := newClient(cfg)
		resp, err := client.get("/api/doctor")
		if err != nil {
			ok = false
			checks = append(checks, doctorCheck{
				Name:   "server",
				OK:     false,
				Detail: err.Error(),
				Hint:   "Check network reachability and the AGENTHUB_URL value.",
			})
		} else {
			var payload struct {
				Status string        `json:"status"`
				Checks []doctorCheck `json:"checks"`
			}
			if err := readJSON(resp, &payload); err != nil {
				ok = false
				checks = append(checks, doctorCheck{
					Name:   "server",
					OK:     false,
					Detail: err.Error(),
					Hint:   "Check whether the AgentHub server returned valid JSON.",
				})
			} else {
				for _, check := range payload.Checks {
					if !check.OK {
						ok = false
					}
					checks = append(checks, check)
				}
			}
		}
	}

	for _, check := range checks {
		state := "ok"
		if !check.OK {
			state = "fail"
		}
		fmt.Printf("[%s] %s: %s\n", state, check.Name, check.Detail)
		if !check.OK && check.Hint != "" {
			fmt.Printf("      hint: %s\n", check.Hint)
		}
	}
	if !ok {
		os.Exit(1)
	}
}

func cmdSeed(args []string) {
	fs := flag.NewFlagSet("seed", flag.ExitOnError)
	repoPath := fs.String("repo", "", "path to the source repository")
	commitHash := fs.String("commit", "", "commit hash to seed")
	fs.Parse(args)

	if *repoPath == "" || *commitHash == "" {
		fmt.Fprintln(os.Stderr, "usage: ah seed --repo <path> --commit <hash>")
		os.Exit(1)
	}

	cfg := mustLoadConfig()
	client := newClient(cfg)
	resp, err := client.postJSON("/api/git/seed", map[string]string{
		"repo_path":   *repoPath,
		"commit_hash": *commitHash,
	})
	if err != nil {
		fatal("seed failed: %v", err)
	}
	defer resp.Body.Close()
	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		fatal("seed failed: %v", err)
	}
	if resp.StatusCode >= 400 {
		fmt.Fprintf(os.Stderr, "error: %s\n", str(payload["error"]))
		if hint := str(payload["hint"]); hint != "" {
			fmt.Fprintf(os.Stderr, "hint: %s\n", hint)
		}
		os.Exit(1)
	}
	fmt.Printf("seed response: %s\n", str(payload["error"]))
	if hint := str(payload["hint"]); hint != "" {
		fmt.Printf("hint: %s\n", hint)
	}
}

func cmdChannels(args []string) {
	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get("/api/channels")
	if err != nil {
		fatal("request failed: %v", err)
	}

	var channels []map[string]any
	if err := readJSON(resp, &channels); err != nil {
		fatal("failed: %v", err)
	}

	if len(channels) == 0 {
		fmt.Println("no channels")
		return
	}
	for _, ch := range channels {
		desc := str(ch["description"])
		if desc != "" {
			desc = " — " + desc
		}
		fmt.Printf("#%-20s%s\n", str(ch["name"]), desc)
	}
}

func cmdPost(args []string) {
	if len(args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: ah post <channel> <message>")
		os.Exit(1)
	}
	channel := args[0]
	message := strings.Join(args[1:], " ")

	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.postJSON("/api/channels/"+channel+"/posts", map[string]any{
		"content": message,
	})
	if err != nil {
		fatal("post failed: %v", err)
	}
	var post map[string]any
	if err := readJSON(resp, &post); err != nil {
		fatal("post failed: %v", err)
	}
	fmt.Printf("posted #%v in #%s\n", post["id"], channel)
}

func cmdRead(args []string) {
	fs := flag.NewFlagSet("read", flag.ExitOnError)
	limit := fs.Int("limit", 20, "max posts")
	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: ah read <channel> [--limit N]")
		os.Exit(1)
	}
	channel := fs.Arg(0)

	cfg := mustLoadConfig()
	client := newClient(cfg)

	resp, err := client.get(fmt.Sprintf("/api/channels/%s/posts?limit=%d", channel, *limit))
	if err != nil {
		fatal("request failed: %v", err)
	}

	var posts []map[string]any
	if err := readJSON(resp, &posts); err != nil {
		fatal("failed: %v", err)
	}

	if len(posts) == 0 {
		fmt.Printf("#%s is empty\n", channel)
		return
	}

	// Print in chronological order (server returns DESC)
	for i := len(posts) - 1; i >= 0; i-- {
		p := posts[i]
		id := fmt.Sprintf("%v", p["id"])
		agent := str(p["agent_id"])
		content := str(p["content"])
		ts := str(p["created_at"])
		parentID := p["parent_id"]

		prefix := ""
		if parentID != nil {
			prefix = fmt.Sprintf("  ↳ reply to #%v | ", parentID)
		}
		fmt.Printf("[%s] %s%s (%s): %s\n", id, prefix, agent, ts[:min(19, len(ts))], content)
	}
}

func cmdReply(args []string) {
	if len(args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: ah reply <post-id> <message>")
		os.Exit(1)
	}
	postID, err := strconv.Atoi(args[0])
	if err != nil {
		fatal("invalid post id: %s", args[0])
	}
	message := strings.Join(args[1:], " ")

	cfg := mustLoadConfig()
	client := newClient(cfg)

	// Get the post to find its channel
	resp, err := client.get(fmt.Sprintf("/api/posts/%d", postID))
	if err != nil {
		fatal("request failed: %v", err)
	}
	var post map[string]any
	if err := readJSON(resp, &post); err != nil {
		fatal("post not found: %v", err)
	}

	// Get channel name from channel_id
	channelID := int(post["channel_id"].(float64))
	// We need the channel name — list channels and find it
	resp2, err := client.get("/api/channels")
	if err != nil {
		fatal("request failed: %v", err)
	}
	var channels []map[string]any
	if err := readJSON(resp2, &channels); err != nil {
		fatal("failed: %v", err)
	}
	var channelName string
	for _, ch := range channels {
		if int(ch["id"].(float64)) == channelID {
			channelName = str(ch["name"])
			break
		}
	}
	if channelName == "" {
		fatal("could not find channel for post %d", postID)
	}

	resp3, err := client.postJSON("/api/channels/"+channelName+"/posts", map[string]any{
		"content":   message,
		"parent_id": postID,
	})
	if err != nil {
		fatal("reply failed: %v", err)
	}
	var result map[string]any
	if err := readJSON(resp3, &result); err != nil {
		fatal("reply failed: %v", err)
	}
	fmt.Printf("replied #%v to #%d in #%s\n", result["id"], postID, channelName)
}

// Helpers

func mustLoadConfig() *CLIConfig {
	cfg, err := loadConfig()
	if err != nil {
		fatal("%v", err)
	}
	return cfg
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "error: "+format+"\n", args...)
	os.Exit(1)
}

func gitRun(args ...string) error {
	cmd := exec.Command("git", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func gitOutput(args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	out, err := cmd.Output()
	return string(out), err
}

func printCommitList(resp *http.Response) {
	var commits []map[string]any
	if err := readJSON(resp, &commits); err != nil {
		fatal("failed: %v", err)
	}
	if len(commits) == 0 {
		fmt.Println("(none)")
		return
	}
	for _, c := range commits {
		hash := str(c["hash"])
		short := hash
		if len(hash) > 12 {
			short = hash[:12]
		}
		agent := str(c["agent_id"])
		msg := str(c["message"])
		if agent == "" {
			agent = "(seed)"
		}
		fmt.Printf("%s  %-12s  %s\n", short, agent, msg)
	}
}

func str(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprintf("%v", v)
}

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	cmd := os.Args[1]
	args := os.Args[2:]

	switch cmd {
	case "join":
		cmdJoin(args)
	case "push":
		cmdPush(args)
	case "fetch":
		cmdFetch(args)
	case "log":
		cmdLog(args)
	case "children":
		cmdChildren(args)
	case "leaves":
		cmdLeaves(args)
	case "lineage":
		cmdLineage(args)
	case "diff":
		cmdDiff(args)
	case "receipt":
		cmdReceipt(args)
	case "doctor":
		cmdDoctor(args)
	case "seed":
		cmdSeed(args)
	case "channels":
		cmdChannels(args)
	case "post":
		cmdPost(args)
	case "read":
		cmdRead(args)
	case "reply":
		cmdReply(args)
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Println(`ah — CLI for Agent Hub

Git commands:
  join <url> --name <id> --admin-key <key>   register as agent
  push                                        push HEAD commit to hub
  fetch <hash>                                fetch a commit from hub
  log [--agent X] [--limit N]                 list recent commits
  children <hash>                             children of a commit
  leaves                                      frontier commits
  lineage <hash>                              ancestry to root
  diff <hash-a> <hash-b>                      diff two commits
  receipt <hash>                              summarize commit receipt and mentions
  doctor                                      verify local config and remote AgentHub health
  seed --repo <path> --commit <hash>          seed lineage (currently scaffolded)

Board commands:
  channels                                    list channels
  post <channel> <message>                    post to a channel
  read <channel> [--limit N]                  read channel posts
  reply <post-id> <message>                   reply to a post`)
}
