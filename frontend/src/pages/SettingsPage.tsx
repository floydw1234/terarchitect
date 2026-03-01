import React, { useState, useEffect, useRef } from 'react';
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
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Table,
  TableBody,
  TableRow,
  TableCell,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import { getSettings, updateSettings, type AppSettingsResponse } from '../utils/api';

/** keys: when set, this field reads/writes multiple settings (e.g. one "LLM base URL" for both Director and Worker). */
type FieldMeta = {
  key: string;
  label: string;
  hint: string;
  sensitive: boolean;
  options?: readonly string[];
  optionLabels?: Record<string, string>;
  keys?: string[];
  /** If true, show a "Required" chip when the field is not set. Execution will be blocked. */
  required?: boolean;
  /** If true, show a "Recommended" chip when the field is not set. Features degrade without it. */
  recommended?: boolean;
};

const WORKER_MODES = ['claude-code', 'opencode'] as const;
type WorkerMode = typeof WORKER_MODES[number];

const WORKER_MODE_LABELS: Record<WorkerMode, string> = {
  'opencode': 'OpenCode',
  'claude-code': 'Claude Code (default)',
};

const AGENT_PROVIDERS = ['openai', 'custom'] as const;
type AgentProvider = typeof AGENT_PROVIDERS[number];

const AGENT_PROVIDER_LABELS: Record<AgentProvider, string> = {
  'openai': 'OpenAI (default)',
  'custom': 'Custom (OpenAI-compatible)',
};

const EMBEDDING_PROVIDERS = ['openai', 'custom'] as const;
type EmbeddingProvider = typeof EMBEDDING_PROVIDERS[number];

const EMBEDDING_PROVIDER_LABELS: Record<EmbeddingProvider, string> = {
  'openai': 'OpenAI',
  'custom': 'Custom',
};

