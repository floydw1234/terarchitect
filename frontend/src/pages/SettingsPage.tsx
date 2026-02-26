import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Paper,
  TextField,
  Stack,
  Alert,
  CircularProgress,
  Divider,
  InputAdornment,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  FormHelperText,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import { getSettings, updateSettings, type AppSettingsResponse } from '../utils/api';

/** keys: when set, this field reads/writes multiple settings (e.g. one "LLM base URL" for both Director and Worker). */
type FieldMeta = { key: string; label: string; hint: string; sensitive: boolean; options?: readonly string[]; optionLabels?: Record<string, string>; keys?: string[] };

const WORKER_MODES = ['opencode', 'claude-code'] as const;
type WorkerMode = typeof WORKER_MODES[number];

const WORKER_MODE_LABELS: Record<WorkerMode, string> = {
  'opencode': 'OpenCode (default)',
  'claude-code': 'Claude Code (headless)',
};

const FIELDS: FieldMeta[] = [
  { key: 'github_user_token', label: 'GitHub user token', hint: 'UI: PR comments, approve, merge, poll. Classic PAT with repo scope.', sensitive: true },
  { key: 'github_agent_token', label: 'GitHub agent token', hint: 'Agent: push branches, create PRs, and reply to PR comments.', sensitive: true },
  { key: 'GIT_USER_NAME', label: 'Agent git name', hint: 'Author name for agent container commits (default: Terarchitect Agent).', sensitive: false },
  { key: 'GIT_USER_EMAIL', label: 'Agent git email', hint: 'Author email for agent container commits (default: agent@terarchitect.local).', sensitive: false },
  { key: 'GIT_DASHBOARD_USER_NAME', label: 'Dashboard git name', hint: 'Git author name for backend/UI (e.g. backend in Docker). Leave blank if not needed.', sensitive: false },
  { key: 'GIT_DASHBOARD_USER_EMAIL', label: 'Dashboard git email', hint: 'Git author email for backend/UI (e.g. backend in Docker). Leave blank if not needed.', sensitive: false },
  // Agent (Director): own URL, model, key
  { key: 'VLLM_URL', label: 'LLM URL', hint: 'Base URL for the Agent/Director (e.g. http://localhost:8000).', sensitive: false },
  { key: 'AGENT_MODEL', label: 'Model', hint: 'Leave blank for Qwen/Qwen3-Coder-Next-FP8.', sensitive: false },
  { key: 'AGENT_API_KEY', label: 'API key', hint: 'Optional API key for the Agent LLM.', sensitive: true },
  { key: 'MIDDLE_AGENT_DEBUG', label: 'Debug', hint: '1 = verbose logs; 0 = quiet.', sensitive: false },
  // Worker mode selector
  { key: 'WORKER_MODE', label: 'Worker mode', hint: 'OpenCode uses an OpenCode HTTP server. Claude Code runs the claude CLI in headless mode (-p flag).', sensitive: false, options: WORKER_MODES, optionLabels: WORKER_MODE_LABELS },
  // OpenCode worker: URL, model, key
  { key: 'WORKER_LLM_URL', label: 'LLM URL', hint: 'Leave blank for http://localhost:8080/v1.', sensitive: false },
  { key: 'WORKER_MODEL', label: 'Model', hint: 'Leave blank to use the same model as the Agent (above).', sensitive: false },
  { key: 'WORKER_API_KEY', label: 'API key', hint: 'Optional API key for the Worker LLM.', sensitive: true },
  { key: 'WORKER_TIMEOUT_SEC', label: 'Timeout (seconds)', hint: 'Per-request timeout (default 3600).', sensitive: false },
  // Frontend LLM: UI-powered LLM features (graph-derived helpers, suggestions, etc.)
  { key: 'FRONTEND_LLM_URL', label: 'Frontend LLM URL', hint: 'Base URL for frontend-driven LLM features (e.g. http://localhost:8000).', sensitive: false },
  { key: 'FRONTEND_LLM_MODEL', label: 'Frontend LLM model', hint: 'Model used by backend endpoints serving frontend LLM features.', sensitive: false },
  { key: 'FRONTEND_LLM_API_KEY', label: 'Frontend LLM API key', hint: 'Optional API key for frontend-driven LLM requests.', sensitive: true },
  // Memory: LLM + embedding (HippoRAG)
  { key: 'MEMORY_LLM_BASE_URL', label: 'LLM URL', hint: 'Leave blank to use the Agent URL (above).', sensitive: false },
  { key: 'MEMORY_LLM_MODEL', label: 'LLM model', hint: 'Leave blank to use the Agent model (above).', sensitive: false },
  { key: 'MEMORY_LLM_API_KEY', label: 'LLM API key', hint: 'Optional. Leave blank to use Agent API key or env.', sensitive: true },
  { key: 'EMBEDDING_SERVICE_URL', label: 'Embedding URL', hint: 'URL for embedding service. Default http://localhost:9009.', sensitive: false, keys: ['EMBEDDING_SERVICE_URL', 'MEMORY_EMBEDDING_BASE_URL'] },
  { key: 'MEMORY_EMBEDDING_MODEL', label: 'Embedding model', hint: 'Model for embedding (memory and app).', sensitive: false },
  { key: 'EMBEDDING_API_KEY', label: 'Embedding API key', hint: 'Embedding service X-API-Key if required.', sensitive: true },
  { key: 'openai_api_key', label: 'OpenAI API key', hint: 'For OpenAI-compatible embedding/LLM calls. Required for memory.', sensitive: true },
  { key: 'anthropic_api_key', label: 'Anthropic API key', hint: 'Optional; for memory or other features.', sensitive: true },
  // Coordinator
  { key: 'MAX_CONCURRENT_AGENTS', label: 'Max concurrent agents', hint: 'How many agent jobs the coordinator runs in parallel. Default 1. Safe to raise with DinD enabled (each container has its own isolated Docker daemon). The coordinator re-reads this every poll cycle — no restart needed.', sensitive: false },
  // Worker-facing API (Phase 1: coordinator and agent containers)
  { key: 'TERARCHITECT_WORKER_API_KEY', label: 'Worker API key', hint: 'Bearer token for /api/worker/* and worker-context/logs/complete. If set, coordinator and agent must send Authorization: Bearer <key>.', sensitive: true },
];

