import React from 'react';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';

export const GRAPH_NODE_WIDTH = 188;
export const GRAPH_NODE_HEIGHT = 108;
export const GRAPH_COMMIT_WIDTH = 228;
export const GRAPH_COMMIT_HEIGHT = 110;

const ACCENT_SEQUENCE = [
  '#0085fa',
  '#4169e1',
  '#10b981',
  '#f59e0b',
  '#6366f1',
  '#0ea5e9',
  '#ec4899',
];

export function accentFromString(value?: string) {
  if (!value) return '#6b7280';

  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }

  return ACCENT_SEQUENCE[hash % ACCENT_SEQUENCE.length];
}

export function getArchitectureNodeAppearance(type: string) {
  switch (type) {
    case 'database':
      return { label: 'Database', accent: '#0085fa', surface: '#e9f7ff', icon: 'DB' };
    case 'cache':
      return { label: 'Cache', accent: '#10b981', surface: '#ecfdf5', icon: 'CA' };
    case 'queue':
      return { label: 'Queue', accent: '#f59e0b', surface: '#fffbeb', icon: 'QU' };
    case 'api':
      return { label: 'API', accent: '#4169e1', surface: '#eef2ff', icon: 'AP' };
    case 'worker':
      return { label: 'Worker', accent: '#ec4899', surface: '#fdf2f8', icon: 'WK' };
    case 'view':
      return { label: 'View', accent: '#0ea5e9', surface: '#f0f9ff', icon: 'VW' };
    case 'frontend':
      return { label: 'Frontend', accent: '#6366f1', surface: '#eef2ff', icon: 'FE' };
    case 'service':
    default:
      return { label: 'Service', accent: '#0085fa', surface: '#e9f7ff', icon: 'SV' };
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
  border: '1px solid #e0e0e0',
  background: '#ffffff',
  borderRadius: 2,
  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.08)',
} as const;

export const graphCanvasSx = {
  position: 'relative',
  overflow: 'auto',
  borderRadius: 2,
  border: '1px solid #e0e0e0',
  backgroundColor: '#f5f9ff',
  backgroundImage: `
    linear-gradient(rgba(0, 133, 250, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 133, 250, 0.05) 1px, transparent 1px)
  `,
  backgroundSize: '40px 40px, 40px 40px',
  backgroundPosition: '-1px -1px, -1px -1px',
} as const;

export const GraphSvgDefs: React.FC<{ scope: string }> = ({ scope }) => {
  const ids = graphSvgIds(scope);

  return (
    <defs>
      <linearGradient id={ids.edgeGradient} x1="0%" x2="100%" y1="0%" y2="0%">
        <stop offset="0%" stopColor="#0085fa" />
        <stop offset="50%" stopColor="#4169e1" />
        <stop offset="100%" stopColor="#0085fa" />
      </linearGradient>
      <filter id={ids.edgeGlow} x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="1" result="blur" />
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
        <path d="M 0 0 L 12 6 L 0 12 z" fill="#0085fa" opacity="0.95" />
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
            bgcolor: alpha('#0085fa', 0.1),
            color: '#0085fa',
            border: '1px solid rgba(0, 133, 250, 0.3)',
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