const FIELDS: FieldMeta[] = [
  // GitHub
  { key: 'github_agent_token', label: 'GitHub token', hint: 'Used by the agent (push branches, create PRs, reply to PR comments) and the UI (PR polling, approve, merge). Classic PAT with repo scope.', sensitive: true, required: true },
  { key: 'GIT_USER_NAME', label: 'Git name', hint: 'Author name for agent commits. No default — e.g. "My Agent".', sensitive: false, required: true },
  { key: 'GIT_USER_EMAIL', label: 'Git email', hint: 'Author email for agent commits. No default — e.g. agent@myorg.com.', sensitive: false, required: true },
  // Director LLM
  { key: 'AGENT_PROVIDER', label: 'Provider', hint: 'OpenAI: auto-resolves the Director LLM URL — just supply your API key. Custom: provide your own OpenAI-compatible base URL.', sensitive: false, options: AGENT_PROVIDERS, optionLabels: AGENT_PROVIDER_LABELS },
  { key: 'AGENT_LLM_URL', label: 'Director LLM URL', hint: 'Base URL for the Director LLM. No default — e.g. http://your-host:8000.', sensitive: false, required: true },
  { key: 'AGENT_MODEL', label: 'Director model', hint: 'Director LLM model name. No default — e.g. gpt-4o, claude-opus-4-5, or your local model ID.', sensitive: false, required: true },
  { key: 'AGENT_API_KEY', label: 'Director API key', hint: "Required. Your OpenAI key (sk-...) for OpenAI provider, or your LLM provider key. Use 'dummy' for local LLMs that skip auth.", sensitive: true, required: true },
  { key: 'MIDDLE_AGENT_DEBUG', label: 'Debug', hint: '1 = verbose logs; 0 = quiet.', sensitive: false },
  // Worker mode selector
  { key: 'WORKER_MODE', label: 'Worker mode', hint: 'Claude Code runs the claude CLI in headless mode (-p flag). OpenCode uses an HTTP LLM server inside the agent.', sensitive: false, options: WORKER_MODES, optionLabels: WORKER_MODE_LABELS },
  // OpenCode worker: URL, model, key — all required, no defaults
  { key: 'WORKER_LLM_URL', label: 'LLM URL', hint: 'Worker LLM base URL. No default — e.g. http://your-host:8080/v1.', sensitive: false, required: true },
  { key: 'WORKER_MODEL', label: 'Model', hint: 'Model name. Required for OpenCode. Optional for Claude Code — passed as --model to the claude CLI (e.g. claude-opus-4-5). Leave blank to use the Claude Code default.', sensitive: false, required: true },
  { key: 'WORKER_API_KEY', label: 'API key', hint: 'API key for the Worker LLM. Required in all modes.', sensitive: true, required: true },
  { key: 'WORKER_TIMEOUT_SEC', label: 'Timeout (seconds)', hint: 'Per-request Worker timeout. Default 3600.', sensitive: false },
  // Frontend LLM — defaults to Agent settings when blank
  { key: 'FRONTEND_LLM_URL', label: 'Frontend LLM URL', hint: 'Base URL for frontend LLM features. Leave blank to use Agent LLM URL.', sensitive: false },
  { key: 'FRONTEND_LLM_MODEL', label: 'Frontend LLM model', hint: 'Model for frontend LLM features. Leave blank to use Agent model.', sensitive: false },
  { key: 'FRONTEND_LLM_API_KEY', label: 'Frontend LLM API key', hint: 'API key for frontend LLM requests. Leave blank to use Agent API key.', sensitive: true },
  // Memory LLM (HippoRAG) — defaults to Agent settings when blank
  { key: 'MEMORY_LLM_BASE_URL', label: 'LLM URL', hint: 'Memory (HippoRAG) LLM URL. Leave blank to use Agent LLM URL.', sensitive: false },
  { key: 'MEMORY_LLM_MODEL', label: 'LLM model', hint: 'Memory LLM model. Leave blank to use Agent model.', sensitive: false },
  { key: 'MEMORY_LLM_API_KEY', label: 'LLM API key', hint: 'Memory LLM API key. Leave blank to use Agent API key.', sensitive: true },
  // Embeddings (ticket/graph search and HippoRAG memory)
  { key: 'EMBEDDING_PROVIDER', label: 'Provider', hint: 'OpenAI: uses the OpenAI Python SDK directly — just supply your API key. Custom: provide your own endpoint URL and key.', sensitive: false, options: EMBEDDING_PROVIDERS, optionLabels: EMBEDDING_PROVIDER_LABELS },
  { key: 'EMBEDDING_SERVICE_URL', label: 'Embedding URL', hint: 'Base URL for your custom embedding service. e.g. http://your-host:9000/v1.', sensitive: false, required: true },
  { key: 'MEMORY_EMBEDDING_MODEL', label: 'Embedding model', hint: 'Embedding model name. e.g. text-embedding-3-small for OpenAI.', sensitive: false, required: true },
  { key: 'EMBEDDING_API_KEY', label: 'Embedding API key', hint: "API key for your custom embedding service. Use 'dummy' for local services that skip auth.", sensitive: true, required: true },
  { key: 'openai_api_key', label: 'OpenAI API key', hint: 'Your OpenAI API key (sk-...). Required for OpenAI embeddings. Also used as a fallback for memory LLM calls.', sensitive: true, required: true },
  // Coordinator
  { key: 'MAX_CONCURRENT_AGENTS', label: 'Max concurrent agents', hint: 'Parallel agent jobs the coordinator runs. Default 1. Safe to increase with DinD (each container has its own Docker daemon). Re-read every poll cycle — no restart needed.', sensitive: false },
  // Worker-facing API auth
  { key: 'TERARCHITECT_WORKER_API_KEY', label: 'Worker API key', hint: 'Bearer token for /api/worker/* endpoints. If set, coordinator and agent must include Authorization: Bearer <key>. Leave blank for no auth (dev).', sensitive: true },
];