const SECTIONS: { title: string; keys: string[]; description?: string }[] = [
  { title: 'GitHub', keys: ['github_user_token', 'github_agent_token', 'GIT_USER_NAME', 'GIT_USER_EMAIL', 'GIT_DASHBOARD_USER_NAME', 'GIT_DASHBOARD_USER_EMAIL'], description: 'Tokens for UI and agent. Agent git = commits from agent container. Dashboard git = backend/UI (e.g. when backend runs in Docker). Leave blank for defaults.' },
  { title: 'Agent', keys: ['VLLM_URL', 'AGENT_MODEL', 'AGENT_API_KEY', 'MIDDLE_AGENT_DEBUG'] },
  { title: 'Worker', keys: ['WORKER_MODE', 'WORKER_LLM_URL', 'WORKER_MODEL', 'WORKER_API_KEY', 'WORKER_TIMEOUT_SEC'], description: 'Worker used inside the agent container. OpenCode (default) uses an LLM via HTTP; Claude Code runs the claude CLI headless.' },
  { title: 'Frontend LLM', keys: ['FRONTEND_LLM_URL', 'FRONTEND_LLM_MODEL', 'FRONTEND_LLM_API_KEY'], description: 'Optional. Used by backend for frontend AI features (e.g. graph-based suggestions).' },
  { title: 'Memory', keys: ['MEMORY_LLM_BASE_URL', 'MEMORY_LLM_MODEL', 'MEMORY_LLM_API_KEY', 'EMBEDDING_SERVICE_URL', 'MEMORY_EMBEDDING_MODEL', 'EMBEDDING_API_KEY', 'openai_api_key', 'anthropic_api_key'], description: 'HippoRAG: LLM and embedding for the memory system. Leave URL/model blank to use Agent settings.' },
  { title: 'Coordinator', keys: ['MAX_CONCURRENT_AGENTS'], description: 'Settings read by the coordinator process. Changes take effect on the next poll cycle without restarting the coordinator.' },
  { title: 'Worker API', keys: ['TERARCHITECT_WORKER_API_KEY'], description: 'Auth for worker-facing API (coordinator and agent containers). Leave blank for no auth (dev).' },
];

const keyToMeta: Record<string, FieldMeta> = Object.fromEntries(FIELDS.map((f) => [f.key, f]));

