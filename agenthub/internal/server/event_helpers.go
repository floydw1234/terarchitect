package server

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"

	"agenthub/internal/db"
)

var eventHeadRe = regexp.MustCompile(`^[a-z][a-z0-9_]{1,63}$`)

type normalizedEvent struct {
	ID         int            `json:"id"`
	ChannelID  int            `json:"channel_id"`
	Channel    string         `json:"channel_name,omitempty"`
	AgentID    string         `json:"agent_id"`
	ParentID   *int           `json:"parent_id"`
	Content    string         `json:"content"`
	RawContent string         `json:"raw_content"`
	Message    string         `json:"message"`
	EventType  string         `json:"event_type"`
	Metadata   map[string]any `json:"metadata"`
	Structured bool           `json:"structured"`
	CreatedAt  any            `json:"created_at"`
}

func normalizePostEvent(post db.PostWithChannel) normalizedEvent {
	out := normalizedEvent{
		ID:         post.ID,
		ChannelID:  post.ChannelID,
		Channel:    post.ChannelName,
		AgentID:    post.AgentID,
		ParentID:   post.ParentID,
		Content:    post.Content,
		RawContent: post.Content,
		Message:    post.Content,
		EventType:  "event",
		Metadata:   map[string]any{},
		Structured: false,
		CreatedAt:  post.CreatedAt,
	}

	var payload map[string]any
	if err := json.Unmarshal([]byte(post.Content), &payload); err == nil && payload["terarchitect_event"] == float64(1) {
		out.Structured = true
		if eventType := strings.TrimSpace(anyString(payload["type"])); eventType != "" {
			out.EventType = eventType
		}
		if message := strings.TrimSpace(anyString(payload["message"])); message != "" {
			out.Message = message
			out.Content = message
		}
		if metadata, ok := payload["metadata"].(map[string]any); ok {
			out.Metadata = metadata
		}
		return out
	}

	content := strings.TrimSpace(post.Content)
	switch {
	case strings.HasPrefix(content, "[feedback]"):
		out.EventType = "human_feedback"
	case strings.Contains(content, ":"):
		head := strings.ToLower(strings.ReplaceAll(strings.TrimSpace(strings.SplitN(content, ":", 2)[0]), " ", "_"))
		if eventHeadRe.MatchString(head) {
			out.EventType = head
		}
	case strings.HasPrefix(strings.ToLower(content), "done"):
		out.EventType = "attempt_published"
	}
	return out
}

func anyString(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func dedupeChannels(posts []db.PostWithChannel) []string {
	seen := map[string]struct{}{}
	var channels []string
	for _, post := range posts {
		if _, ok := seen[post.ChannelName]; ok {
			continue
		}
		seen[post.ChannelName] = struct{}{}
		channels = append(channels, post.ChannelName)
	}
	sort.Strings(channels)
	return channels
}
