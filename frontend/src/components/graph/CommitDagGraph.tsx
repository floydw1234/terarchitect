import React from 'react';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import {
  GRAPH_COMMIT_HEIGHT,
  GRAPH_COMMIT_WIDTH,
  GraphEmptyState,
  GraphSvgDefs,
  accentFromString,
  buildCurvedPath,
  getEdgeLabelPosition,
  graphCanvasSx,
  graphGlassPanelSx,
  graphSvgIds,
} from './graphVisuals';

export interface DagCommit {
  hash: string;
  parent_hash: string;
  agent_id: string;
  message: string;
  created_at: string;
}

interface CommitDagGraphProps {
  commits: DagCommit[];
  leaves: DagCommit[];
  title?: string;
  subtitle?: string;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyHint?: string;
}

interface CommitLayoutNode {
  commit: DagCommit;
  depth: number;
  row: number;
  x: number;
  y: number;
  hasKnownParent: boolean;
}

const COLUMN_GAP = 132;
const ROW_GAP = 136;
const PADDING_X = 68;
const PADDING_Y = 52;

function short(hash: string) {
  return hash ? hash.slice(0, 10) : '';
}

function formatTimestamp(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'unknown time';

  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

export function buildCommitDagLayout(commits: DagCommit[]) {
  const commitMap = new Map(commits.map((commit) => [commit.hash, commit]));
  const depthCache = new Map<string, number>();
  const indexMap = new Map(commits.map((commit, index) => [commit.hash, index]));

  const resolveDepth = (hash: string, trail = new Set<string>()): number => {
    if (depthCache.has(hash)) return depthCache.get(hash)!;
    if (trail.has(hash)) return 0;

    const commit = commitMap.get(hash);
    if (!commit || !commit.parent_hash || !commitMap.has(commit.parent_hash)) {
      depthCache.set(hash, 0);
      return 0;
    }

    trail.add(hash);
    const depth = resolveDepth(commit.parent_hash, trail) + 1;
    trail.delete(hash);
    depthCache.set(hash, depth);
    return depth;
  };

  const groups = new Map<number, DagCommit[]>();
  commits.forEach((commit) => {
    const depth = resolveDepth(commit.hash);
    const bucket = groups.get(depth) ?? [];
    bucket.push(commit);
    groups.set(depth, bucket);
  });

  groups.forEach((bucket) => {
    bucket.sort((a, b) => (indexMap.get(a.hash)! - indexMap.get(b.hash)!));
  });

  const nodes: CommitLayoutNode[] = [];
  const edges: Array<{ from: CommitLayoutNode; to: CommitLayoutNode }> = [];
  const nodeByHash = new Map<string, CommitLayoutNode>();

  Array.from(groups.entries())
    .sort(([a], [b]) => a - b)
    .forEach(([depth, bucket]) => {
      bucket.forEach((commit, row) => {
        const node: CommitLayoutNode = {
          commit,
          depth,
          row,
          x: PADDING_X + depth * (GRAPH_COMMIT_WIDTH + COLUMN_GAP),
          y: PADDING_Y + row * ROW_GAP,
          hasKnownParent: Boolean(commit.parent_hash && commitMap.has(commit.parent_hash)),
        };
        nodes.push(node);
        nodeByHash.set(commit.hash, node);
      });
    });

  nodes.forEach((node) => {
    const parent = node.commit.parent_hash ? nodeByHash.get(node.commit.parent_hash) : undefined;
    if (parent) {
      edges.push({ from: parent, to: node });
    }
  });

  const maxDepth = nodes.reduce((acc, node) => Math.max(acc, node.depth), 0);
  const maxRow = nodes.reduce((acc, node) => Math.max(acc, node.row), 0);

  return {
    nodes,
    edges,
    width: Math.max(
      860,
      PADDING_X * 2 + (maxDepth + 1) * GRAPH_COMMIT_WIDTH + maxDepth * COLUMN_GAP,
    ),
    height: Math.max(
      360,
      PADDING_Y * 2 + (maxRow + 1) * GRAPH_COMMIT_HEIGHT + maxRow * (ROW_GAP - GRAPH_COMMIT_HEIGHT),
    ),
  };
}

const CommitDagGraph: React.FC<CommitDagGraphProps> = ({
  commits,
  leaves,
  title = 'Commit DAG',
  subtitle = 'Parent to child lineage from the recent AgentHub log',
  emptyTitle = 'No recent commits',
  emptyDescription = 'The AgentHub DAG will appear here once commit history is available.',
  emptyHint = 'This view only needs the recent commit log and parent hashes. It does not require separate lineage endpoints.',
}) => {
  const layout = buildCommitDagLayout(commits);
  const leafHashes = new Set(leaves.map((leaf) => leaf.hash));
  const scope = 'agenthub-dag';
  const ids = graphSvgIds(scope);

  return (
    <Box
      sx={{
        ...graphCanvasSx,
        minHeight: 380,
      }}
    >
      <Box sx={{ ...graphGlassPanelSx, position: 'absolute', top: 16, left: 16, zIndex: 2, px: 2, py: 1.25 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ xs: 'flex-start', sm: 'center' }}>
          <Typography variant="subtitle2" fontWeight={700}>
            {title}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {subtitle}
          </Typography>
        </Stack>
      </Box>

      {commits.length === 0 && (
        <GraphEmptyState
          title={emptyTitle}
          description={emptyDescription}
          hint={emptyHint}
        />
      )}

      {commits.length > 0 && (
        <Box sx={{ position: 'relative', width: layout.width, height: layout.height }}>
          <svg
            width={layout.width}
            height={layout.height}
            style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
          >
            <GraphSvgDefs scope={scope} />
            {layout.edges.map(({ from, to }) => {
              const startX = from.x + GRAPH_COMMIT_WIDTH;
              const startY = from.y + GRAPH_COMMIT_HEIGHT / 2;
              const endX = to.x;
              const endY = to.y + GRAPH_COMMIT_HEIGHT / 2;
              const labelPos = getEdgeLabelPosition(startX, startY, endX, endY);

              return (
                <g key={`${from.commit.hash}-${to.commit.hash}`}>
                  <path
                    d={buildCurvedPath(startX, startY, endX, endY)}
                    stroke={`url(#${ids.edgeGradient})`}
                    strokeWidth={3}
                    fill="none"
                    opacity={0.28}
                    filter={`url(#${ids.edgeGlow})`}
                  />
                  <path
                    d={buildCurvedPath(startX, startY, endX, endY)}
                    stroke={`url(#${ids.edgeGradient})`}
                    strokeWidth={2}
                    fill="none"
                    strokeDasharray="10 12"
                    className="graph-edge-flow"
                    markerEnd={`url(#${ids.arrow})`}
                    opacity={0.92}
                  />
                  <circle cx={labelPos.x} cy={labelPos.y} r={4.5} fill="#0085fa" opacity={0.8} />
                </g>
              );
            })}
          </svg>

          {layout.nodes.map((node) => {
            const accent = accentFromString(node.commit.agent_id);
            const isLeaf = leafHashes.has(node.commit.hash);

            return (
              <Box
                key={node.commit.hash}
                data-testid={`commit-dag-node-${short(node.commit.hash)}`}
                sx={{
                  ...graphGlassPanelSx,
                  position: 'absolute',
                  left: node.x,
                  top: node.y,
                  width: GRAPH_COMMIT_WIDTH,
                  minHeight: GRAPH_COMMIT_HEIGHT,
                  p: 1.5,
                  borderColor: isLeaf ? alpha('#0085fa', 0.48) : alpha(accent, 0.28),
                  boxShadow: isLeaf
                    ? `0 4px 12px ${alpha('#0085fa', 0.12)}`
                    : `0 2px 8px ${alpha(accent, 0.08)}`,
                  overflow: 'hidden',
                }}
              >
                <Box
                  sx={{
                    position: 'absolute',
                    inset: 0,
                    background: `radial-gradient(circle at 0% 0%, ${alpha(accent, 0.18)}, transparent 36%)`,
                    pointerEvents: 'none',
                  }}
                />
                <Stack spacing={1} sx={{ position: 'relative', zIndex: 1 }}>
                  <Stack direction="row" justifyContent="space-between" spacing={1} alignItems="center">
                    <Typography
                      variant="caption"
                      sx={{ fontFamily: '"JetBrains Mono", monospace', color: accent, fontWeight: 700 }}
                    >
                      {short(node.commit.hash)}
                    </Typography>
                    <Stack direction="row" spacing={0.75}>
                      {isLeaf && (
                        <Chip
                          size="small"
                          label="Frontier"
                          sx={{
                            height: 20,
                            bgcolor: alpha('#0085fa', 0.1),
                            color: '#0085fa',
                            border: '1px solid rgba(0, 133, 250, 0.28)',
                          }}
                        />
                      )}
                      {!node.hasKnownParent && (
                        <Chip
                          size="small"
                          label="Root"
                          sx={{
                            height: 20,
                            bgcolor: '#f0f0f0',
                            color: 'text.secondary',
                          }}
                        />
                      )}
                    </Stack>
                  </Stack>
                  <Typography variant="body2" fontWeight={600} sx={{ lineHeight: 1.35 }}>
                    {node.commit.message || '(no message)'}
                  </Typography>
                  <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
                    <Chip
                      size="small"
                      label={node.commit.agent_id || 'seed'}
                      sx={{
                        height: 22,
                        bgcolor: alpha(accent, 0.14),
                        color: accent,
                        border: `1px solid ${alpha(accent, 0.28)}`,
                        fontFamily: '"JetBrains Mono", monospace',
                      }}
                    />
                    <Typography variant="caption" color="text.secondary">
                      {formatTimestamp(node.commit.created_at)}
                    </Typography>
                  </Stack>
                </Stack>
              </Box>
            );
          })}
        </Box>
      )}
    </Box>
  );
};

export default CommitDagGraph;