const SettingsPage: React.FC = () => {
  const [status, setStatus] = useState<AppSettingsResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    setLoading(true);
    setMessage(null);
    try {
      const data = await getSettings();
      setStatus(data);
      setValues({});
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to load settings' });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const next = await updateSettings(values);
      setStatus(next);
      setValues({});
      setMessage({ type: 'success', text: 'Settings saved.' });
    } catch (e) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to save settings' });
    } finally {
      setSaving(false);
    }
  };

  const hasChanges = Object.keys(values).length > 0;

  const primaryKey = (meta: FieldMeta) => meta.keys?.[0] ?? meta.key;

  /** Current worker mode: pending edit takes priority, then saved value, then default. */
  const currentWorkerMode = (): WorkerMode => {
    const pending = values['WORKER_MODE'];
    if (pending !== undefined) return (WORKER_MODES.includes(pending as WorkerMode) ? pending : 'opencode') as WorkerMode;
    const saved = status?.['WORKER_MODE'];
    if (typeof saved === 'string' && WORKER_MODES.includes(saved as WorkerMode)) return saved as WorkerMode;
    return 'opencode';
  };

  /** Keys hidden when worker mode is claude-code (URL and model not needed). */
  const CLAUDE_CODE_HIDDEN_KEYS = new Set(['WORKER_LLM_URL', 'WORKER_MODEL']);

  const displayValue = (key: string): string => {
    const meta = keyToMeta[key];
    const pk = meta ? primaryKey(meta) : key;
    if (values[pk] !== undefined) return values[pk];
    if (meta?.sensitive) return '';
    if (meta?.keys) {
      const v = meta.keys.map((k) => status?.[k]).find((v) => v != null && (typeof v !== 'string' || v.trim() !== ''));
      if (v === undefined || v === null) return '';
      return typeof v === 'string' ? v : '';
    }
    const v = status?.[key];
    if (v === null || v === undefined) return '';
    return typeof v === 'string' ? v : '';
  };

  /** True if a value exists in the DB. For composite fields, true if any of the keys is set. */
  const isSet = (key: string): boolean => {
    const meta = keyToMeta[key];
    const keys = meta?.keys ?? [key];
    for (const k of keys) {
      const v = status?.[k];
      if (v === undefined || v === null) continue;
      if (typeof v === 'boolean') { if (v) return true; continue; }
      if (String(v).trim().length > 0) return true;
    }
    return false;
  };

  const handleFieldChange = (key: string, value: string) => {
    const meta = keyToMeta[key];
    const pk = primaryKey(meta);
    if (meta?.keys) {
      const updates: Record<string, string> = {};
      meta.keys.forEach((k) => { updates[k] = value; });
      setValues((prev) => ({ ...prev, ...updates }));
    } else {
      setValues((prev) => ({ ...prev, [pk]: value }));
    }
  };


  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 720, mx: 'auto' }}>
      <Typography variant="h5" sx={{ mb: 2 }}>
        Settings
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Secrets are encrypted at rest. Leave a field blank to keep the current value; enter a new value to update, or
        clear and save to remove. URLs and paths are stored plain.
      </Typography>

      {message && (
        <Alert severity={message.type} onClose={() => setMessage(null)} sx={{ mb: 2 }}>
          {message.text}
        </Alert>
      )}

      <Paper sx={{ p: 3 }}>
        {SECTIONS.map((section) => (
          <Box key={section.title} sx={{ mb: 3 }}>
            <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 0.5 }}>
              {section.title}
            </Typography>
            {section.description && (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                {section.description}
              </Typography>
            )}
            <Stack spacing={2} sx={{ mt: section.description ? 0 : 1 }}>
              {section.keys.map((key) => {
                const meta = keyToMeta[key];
                if (!meta) return null;

                // Hide URL/model fields when Claude Code mode is active
                const workerMode = currentWorkerMode();
                if (workerMode === 'claude-code' && CLAUDE_CODE_HIDDEN_KEYS.has(key)) return null;

                const sensitive = meta.sensitive;
                const set = isSet(key);
                const value = displayValue(key);
                const pk = primaryKey(meta);

                // Dynamic hint for WORKER_API_KEY based on mode
                const hint = key === 'WORKER_API_KEY' && workerMode === 'claude-code'
                  ? 'Anthropic API key for Claude Code (passed as ANTHROPIC_API_KEY to the claude CLI).'
                  : meta.hint;

                // Render as Select dropdown for fields with options
                if (meta.options) {
                  const selectValue = value || meta.options[0];
                  return (
                    <FormControl key={key} fullWidth size="small">
                      <InputLabel>{meta.label}</InputLabel>
                      <Select
                        label={meta.label}
                        value={selectValue}
                        onChange={(e) => handleFieldChange(key, e.target.value as string)}
                      >
                        {meta.options.map((opt) => (
                          <MenuItem key={opt} value={opt}>
                            {meta.optionLabels?.[opt] ?? opt}
                          </MenuItem>
                        ))}
                      </Select>
                      <FormHelperText>{hint}</FormHelperText>
                    </FormControl>
                  );
                }

                return (
                  <Box key={key}>
                    <TextField
                      fullWidth
                      size="small"
                      type={sensitive ? 'password' : 'text'}
                      label={meta.label}
                      placeholder={sensitive ? (set ? '••••••••' : 'Not set') : 'Leave blank for default'}
                      value={value}
                      onChange={(e) => handleFieldChange(key, e.target.value)}
                      helperText={hint}
                      autoComplete="off"
                      InputProps={
                        sensitive && set && values[pk] === undefined
                          ? {
                              endAdornment: (
                                <InputAdornment position="end">
                                  <CheckCircleIcon color="success" fontSize="small" titleAccess="Value is set" />
                                </InputAdornment>
                              ),
                            }
                          : undefined
                      }
                    />
                    {sensitive && set && values[pk] === undefined && (
                      <Typography variant="caption" color="success.main" sx={{ mt: 0.5, display: 'block' }}>
                        ✓ Set
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </Stack>
            <Divider sx={{ mt: 2 }} />
          </Box>
        ))}

        <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
          <Button variant="contained" onClick={handleSave} disabled={saving || !hasChanges}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
          {hasChanges && (
            <Button onClick={() => setValues({})} disabled={saving}>
              Reset
            </Button>
          )}
        </Box>
      </Paper>
    </Box>
  );
};

export default SettingsPage;