const SECTIONS: { title: string; keys: string[]; description?: string }[] = [
  { title: 'GitHub', keys: ['github_agent_token', 'GIT_USER_NAME', 'GIT_USER_EMAIL'], description: 'One token and git identity for all GitHub actions: agent pushes/PRs, PR comment replies, UI polling, approve, and merge.' },
  { title: 'Director', keys: ['AGENT_PROVIDER', 'AGENT_LLM_URL', 'AGENT_MODEL', 'AGENT_API_KEY', 'MIDDLE_AGENT_DEBUG'], description: 'The Director LLM orchestrates the agent — assessing progress, interpreting results, and deciding next steps.' },
  { title: 'Worker', keys: ['WORKER_MODE', 'WORKER_LLM_URL', 'WORKER_MODEL', 'WORKER_API_KEY', 'WORKER_TIMEOUT_SEC'], description: 'Worker used inside the agent container. Claude Code (default) runs the claude CLI headless; OpenCode uses an LLM via HTTP.' },
  { title: 'Embeddings', keys: ['EMBEDDING_PROVIDER', 'EMBEDDING_SERVICE_URL', 'MEMORY_EMBEDDING_MODEL', 'EMBEDDING_API_KEY', 'openai_api_key'], description: 'Used for ticket/graph search and HippoRAG memory indexing.' },
  { title: 'Frontend LLM', keys: ['FRONTEND_LLM_URL', 'FRONTEND_LLM_MODEL', 'FRONTEND_LLM_API_KEY'], description: 'Optional. Used by backend for AI features (e.g. graph-based suggestions). Defaults to Agent settings when blank.' },
  { title: 'Memory', keys: ['MEMORY_LLM_BASE_URL', 'MEMORY_LLM_MODEL', 'MEMORY_LLM_API_KEY'], description: 'HippoRAG LLM settings. Leave blank to use Agent settings.' },
  { title: 'Coordinator', keys: ['MAX_CONCURRENT_AGENTS'], description: 'Settings read by the coordinator process. Changes take effect on the next poll cycle without restarting the coordinator.' },
  { title: 'Worker API', keys: ['TERARCHITECT_WORKER_API_KEY'], description: 'Auth for worker-facing API (coordinator and agent containers). Leave blank for no auth (dev).' },
];

const keyToMeta: Record<string, FieldMeta> = Object.fromEntries(FIELDS.map((f) => [f.key, f]));

/** All recognized setting keys (flat — includes alias keys from composite fields). */
const ALL_KNOWN_KEYS = new Set<string>([
  ...FIELDS.map((f) => f.key),
  ...FIELDS.flatMap((f) => f.keys ?? []),
]);

