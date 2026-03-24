import React, { useEffect, useState, useCallback } from 'react';
import {
  Box, Typography, Card, CardContent, Chip, CircularProgress,
  Alert, IconButton, Tooltip, Divider, Stack,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { AGENTHUB_URL } from '../utils/api';

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
  const resp = await fetch(AGENTHUB_URL + path, {
    headers: { Authorization: `Bearer ${(window as any).__AH_KEY__ ?? ''}` },
  });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

const AgenthubPage: React.FC = () => {
  const [online, setOnline] = useState<boolean | null>(null);
  const [leaves, setLeaves] = useState<Commit[]>([]);
  const [log, setLog] = useState<Commit[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [recentPosts, setRecentPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Health check
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

      // Fetch recent posts from each channel (up to 5 channels, 5 posts each)
      const posts: Post[] = [];
      for (const ch of (channelsData ?? []).slice(0, 5)) {
        try {
          const p = await ahGet(`/api/channels/${ch.name}/posts?limit=5`);
          posts.push(...(p ?? []));
        } catch {}
      }
      posts.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      setRecentPosts(posts.slice(0, 20));

      setLastRefresh(new Date());
    } catch (e: any) {
      setOnline(false);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const agentColors: Record<string, string> = {};
  const palette = ['#22d3ee', '#a78bfa', '#34d399', '#f59e0b', '#f87171', '#60a5fa'];
  let colorIdx = 0;
  function agentColor(id: string) {
    if (!id) return '#94a3b8';
    if (!agentColors[id]) agentColors[id] = palette[colorIdx++ % palette.length];
    return agentColors[id];
  }

  const uniqueAgents = Array.from(new Set([...log.map(c => c.agent_id), ...recentPosts.map(p => p.agent_id)].filter(Boolean)));

  return (
    <Box sx={{ maxWidth: 1100, mx: 'auto' }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 3, gap: 2 }}>
        <Typography variant="h5" fontWeight={700}>AgentHub</Typography>
        <Chip
          size="small"
          label={online === null ? 'checking…' : online ? 'online' : 'offline'}
          color={online ? 'success' : online === false ? 'error' : 'default'}
        />
        {lastRefresh && (
          <Typography variant="caption" color="text.secondary">
            refreshed {timeAgo(lastRefresh.toISOString())}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Refresh">
          <span>
            <IconButton onClick={load} disabled={loading} size="small">
              <RefreshIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}
      {online === false && !error && (
        <Alert severity="info" sx={{ mb: 2 }}>
          AgentHub is not reachable at {AGENTHUB_URL}. Start it with{' '}
          <code>docker compose --profile swarm up agenthub</code>.
        </Alert>
      )}

      {loading && !log.length && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress size={32} />
        </Box>
      )}

      {online && (
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>

          {/* Stats row */}
          <Card sx={{ gridColumn: '1 / -1' }}>
            <CardContent>
              <Stack direction="row" spacing={4}>
                <Box>
                  <Typography variant="h4" fontWeight={700} color="primary">{log.length}</Typography>
                  <Typography variant="caption" color="text.secondary">commits (last 30)</Typography>
                </Box>
                <Box>
                  <Typography variant="h4" fontWeight={700} color="primary">{leaves.length}</Typography>
                  <Typography variant="caption" color="text.secondary">leaves (frontier)</Typography>
                </Box>
                <Box>
                  <Typography variant="h4" fontWeight={700} color="primary">{channels.length}</Typography>
                  <Typography variant="caption" color="text.secondary">channels</Typography>
                </Box>
                <Box>
                  <Typography variant="h4" fontWeight={700} color="primary">{uniqueAgents.length}</Typography>
                  <Typography variant="caption" color="text.secondary">agents seen</Typography>
                </Box>
              </Stack>
              {uniqueAgents.length > 0 && (
                <Box sx={{ mt: 2, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  {uniqueAgents.map(id => (
                    <Chip key={id} size="small" label={id}
                      sx={{ bgcolor: agentColor(id) + '22', color: agentColor(id), border: `1px solid ${agentColor(id)}44` }} />
                  ))}
                </Box>
              )}
            </CardContent>
          </Card>

          {/* Frontier / leaves */}
          <Card>
            <CardContent>
              <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
                Frontier — current leaves
              </Typography>
              {leaves.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No commits yet.</Typography>
              ) : (
                <Stack spacing={1}>
                  {leaves.map(c => (
                    <Box key={c.hash} sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
                      <Typography variant="caption" sx={{ fontFamily: 'monospace', color: 'primary.main', pt: 0.3, whiteSpace: 'nowrap' }}>
                        {short(c.hash)}
                      </Typography>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography variant="body2" noWrap>{c.message || '(no message)'}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {c.agent_id || '(seed)'} · {timeAgo(c.created_at)}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>

          {/* Recent commits */}
          <Card>
            <CardContent>
              <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
                Recent commits
              </Typography>
              {log.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No commits yet.</Typography>
              ) : (
                <Stack spacing={1} divider={<Divider />}>
                  {log.slice(0, 15).map(c => (
                    <Box key={c.hash} sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
                      <Typography variant="caption" sx={{ fontFamily: 'monospace', color: 'primary.main', pt: 0.3, whiteSpace: 'nowrap' }}>
                        {short(c.hash)}
                      </Typography>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography variant="body2" noWrap>{c.message || '(no message)'}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          <span style={{ color: agentColor(c.agent_id) }}>{c.agent_id || '(seed)'}</span>
                          {' '}· {timeAgo(c.created_at)}
                          {c.parent_hash && (
                            <span style={{ opacity: 0.5 }}> · ← {short(c.parent_hash)}</span>
                          )}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>

          {/* Channels */}
          <Card>
            <CardContent>
              <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
                Channels
              </Typography>
              {channels.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No channels yet.</Typography>
              ) : (
                <Stack spacing={0.5}>
                  {channels.map(ch => (
                    <Box key={ch.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>#{ch.name}</Typography>
                      {ch.description && (
                        <Typography variant="caption" color="text.secondary">— {ch.description}</Typography>
                      )}
                    </Box>
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>

          {/* Recent board posts */}
          <Card>
            <CardContent>
              <Typography variant="subtitle1" fontWeight={600} mb={1.5}>
                Recent board posts
              </Typography>
              {recentPosts.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No posts yet.</Typography>
              ) : (
                <Stack spacing={1} divider={<Divider />}>
                  {recentPosts.map(p => (
                    <Box key={p.id}>
                      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 0.25 }}>
                        <Typography variant="caption" sx={{ color: agentColor(p.agent_id), fontWeight: 600 }}>
                          {p.agent_id}
                        </Typography>
                        {p.parent_id && (
                          <Chip size="small" label="reply" sx={{ height: 16, fontSize: 10 }} />
                        )}
                        <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
                          {timeAgo(p.created_at)}
                        </Typography>
                      </Box>
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                        {p.content.length > 200 ? p.content.slice(0, 200) + '…' : p.content}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>

        </Box>
      )}
    </Box>
  );
};

export default AgenthubPage;
