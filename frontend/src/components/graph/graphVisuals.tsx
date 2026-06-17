import React from 'react';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';

export const GRAPH_NODE_WIDTH = 188;
export const GRAPH_NODE_HEIGHT = 108;
export const GRAPH_COMMIT_WIDTH = 228;
export const GRAPH_COMMIT_HEIGHT = 110;

const ACCENT_SEQUENCE = [
  '#8b5cf6',
  '#22d3ee',
  '#6366f1',
  '#38bdf8',
  '#a78bfa',
  '#2dd4bf',
  '#f472b6',
];

export function accentFromString(value?: string) {
  if (!value) return '#94a3b8';

  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }

  return ACCENT_SEQUENCE[hash % ACCENT_SEQUENCE.length];
}

export function getArchitectureNodeAppearance(type: string) {
  switch (type) {
    case 'database':
      return { label: 'Database', accent: '#22d3ee', surface: '#0c1820', icon: 'DB' };
    case 'cache':
      return { label: 'Cache', accent: '#2dd4bf', surface: '#0d1b18', icon: 'CA' };
    case 'queue':
      return { label: 'Queue', accent: '#f59e0b', surface: '#1a1308', icon: 'QU' };
    case 'api':
      return { label: 'API', accent: '#60a5fa', surface: '#0c1424', icon: 'AP' };
    case 'worker':
      return { label: 'Worker', accent: '#f472b6', surface: '#1a0f1a', icon: 'WK' };
    case 'view':
      return { label: 'View', accent: '#38bdf8', surface: '#0b1720', icon: 'VW' };
    case 'frontend':
      return { label: 'Frontend', accent: '#a78bfa', surface: '#130f20', icon: 'FE' };
    case 'service':
    default:
      return { label: 'Service', accent: '#8b5cf6', surface: '#120f20', icon: 'SV' };
  }
}

export function buildCurvedPath(startX: number, startY: number, endX: number, endY: number) {
  const deltaX = endX - startX;
  const pull = Math.max(90, Math.abs(deltaX) * 0.45);
  const c1x = startX + pull;
  const c2x = endX - pull;

  return `M ${startX} ${startY} C ${c1x} ${startY}, ${c2x} ${endY}, ${endX} ${endY}`;
}

function bezierPoint(
  startX: number,
  startY: number,
  endX: number,
  endY: number,
  t: number,
) {
  const deltaX = endX - startX;
  const pull = Math.max(90, Math.abs(deltaX) * 0.45);
  const p0 = { x: startX, y: startY };
  const p1 = { x: startX + pull, y: startY };
  const p2 = { x: endX - pull, y: endY };
  const p3 = { x: endX, y: endY };
  const inv = 1 - t;

  return {
    x:
      inv * inv * inv * p0.x +
      3 * inv * inv * t * p1.x +
      3 * inv * t * t * p2.x +
      t * t * t * p3.x,
    y:
      inv * inv * inv * p0.y +
      3 * inv * inv * t * p1.y +
      3 * inv * t * t * p2.y +
      t * t * t * p3.y,
  };
}

export function getEdgeLabelPosition(
  startX: number,
  startY: number,
  endX: number,
  endY: number,
) {
  return bezierPoint(startX, startY, endX, endY, 0.5);
}

export function graphSvgIds(scope: string) {
  return {
    arrow: `${scope}-arrow`,
    edgeGradient: `${scope}-edge-gradient`,
    edgeGlow: `${scope}-edge-glow`,
  };
}

export const graphGlassPanelSx = {
  position: 'relative',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  background:
    'linear-gradient(180deg, rgba(18, 20, 27, 0.86) 0%, rgba(10, 12, 17, 0.78) 100%)',
  backdropFilter: 'blur(24px)',
  boxShadow:
    '0 24px 80px rgba(0, 0, 0, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.05)',
} as const;

export const graphCanvasSx = {
  position: 'relative',
  overflow: 'auto',
  borderRadius: 4,
  border: '1px solid rgba(255, 255, 255, 0.07)',
  backgroundColor: '#08090a',
  backgroundImage: `
    radial-gradient(circle at 18% 12%, rgba(99, 102, 241, 0.20), transparent 26%),
    radial-gradient(circle at 82% 18%, rgba(34, 211, 238, 0.16), transparent 22%),
    linear-gradient(rgba(255, 255, 255, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.045) 1px, transparent 1px)
  `,
  backgroundSize: 'auto, auto, 40px 40px, 40px 40px',
  backgroundPosition: '0 0, 0 0, -1px -1px, -1px -1px',
} as const;

export const GraphSvgDefs: React.FC<{ scope: string }> = ({ scope }) => {
  const ids = graphSvgIds(scope);

  return (
    <defs>
      <linearGradient id={ids.edgeGradient} x1="0%" x2="100%" y1="0%" y2="0%">
        <stop offset="0%" stopColor="#8b5cf6" />
        <stop offset="50%" stopColor="#6366f1" />
        <stop offset="100%" stopColor="#22d3ee" />
      </linearGradient>
      <filter id={ids.edgeGlow} x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
      <marker
        id={ids.arrow}
        markerWidth="14"
        markerHeight="14"
        refX="11"
        refY="6"
        orient="auto"
        markerUnits="strokeWidth"
      >
        <path d="M 0 0 L 12 6 L 0 12 z" fill="#8ddcff" opacity="0.95" />
      </marker>
    </defs>
  );
};

interface GraphEmptyStateProps {
  title: string;
  description: string;
  hint?: string;
  action?: React.ReactNode;
}

export const GraphEmptyState: React.FC<GraphEmptyStateProps> = ({
  title,
  description,
  hint,
  action,
}) => (
  <Box
    sx={{
      position: 'absolute',
      inset: 0,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      p: 3,
    }}
  >
    <Box
      sx={{
        ...graphGlassPanelSx,
        maxWidth: 420,
        px: 3,
        py: 3.5,
        textAlign: 'center',
      }}
    >
      <Stack spacing={1.25} alignItems="center">
        <Chip
          size="small"
          label="Graph Surface"
          sx={{
            bgcolor: alpha('#8b5cf6', 0.14),
            color: '#c4b5fd',
            border: '1px solid rgba(139, 92, 246, 0.3)',
          }}
        />
        <Typography variant="h6" fontWeight={700}>
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {description}
        </Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary" sx={{ maxWidth: 320 }}>
            {hint}
          </Typography>
        )}
        {action}
      </Stack>
    </Box>
  </Box>
);
