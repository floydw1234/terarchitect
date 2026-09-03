import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded';
import { LineageField } from '../components/LineageField';
import CommitDagGraph from '../components/graph/CommitDagGraph';
import { accentFromString, graphGlassPanelSx } from '../components/graph/graphVisuals';
import {
  getProjectAgenthubGraph,
  getProjects,
  type Project,
  type ProjectAgenthubGraph,
} from '../utils/api';

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

const StatCard: React.FC<{ label: string; value: number; accent: string }> = ({ label, value, accent }) => (
  <Card sx={{ minHeight: 132, bgcolor: 'background.default', borderLeft: `4px solid ${accent}` }}>
    <CardContent>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.14em' }}>
        {label}
      </Typography>
      <Typography variant="h4" sx={{ mt: 1, color: accent, fontWeight: 700 }}>
        {value}
      </Typography>
    </CardContent>
  </Card>
);

function statusSeverity(code: string | undefined): 'info' | 'warning' | 'error' {
  if (code === 'agenthub_unreachable' || code === 'agenthub_not_configured') {
    return 'error';
  }
  if (code === 'agenthub_auth_required' || code === 'agenthub_http_error') {
    return 'warning';
  }
  return 'info';
}

const AgenthubPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [graphData, setGraphData] = useState<ProjectAgenthubGraph | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const loadProjects = useCallback(async () => {
    setLoadingProjects(true);
    setError(null);
    try {
      const data = await getProjects();
      setProjects(data);
      setSelectedProjectId((current) => {
        if (current && data.some((project) => project.id === current)) {
          return current;
        }
        return data[0]?.id ?? '';
      });
    } catch (e: any) {
      setProjects([]);
      setSelectedProjectId('');
      setGraphData(null);
      setError(e.message);
    } finally {
      setLoadingProjects(false);
    }
  }, []);

  const loadGraph = useCallback(async (projectId: string) => {
    setLoadingGraph(true);
    setError(null);
    try {
      const data = await getProjectAgenthubGraph(projectId);
      setGraphData(data);
      setLastRefresh(new Date());
    } catch (e: any) {
      setGraphData(null);
      setError(e.message);
    } finally {
      setLoadingGraph(false);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    if (!selectedProjectId) {
      setGraphData(null);
      return;
    }
    void loadGraph(selectedProjectId);
  }, [selectedProjectId, loadGraph]);

  const selectedProject =
    (graphData?.project?.id === selectedProjectId ? graphData.project : null) ??
    projects.find((project) => project.id === selectedProjectId) ??
    null;

  const status = graphData?.status;
  const commits = graphData?.graph.commits ?? [];
  const leaves = graphData?.graph.leaves ?? [];
  const channels = graphData?.graph.channels ?? [];
  const recentPosts = graphData?.graph.posts ?? [];
  const uniqueAgents = Array.from(
    new Set([...commits.map((commit) => commit.agent_id), ...recentPosts.map((post) => post.agent_id)].filter(Boolean)),
  );
  const loading = loadingProjects || loadingGraph;

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
              {selectedProject ? `AgentHub DAG for ${selectedProject.name}` : 'AgentHub DAG'}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1, maxWidth: 760 }}>
              Project-scoped frontier, attempt lineage, and related AgentHub channels served through the Terarchitect backend.
            </Typography>
          </Box>

          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ xs: 'stretch', sm: 'center' }}>
            <FormControl size="small" sx={{ minWidth: { xs: '100%', sm: 260 } }}>
              <InputLabel id="agenthub-project-select-label">Project</InputLabel>
              <Select
                labelId="agenthub-project-select-label"
                label="Project"
                value={selectedProjectId}
                onChange={(event) => setSelectedProjectId(event.target.value)}
                disabled={!projects.length}
              >
                {projects.map((project) => (
                  <MenuItem key={project.id} value={project.id}>
                    {project.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip
                size="small"
                label={status ? (status.online ? 'Online' : 'Offline') : loading ? 'Loading' : 'Idle'}
                color={status ? (status.online ? 'success' : 'error') : 'default'}
              />
              <Chip size="small" label="Project-scoped" color="info" variant="outlined" />
              {lastRefresh && (
                <Chip
                  size="small"
                  label={`Refreshed ${timeAgo(lastRefresh.toISOString())}`}
                  sx={{ bgcolor: 'info.light' }}
                />
              )}
              <Tooltip title="Refresh project DAG">
                <span>
                  <IconButton
                    onClick={() => {
                      if (selectedProjectId) {
                        void loadGraph(selectedProjectId);
                      } else {
                        void loadProjects();
                      }
                    }}
                    disabled={loading}
                    size="small"
                  >
                    <RefreshRoundedIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </Stack>
          </Stack>
        </Stack>
      </Box>

      <Card>
        <CardContent>
          <Stack direction={{ xs: 'column', lg: 'row' }} spacing={2} alignItems={{ xs: 'stretch', lg: 'center' }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle1">Backend AgentHub Auth</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                The browser never stores or sends an AgentHub API key. Terarchitect reads AgentHub from the backend using
                <code> AGENTHUB_API_KEY </code>
                when configured, or no auth header for local read-only dev bypass.
              </Typography>
            </Box>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Chip
                size="small"
                label={status?.auth_mode === 'backend_api_key' ? 'Backend key configured' : 'No backend key'}
                color={status?.auth_mode === 'backend_api_key' ? 'success' : 'default'}
                variant={status?.auth_mode === 'backend_api_key' ? 'filled' : 'outlined'}
              />
              {status?.guidance && <Chip size="small" label="Backend action needed" color="warning" variant="outlined" />}
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {error && <Alert severity="warning">{error}</Alert>}
      {!error && status?.message && status.code !== 'ok' && (
        <Alert severity={statusSeverity(status.code)}>
          {status.message}
          {status.guidance ? ` ${status.guidance}` : ''}
        </Alert>
      )}
      {!loading && !projects.length && (
        <Alert severity="info">Create a project first. This page shows one AgentHub DAG per Terarchitect project.</Alert>
      )}

      {loading && !graphData && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 7 }}>
          <CircularProgress size={34} />
        </Box>
      )}

      {selectedProject && graphData && (
        <>
          <Card>
            <CardContent>
              <Typography variant="subtitle1" sx={{ mb: 1.5 }}>
                Project Frontier
              </Typography>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} flexWrap="wrap" useFlexGap>
                <LineageField label="Accepted frontier" value={selectedProject.accepted_frontier_id} width={16} />
                <LineageField label="Shipped frontier" value={selectedProject.shipped_frontier} width={16} />
                <LineageField label="Source SHA" value={selectedProject.github_resolved_sha} width={16} />
              </Stack>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
                <Chip size="small" label={`${graphData.scope.anchor_hashes.length} anchor hashes`} variant="outlined" />
                <Chip size="small" label={`${graphData.scope.attempt_hashes.length} attempt leaves`} variant="outlined" />
                <Chip size="small" label={`${graphData.graph.root_hashes.length} scoped roots`} variant="outlined" />
              </Stack>
            </CardContent>
          </Card>

          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', xl: 'repeat(4, minmax(0, 1fr))' },
              gap: 2,
            }}
          >
            <StatCard label="Scoped commits" value={commits.length} accent="#0085FA" />
            <StatCard label="Project frontier" value={leaves.length} accent="#0085FA" />
            <StatCard label="Channels" value={channels.length} accent="#45C3F8" />
            <StatCard label="Agents seen" value={uniqueAgents.length} accent="#0085FA" />
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
              <CommitDagGraph
                commits={commits}
                leaves={leaves}
                title={selectedProject ? `Commit DAG · ${selectedProject.name}` : 'Commit DAG'}
                subtitle="Only commits related to this project's frontier, source SHA, and ticket attempts"
                emptyTitle="No project-scoped commits yet"
                emptyDescription="This project has no visible AgentHub lineage in the backend-scoped DAG yet."
                emptyHint="Accept a frontier, import a source SHA, or publish an attempt to anchor the graph."
              />
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
                  Project frontier leaves
                </Typography>
                {leaves.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No project frontier leaves are visible yet.
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
                  Scoped commits
                </Typography>
                {commits.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No project-scoped commits yet.
                  </Typography>
                ) : (
                  <Stack spacing={1} divider={<Divider />}>
                    {commits.slice(0, 15).map((commit) => {
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
                              {commit.parent_hash && <span style={{ opacity: 0.58 }}>{` · ← ${short(commit.parent_hash)}`}</span>}
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
                  Project channels
                </Typography>
                {channels.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No project-related channels were found in AgentHub.
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
                  Recent project posts
                </Typography>
                {recentPosts.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No recent project posts yet.
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
                            {post.channel_name && <Chip size="small" label={`#${post.channel_name}`} sx={{ height: 18, fontSize: 10 }} />}
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
