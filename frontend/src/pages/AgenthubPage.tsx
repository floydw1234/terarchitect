import React, { useEffect, useState, useCallback } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded';
import { AGENTHUB_URL } from '../utils/api';
import CommitDagGraph from '../components/graph/CommitDagGraph';
import { accentFromString, graphGlassPanelSx } from '../components/graph/graphVisuals';

const AGENTHUB_KEY_STORAGE = 'terarchitect.agenthub.key';
const AUTH_REQUIRED_MESSAGE = 'AgentHub requires an API key. Enter or update it below, then save to reload the DAG.';

interface Commit {
  hash: string;
  parent_hash: string;
  agent_id: string;
  message: string;
  created_at: string;
}

interface Channel {
  id: number;
  name: string;
  description: string;
  created_at: string;
}

interface Post {
  id: number;
  channel_id: number;
  agent_id: string;
  parent_id: number | null;
  content: string;
  created_at: string;
}

function short(hash: string) {
  return hash ? hash.slice(0, 10) : '';
}

function timeAgo(iso: string) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

async function ahGet(path: string) {
  const storedKey = window.localStorage.getItem(AGENTHUB_KEY_STORAGE)?.trim();
  const key = storedKey || (window as any).__AH_KEY__ || '';
  const resp = await fetch(AGENTHUB_URL + path, {
    headers: key ? { Authorization: `Bearer ${key}` } : undefined,
  });
  if (resp.status === 401) {
    const authError = new Error(AUTH_REQUIRED_MESSAGE);
    (authError as Error & { code?: string }).code = 'AUTH_REQUIRED';
    throw authError;
  }
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

const StatCard: React.FC<{ label: string; value: number; accent: string }> = ({ label, value, accent }) => (
  <Card sx={{ minHeight: 132 }}>
    <CardContent>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.14em' }}>
        {label}
      </Typography>
      <Typography variant="h4" sx={{ mt: 1, color: accent }}>
        {value}
      </Typography>
    </CardContent>
  </Card>
);

const AgenthubPage: React.FC = () => {
  const [online, setOnline] = useState<boolean | null>(null);
  const [leaves, setLeaves] = useState<Commit[]>([]);
  const [log, setLog] = useState<Commit[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [recentPosts, setRecentPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [keyDraft, setKeyDraft] = useState('');
  const [hasSavedKey, setHasSavedKey] = useState(() => Boolean(window.localStorage.getItem(AGENTHUB_KEY_STORAGE)?.trim()));
  const [usingDevFallback, setUsingDevFallback] = useState(
    () => !window.localStorage.getItem(AGENTHUB_KEY_STORAGE)?.trim() && Boolean((window as any).__AH_KEY__),
  );
  const resetProtectedData = useCallback(() => {
    setLeaves([]);
    setLog([]);
    setChannels([]);
    setRecentPosts([]);
    setLastRefresh(null);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const storedKey = window.localStorage.getItem(AGENTHUB_KEY_STORAGE)?.trim();
    setHasSavedKey(Boolean(storedKey));
    setUsingDevFallback(!storedKey && Boolean((window as any).__AH_KEY__));
    try {
      const health = await fetch(AGENTHUB_URL + '/api/health');
      setOnline(health.ok);

      if (!health.ok) {
        setLoading(false);
        return;
      }

      const [leavesData, logData, channelsData] = await Promise.all([
        ahGet('/api/git/leaves'),
        ahGet('/api/git/commits?limit=30'),
        ahGet('/api/channels'),
      ]);

      setLeaves(leavesData ?? []);
      setLog(logData ?? []);
      setChannels(channelsData ?? []);

      const posts: Post[] = [];
      for (const channel of (channelsData ?? []).slice(0, 5)) {
        try {
          const channelPosts = await ahGet(`/api/channels/${channel.name}/posts?limit=5`);
          posts.push(...(channelPosts ?? []));
        } catch {
          // Ignore channel-specific post failures and keep the dashboard usable.
        }
      }
      posts.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      setRecentPosts(posts.slice(0, 20));

      setLastRefresh(new Date());
    } catch (e: any) {
      if (e?.code === 'AUTH_REQUIRED') {
        resetProtectedData();
        setOnline(true);
      } else {
        setOnline(false);
      }
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [resetProtectedData]);

  useEffect(() => {
    load();
  }, [load]);

  const uniqueAgents = Array.from(
    new Set([...log.map((commit) => commit.agent_id), ...recentPosts.map((post) => post.agent_id)].filter(Boolean)),
  );

  const handleSaveKey = () => {
    const nextKey = keyDraft.trim();
    if (nextKey) {
      window.localStorage.setItem(AGENTHUB_KEY_STORAGE, nextKey);
    } else {
      window.localStorage.removeItem(AGENTHUB_KEY_STORAGE);
    }
    setKeyDraft('');
    void load();
  };

  const handleClearKey = () => {
    window.localStorage.removeItem(AGENTHUB_KEY_STORAGE);
    setKeyDraft('');
    resetProtectedData();
    void load();
  };

  return (
    <Box sx={{ maxWidth: 1440, mx: 'auto', display: 'flex', flexDirection: 'column', gap: 2.25 }}>
      <Box sx={{ ...graphGlassPanelSx, px: { xs: 2, md: 2.75 }, py: { xs: 2.25, md: 2.75 } }}>
        <Stack
          direction={{ xs: 'column', lg: 'row' }}
          spacing={2}
          justifyContent="space-between"
          alignItems={{ xs: 'flex-start', lg: 'center' }}
        >
          <Box>
            <Typography variant="overline" color="secondary.main">
              Multi-agent Operations
            </Typography>
            <Typography variant="h4" sx={{ mt: 0.5 }}>
              AgentHub
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1, maxWidth: 760 }}>
              Recent frontier state, active channels, and commit lineage from the AgentHub swarm in a shared graph visual language.
            </Typography>
          </Box>

          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Chip
              size="small"
              label={online === null ? 'Checking' : online ? 'Online' : 'Offline'}
              color={online ? 'success' : online === false ? 'error' : 'default'}
            />
            {lastRefresh && (
              <Chip
                size="small"
                label={`Refreshed ${timeAgo(lastRefresh.toISOString())}`}
                sx={{ bgcolor: 'rgba(255, 255, 255, 0.05)' }}
              />
            )}
            <Tooltip title="Refresh AgentHub data">
              <span>
                <IconButton onClick={load} disabled={loading} size="small">
                  <RefreshRoundedIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>
      </Box>

      <Card>
        <CardContent>
          <Stack direction={{ xs: 'column', lg: 'row' }} spacing={2} alignItems={{ xs: 'stretch', lg: 'center' }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle1">Connection</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                Store an AgentHub API key in this browser to load protected DAG data. The key is not shown in page text.
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                {hasSavedKey
                  ? 'A saved key is available in local storage.'
                  : usingDevFallback
                    ? 'Using the development fallback from window.__AH_KEY__.'
                    : 'No saved key is configured.'}
              </Typography>
            </Box>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.25} sx={{ width: { xs: '100%', lg: 'auto' } }}>
              <TextField
                type="password"
                size="small"
                label="API key"
                value={keyDraft}
                onChange={(event) => setKeyDraft(event.target.value)}
                autoComplete="off"
                sx={{ minWidth: { xs: '100%', sm: 280 } }}
              />
              <Button variant="contained" onClick={handleSaveKey} disabled={loading}>
                Save key
              </Button>
              <Button variant="outlined" onClick={handleClearKey} disabled={loading || (!hasSavedKey && !usingDevFallback)}>
                Clear key
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {error && <Alert severity="warning">{error}</Alert>}
      {online === false && !error && (
        <Alert severity="info">
          AgentHub is not reachable at {AGENTHUB_URL}. Start it with <code>docker compose --profile swarm up agenthub</code>.
        </Alert>
      )}

      {loading && !log.length && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 7 }}>
          <CircularProgress size={34} />
        </Box>
      )}

      {online && (
        <>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', xl: 'repeat(4, minmax(0, 1fr))' },
              gap: 2,
            }}
          >
            <StatCard label="Recent commits" value={log.length} accent="#8b5cf6" />
            <StatCard label="Frontier leaves" value={leaves.length} accent="#22d3ee" />
            <StatCard label="Channels" value={channels.length} accent="#38bdf8" />
            <StatCard label="Agents seen" value={uniqueAgents.length} accent="#a78bfa" />
          </Box>

          {uniqueAgents.length > 0 && (
            <Card>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                  Active agents
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {uniqueAgents.map((agentId) => {
                    const accent = accentFromString(agentId);
                    return (
                      <Chip
                        key={agentId}
                        size="small"
                        label={agentId}
                        sx={{
                          bgcolor: alpha(accent, 0.12),
                          color: accent,
                          border: `1px solid ${alpha(accent, 0.28)}`,
                          fontFamily: '"JetBrains Mono", monospace',
                        }}
                      />
                    );
                  })}
                </Stack>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent sx={{ p: 1.25 }}>
              <CommitDagGraph commits={log} leaves={leaves} />
            </CardContent>
          </Card>

          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', xl: 'repeat(2, minmax(0, 1fr))' },
              gap: 2,
            }}
          >
            <Card>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                  Frontier leaves
                </Typography>
                {leaves.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No commits on the frontier yet.
                  </Typography>
                ) : (
                  <Stack spacing={1.25}>
                    {leaves.map((commit) => (
                      <Box key={commit.hash} sx={{ display: 'flex', gap: 1.25, alignItems: 'flex-start' }}>
                        <Typography variant="caption" sx={{ fontFamily: '"JetBrains Mono", monospace', color: 'secondary.main', pt: 0.2, whiteSpace: 'nowrap' }}>
                          {short(commit.hash)}
                        </Typography>
                        <Box sx={{ minWidth: 0, flex: 1 }}>
                          <Typography variant="body2" noWrap>
                            {commit.message || '(no message)'}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {commit.agent_id || 'seed'} · {timeAgo(commit.created_at)}
                          </Typography>
                        </Box>
                      </Box>
                    ))}
                  </Stack>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                  Recent commits
                </Typography>
                {log.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No recent commits yet.
                  </Typography>
                ) : (
                  <Stack spacing={1} divider={<Divider />}>
                    {log.slice(0, 15).map((commit) => {
                      const accent = accentFromString(commit.agent_id);
                      return (
                        <Box key={commit.hash} sx={{ display: 'flex', gap: 1.25, alignItems: 'flex-start' }}>
                          <Typography variant="caption" sx={{ fontFamily: '"JetBrains Mono", monospace', color: accent, pt: 0.2, whiteSpace: 'nowrap' }}>
                            {short(commit.hash)}
                          </Typography>
                          <Box sx={{ minWidth: 0, flex: 1 }}>
                            <Typography variant="body2" noWrap>
                              {commit.message || '(no message)'}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              <span style={{ color: accent }}>{commit.agent_id || 'seed'}</span>
                              {' · '}
                              {timeAgo(commit.created_at)}
                              {commit.parent_hash && (
                                <span style={{ opacity: 0.58 }}>{` · ← ${short(commit.parent_hash)}`}</span>
                              )}
                            </Typography>
                          </Box>
                        </Box>
                      );
                    })}
                  </Stack>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                  Channels
                </Typography>
                {channels.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No channels registered.
                  </Typography>
                ) : (
                  <Stack spacing={1}>
                    {channels.map((channel) => (
                      <Box key={channel.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
                        <Typography variant="body2" sx={{ fontFamily: '"JetBrains Mono", monospace' }}>
                          #{channel.name}
                        </Typography>
                        {channel.description && (
                          <Typography variant="caption" color="text.secondary" noWrap>
                            {channel.description}
                          </Typography>
                        )}
                      </Box>
                    ))}
                  </Stack>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                  Recent board posts
                </Typography>
                {recentPosts.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No recent posts yet.
                  </Typography>
                ) : (
                  <Stack spacing={1} divider={<Divider />}>
                    {recentPosts.map((post) => {
                      const accent = accentFromString(post.agent_id);
                      return (
                        <Box key={post.id}>
                          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 0.4 }}>
                            <Typography variant="caption" sx={{ color: accent, fontWeight: 700 }}>
                              {post.agent_id || 'unknown'}
                            </Typography>
                            {post.parent_id && <Chip size="small" label="Reply" sx={{ height: 18, fontSize: 10 }} />}
                            <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
                              {timeAgo(post.created_at)}
                            </Typography>
                          </Box>
                          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                            {post.content.length > 220 ? `${post.content.slice(0, 220)}…` : post.content}
                          </Typography>
                        </Box>
                      );
                    })}
                  </Stack>
                )}
              </CardContent>
            </Card>
          </Box>
        </>
      )}
    </Box>
  );
};

export default AgenthubPage;