const SettingsPage: React.FC = () => {
  const [status, setStatus] = useState<AppSettingsResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Import dialog
  const [importOpen, setImportOpen] = useState(false);
  const [importJson, setImportJson] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [importPreview, setImportPreview] = useState<{ key: string; value: string; known: boolean }[] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
    if (pending !== undefined) return (WORKER_MODES.includes(pending as WorkerMode) ? pending : 'claude-code') as WorkerMode;
    const saved = status?.['WORKER_MODE'];
    if (typeof saved === 'string' && WORKER_MODES.includes(saved as WorkerMode)) return saved as WorkerMode;
    return 'claude-code';
  };

  /** Current agent provider: openai (default) or custom. */
  const currentAgentProvider = (): AgentProvider => {
    const pending = values['AGENT_PROVIDER'];
    if (pending !== undefined) return (AGENT_PROVIDERS.includes(pending as AgentProvider) ? pending : 'openai') as AgentProvider;
    const saved = status?.['AGENT_PROVIDER'];
    if (typeof saved === 'string' && AGENT_PROVIDERS.includes(saved as AgentProvider)) return saved as AgentProvider;
    return 'openai';
  };

  /** Current embedding provider: openai (default) or custom. */
  const currentEmbeddingProvider = (): EmbeddingProvider => {
    const pending = values['EMBEDDING_PROVIDER'];
    if (pending !== undefined) return (EMBEDDING_PROVIDERS.includes(pending as EmbeddingProvider) ? pending : 'openai') as EmbeddingProvider;
    const saved = status?.['EMBEDDING_PROVIDER'];
    if (typeof saved === 'string' && EMBEDDING_PROVIDERS.includes(saved as EmbeddingProvider)) return saved as EmbeddingProvider;
    return 'openai';
  };

  /** Keys hidden when worker mode is claude-code (LLM URL not needed; model is optional). */
  const CLAUDE_CODE_HIDDEN_KEYS = new Set(['WORKER_LLM_URL']);

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

  // ---------------------------------------------------------------------------
  // Import / Export
  // ---------------------------------------------------------------------------

  const parseImportJson = (raw: string) => {
    setImportError(null);
    setImportPreview(null);
    if (!raw.trim()) return;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
        setImportError('Config must be a JSON object (key/value pairs).');
        return;
      }
      const preview: { key: string; value: string; known: boolean }[] = [];
      for (const [k, v] of Object.entries(parsed)) {
        if (typeof v !== 'string' && typeof v !== 'number' && typeof v !== 'boolean') continue;
        preview.push({ key: k, value: String(v), known: ALL_KNOWN_KEYS.has(k) });
      }
      if (preview.length === 0) {
        setImportError('No recognizable key/value pairs found in the JSON.');
        return;
      }
      setImportPreview(preview);
    } catch {
      setImportError('Invalid JSON — check the file and try again.');
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      setImportJson(text);
      parseImportJson(text);
    };
    reader.readAsText(file);
    // Reset so the same file can be re-uploaded
    e.target.value = '';
  };

  const handleApplyImport = () => {
    if (!importPreview) return;
    const updates: Record<string, string> = {};
    for (const { key, value, known } of importPreview) {
      if (!known) continue; // silently skip unknown keys
      if (!value && value !== '0') continue; // skip truly empty values
      updates[key] = value;
    }
    setValues((prev) => ({ ...prev, ...updates }));
    setImportOpen(false);
    setImportJson('');
    setImportPreview(null);
    setImportError(null);
    const count = Object.keys(updates).length;
    setMessage({ type: 'success', text: `${count} setting${count !== 1 ? 's' : ''} imported — review and save to apply.` });
  };

  const handleExport = () => {
    if (!status) return;
    const out: Record<string, string> = {};
    for (const field of FIELDS) {
      if (field.sensitive) {
        // Export as empty placeholder so the user knows what keys exist
        out[field.key] = '';
      } else {
        const v = status[field.key];
        out[field.key] = typeof v === 'string' ? v : '';
      }
    }
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'terarchitect-config.json';
    a.click();
    URL.revokeObjectURL(url);
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
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 2, gap: 2, flexWrap: 'wrap' }}>
        <Box>
          <Typography variant="h5">Settings</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Secrets are encrypted at rest. Leave a field blank to keep the current value; enter a new value to update, or
            clear and save to remove.
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, flexShrink: 0 }}>
          <Button
            size="small"
            variant="outlined"
            startIcon={<UploadFileIcon />}
            onClick={() => { setImportOpen(true); setImportJson(''); setImportPreview(null); setImportError(null); }}
          >
            Import config
          </Button>
          <Button
            size="small"
            variant="outlined"
            startIcon={<FileDownloadIcon />}
            onClick={handleExport}
            disabled={!status}
          >
            Export config
          </Button>
        </Box>
      </Box>

      {/* Hidden file input for JSON upload */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        style={{ display: 'none' }}
        onChange={handleFileUpload}
      />

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

                // Provider-aware visibility and required logic
                const workerMode = currentWorkerMode();
                const agentProvider = currentAgentProvider();
                const embeddingProvider = currentEmbeddingProvider();

                // Hide fields based on mode / provider
                if (workerMode === 'claude-code' && CLAUDE_CODE_HIDDEN_KEYS.has(key)) return null;
                if (agentProvider === 'openai' && key === 'AGENT_LLM_URL') return null;
                if (embeddingProvider === 'openai' && (key === 'EMBEDDING_SERVICE_URL' || key === 'EMBEDDING_API_KEY')) return null;
                if (embeddingProvider === 'custom' && key === 'openai_api_key') return null;

                const sensitive = meta.sensitive;
                const set = isSet(key);
                const value = displayValue(key);
                const pk = primaryKey(meta);

                // Dynamic hints based on provider / mode
                let hint = meta.hint;
                if (key === 'WORKER_API_KEY') {
                  hint = workerMode === 'claude-code'
                    ? "Anthropic API key for Claude Code (passed as ANTHROPIC_API_KEY to the claude CLI). Required."
                    : "API key for the Worker LLM. Use your provider key or 'dummy' for local LLMs that skip auth. Required.";
                } else if (key === 'AGENT_API_KEY' && agentProvider === 'openai') {
                  hint = "Your OpenAI API key (sk-...) — used as the Director API key.";
                } else if (key === 'openai_api_key' && embeddingProvider === 'openai') {
                  hint = "Your OpenAI API key (sk-...). Required for OpenAI embeddings.";
                }

                // Compute whether this field is required / recommended
                let isRequired = false;
                if (meta.required) {
                  if (key === 'WORKER_LLM_URL' || key === 'WORKER_MODEL') {
                    isRequired = workerMode !== 'claude-code';
                  } else if (key === 'AGENT_LLM_URL') {
                    isRequired = agentProvider !== 'openai';
                  } else if (key === 'EMBEDDING_SERVICE_URL' || key === 'EMBEDDING_API_KEY') {
                    isRequired = embeddingProvider !== 'openai';
                  } else if (key === 'openai_api_key') {
                    isRequired = embeddingProvider === 'openai';
                  } else if (key === 'MEMORY_EMBEDDING_MODEL') {
                    isRequired = true; // always required regardless of provider
                  } else {
                    isRequired = true;
                  }
                }
                // WORKER_API_KEY required in all modes
                if (key === 'WORKER_API_KEY') isRequired = true;

                const isRecommended = meta.recommended === true && !isRequired;

                const statusChip = !set ? (
                  isRequired ? (
                    <Chip label="Required" size="small" color="error" sx={{ ml: 1, height: 18, fontSize: '0.65rem' }} />
                  ) : isRecommended ? (
                    <Chip label="Recommended" size="small" color="warning" sx={{ ml: 1, height: 18, fontSize: '0.65rem' }} />
                  ) : null
                ) : null;

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
                    <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.5 }}>
                      <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                        {meta.label}
                      </Typography>
                      {statusChip}
                    </Box>
                    <TextField
                      fullWidth
                      size="small"
                      type={sensitive ? 'password' : 'text'}
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

      {/* Import config dialog */}
      <Dialog open={importOpen} onClose={() => setImportOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Import config</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Upload a <code>.json</code> file or paste JSON below. Known setting keys will be pre-filled for review —
            you can edit them before saving. Sensitive values (tokens, API keys) are accepted but never exported.
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={<UploadFileIcon />}
            onClick={() => fileInputRef.current?.click()}
            sx={{ mb: 2 }}
          >
            Choose file…
          </Button>
          <TextField
            label="Or paste JSON here"
            multiline
            minRows={5}
            maxRows={14}
            fullWidth
            size="small"
            value={importJson}
            onChange={(e) => {
              setImportJson(e.target.value);
              parseImportJson(e.target.value);
            }}
            sx={{ fontFamily: 'monospace' }}
            inputProps={{ style: { fontFamily: 'monospace', fontSize: '0.8rem' } }}
          />
          {importError && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {importError}
            </Alert>
          )}
          {importPreview && !importError && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" sx={{ mb: 1 }}>
                <strong>{importPreview.filter((r) => r.known).length}</strong> recognized settings
                {importPreview.filter((r) => !r.known).length > 0 && (
                  <> · <span style={{ color: '#94a3b8' }}>{importPreview.filter((r) => !r.known).length} unknown (will be ignored)</span></>
                )}
              </Typography>
              <Paper variant="outlined" sx={{ maxHeight: 280, overflowY: 'auto' }}>
                <Table size="small">
                  <TableBody>
                    {importPreview.map(({ key, value, known }) => (
                      <TableRow
                        key={key}
                        sx={{ opacity: known ? 1 : 0.4 }}
                      >
                        <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.75rem', py: 0.5, width: '45%' }}>
                          {key}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.75rem', py: 0.5, color: 'text.secondary' }}>
                          {keyToMeta[key]?.sensitive && value
                            ? '••••••••'
                            : value
                              ? (value.length > 60 ? `${value.slice(0, 60)}…` : value)
                              : <em>(empty)</em>}
                        </TableCell>
                        <TableCell sx={{ py: 0.5, width: 90, textAlign: 'right' }}>
                          {!known && (
                            <Chip label="unknown" size="small" sx={{ height: 16, fontSize: '0.6rem' }} />
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Paper>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setImportOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!importPreview || !!importError || importPreview.filter((r) => r.known).length === 0}
            onClick={handleApplyImport}
          >
            Apply
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default SettingsPage;
