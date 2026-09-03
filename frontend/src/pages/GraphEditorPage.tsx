import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import AutoAwesomeRoundedIcon from '@mui/icons-material/AutoAwesomeRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import DeviceHubRoundedIcon from '@mui/icons-material/DeviceHubRounded';
import SaveRoundedIcon from '@mui/icons-material/SaveRounded';
import AddCircleOutlineRoundedIcon from '@mui/icons-material/AddCircleOutlineRounded';
import DashboardRoundedIcon from '@mui/icons-material/DashboardRounded';
import { getGraph, updateGraph, generateGraph } from '../utils/api';
import {
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  GraphEmptyState,
  GraphSvgDefs,
  buildCurvedPath,
  getArchitectureNodeAppearance,
  getEdgeLabelPosition,
  graphCanvasSx,
  graphGlassPanelSx,
  graphSvgIds,
} from '../components/graph/graphVisuals';

interface NodeData {
  label: string;
  description: string;
  tech: string[];
  ports: string[];
  security: string[];
}

interface GraphNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: NodeData;
}

interface EdgeData {
  label?: string;
  protocol?: string;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  data?: EdgeData;
}

interface NoticeState {
  severity: 'success' | 'info' | 'warning' | 'error';
  message: string;
}

function normalizeNode(n: Partial<GraphNode>): GraphNode {
  const data = (n.data ?? {}) as Partial<NodeData>;
  return {
    id: n.id ?? `node-${Date.now()}`,
    type: n.type ?? 'service',
    position: n.position ?? { x: 0, y: 0 },
    data: {
      label: data.label ?? 'Service',
      description: typeof data.description === 'string' ? data.description : '',
      tech: Array.isArray(data.tech) ? data.tech : [],
      ports: Array.isArray(data.ports) ? data.ports : [],
      security: Array.isArray(data.security) ? data.security : [],
    },
  };
}

function normalizeEdge(e: Partial<GraphEdge>): GraphEdge {
  return {
    id: e.id ?? `edge-${Date.now()}`,
    source: e.source ?? '',
    target: e.target ?? '',
    data: e.data ? { label: e.data.label, protocol: e.data.protocol } : {},
  };
}

const NODE_TYPES = ['service', 'database', 'cache', 'queue', 'api', 'worker', 'view', 'frontend'] as const;

function getNodePos(node: GraphNode) {
  return node.position;
}

function summarizeNodeMeta(node: GraphNode) {
  const parts = [
    ...node.data.tech.slice(0, 2),
    ...node.data.ports.slice(0, 1).map((port) => `:${port}`),
    ...node.data.security.slice(0, 1),
  ].filter(Boolean);

  return parts.slice(0, 3);
}

const GraphEditorPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null);
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const dragRef = useRef<{
    nodeId: string;
    startX: number;
    startY: number;
    startPos: { x: number; y: number };
  } | null>(null);
  const setNodesRef = useRef(setNodes);
  setNodesRef.current = setNodes;

  const fetchGraph = useCallback(async () => {
    if (!projectId) return;

    try {
      const data = await getGraph(projectId);
      setNodes(
        Array.isArray(data.nodes)
          ? (data.nodes as Partial<GraphNode>[]).map(normalizeNode)
          : [],
      );
      setEdges(
        Array.isArray(data.edges)
          ? (data.edges as Partial<GraphEdge>[]).map(normalizeEdge)
          : [],
      );
    } catch (error) {
      console.error('Failed to fetch graph:', error);
      setNotice({ severity: 'error', message: 'Failed to load the architecture graph.' });
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      fetchGraph();
    }
  }, [projectId, fetchGraph]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;

      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      setNodesRef.current((prev: GraphNode[]) =>
        prev.map((node) =>
          node.id === drag.nodeId
            ? {
                ...node,
                position: {
                  x: drag.startPos.x + dx,
                  y: drag.startPos.y + dy,
                },
              }
            : node,
        ),
      );
    };

    const onUp = () => {
      dragRef.current = null;
      setDraggingNodeId(null);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);

    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  const handleNodeMouseDown = (e: React.MouseEvent, nodeId: string) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.button !== 0) return;

    if (e.shiftKey) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(nodeId)) next.delete(nodeId);
        else next.add(nodeId);
        return next;
      });
      return;
    }

    const node = nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return;

    setSelectedIds(new Set([nodeId]));
    setEditingNodeId(null);
    setEditingEdgeId(null);
    setDraggingNodeId(nodeId);
    dragRef.current = {
      nodeId,
      startX: e.clientX,
      startY: e.clientY,
      startPos: { ...node.position },
    };
  };

  const handlePaneClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget || e.target instanceof SVGSVGElement) {
      setSelectedIds(new Set());
      setEditingNodeId(null);
      setEditingEdgeId(null);
    }
  };

  const handleAddNode = () => {
    const column = nodes.length % 4;
    const row = Math.floor(nodes.length / 4);
    const id = `node-${Date.now()}`;

    setNodes((prev) => [
      ...prev,
      {
        id,
        type: 'service',
        position: { x: 96 + column * 236, y: 116 + row * 156 },
        data: {
          label: `Service ${prev.length + 1}`,
          description: '',
          tech: [],
          ports: [],
          security: [],
        },
      },
    ]);
    setSelectedIds(new Set([id]));
    setNotice({ severity: 'success', message: 'Node added. Drag it into position or double-click to edit it.' });
  };

  const handleConnectSelected = () => {
    if (selectedIds.size !== 2) {
      setNotice({
        severity: 'info',
        message: 'Select exactly two nodes with Shift+click to create a connection.',
      });
      return;
    }

    const [source, target] = Array.from(selectedIds);
    setEdges((prev) => [
      ...prev,
      normalizeEdge({ id: `edge-${Date.now()}`, source, target }),
    ]);
    setSelectedIds(new Set());
    setNotice({ severity: 'success', message: 'Edge created. Double-click the connection to add a label or protocol.' });
  };

  const handleDeleteSelected = () => {
    if (selectedIds.size === 0) {
      setNotice({ severity: 'info', message: 'Select one or more nodes to remove them from the graph.' });
      return;
    }

    const toRemove = Array.from(selectedIds);
    setNodes((prev) => prev.filter((node) => !toRemove.includes(node.id)));
    setEdges((prev) =>
      prev.filter((edge) => !toRemove.includes(edge.source) && !toRemove.includes(edge.target)),
    );
    setSelectedIds(new Set());
    setNotice({ severity: 'success', message: 'Selected nodes and attached edges removed.' });
  };

  const handleNodeDoubleClick = (e: React.MouseEvent, nodeId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setEditingEdgeId(null);
    setEditingNodeId(nodeId);
  };

  const handleUpdateNode = (nodeId: string, updates: Partial<NodeData> & { type?: string }) => {
    setNodes((prev) =>
      prev.map((node) => {
        if (node.id !== nodeId) return node;

        const { type: nextType, ...dataUpdates } = updates;
        const nextNode = { ...node, data: { ...node.data, ...dataUpdates } };
        if (nextType !== undefined) {
          nextNode.type = nextType;
        }
        return nextNode;
      }),
    );
  };

  const handleUpdateEdge = (edgeId: string, updates: Partial<EdgeData>) => {
    setEdges((prev) =>
      prev.map((edge) =>
        edge.id === edgeId
          ? { ...edge, data: { ...edge.data, ...updates } }
          : edge,
      ),
    );
  };

  const handleSaveGraph = async () => {
    if (!projectId) return;

    try {
      const cleanedNodes = nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          tech: node.data.tech.filter(Boolean),
          ports: node.data.ports.filter(Boolean),
          security: node.data.security.filter(Boolean),
        },
      }));

      await updateGraph(projectId, { nodes: cleanedNodes, edges });
      setNotice({ severity: 'success', message: 'Graph saved successfully.' });
    } catch (error) {
      console.error('Failed to save graph:', error);
      setNotice({ severity: 'error', message: 'Failed to save the graph.' });
    }
  };

  const handleGenerateGraph = async () => {
    if (!projectId) return;

    setGenerating(true);
    setGenerateError(null);
    try {
      const result = await generateGraph(projectId);
      setNodes(
        Array.isArray(result.nodes)
          ? (result.nodes as Partial<GraphNode>[]).map(normalizeNode)
          : [],
      );
      setEdges(
        Array.isArray(result.edges)
          ? (result.edges as Partial<GraphEdge>[]).map(normalizeEdge)
          : [],
      );
      setNotice({ severity: 'success', message: 'Generated a fresh graph from the repository.' });
    } catch (error: any) {
      const message = error?.message || 'Failed to generate graph';
      setGenerateError(message);
      console.error('Failed to generate graph:', error);
    } finally {
      setGenerating(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ minHeight: 420, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Stack spacing={1.5} alignItems="center">
          <CircularProgress size={32} />
          <Typography variant="body2" color="text.secondary">
            Loading architecture graph…
          </Typography>
        </Stack>
      </Box>
    );
  }

  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const canvasWidth = Math.max(
    1240,
    ...nodes.map((node) => node.position.x + GRAPH_NODE_WIDTH + 120),
  );
  const canvasHeight = Math.max(
    760,
    ...nodes.map((node) => node.position.y + GRAPH_NODE_HEIGHT + 160),
  );
  const scope = 'architecture-editor';
  const svgIds = graphSvgIds(scope);

  return (
    <Box sx={{ minHeight: 'calc(100vh - 130px)', display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Paper sx={{ ...graphGlassPanelSx, p: { xs: 2, md: 2.5 } }}>
        <Stack
          direction={{ xs: 'column', xl: 'row' }}
          spacing={2}
          alignItems={{ xs: 'flex-start', xl: 'center' }}
          justifyContent="space-between"
        >
          <Box>
            <Typography variant="overline" color="secondary.main">
              Architecture Workspace
            </Typography>
            <Typography variant="h4" sx={{ mt: 0.5 }}>
              Graph Editor
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1, maxWidth: 720 }}>
              Drag services into place, model interfaces with curved links, and keep the saved graph schema unchanged for Kanban and repo generation flows.
            </Typography>
          </Box>

          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} flexWrap="wrap" useFlexGap>
            <Chip label={`${nodes.length} nodes`} />
            <Chip label={`${edges.length} edges`} />
            <Chip label={`${selectedIds.size} selected`} color={selectedIds.size ? 'primary' : 'default'} />
          </Stack>
        </Stack>

        <Stack
          direction={{ xs: 'column', md: 'row' }}
          spacing={1}
          useFlexGap
          flexWrap="wrap"
          sx={{ mt: 2.25 }}
        >
          <Button component={Link} to={`/projects/${projectId}/kanban`} variant="outlined" startIcon={<DashboardRoundedIcon />}>
            Kanban Board
          </Button>
          {nodes.length === 0 && (
            <Button
              variant="contained"
              color="secondary"
              onClick={handleGenerateGraph}
              disabled={generating}
              startIcon={<AutoAwesomeRoundedIcon />}
            >
              {generating ? 'Generating…' : 'Generate from Repo'}
            </Button>
          )}
          <Button variant="contained" onClick={handleAddNode} startIcon={<AddCircleOutlineRoundedIcon />}>
            Add Node
          </Button>
          <Button variant="outlined" onClick={handleConnectSelected} startIcon={<DeviceHubRoundedIcon />}>
            Connect Selected
          </Button>
          <Button variant="outlined" color="error" onClick={handleDeleteSelected} startIcon={<DeleteOutlineRoundedIcon />}>
            Delete Selected
          </Button>
          <Button variant="contained" color="primary" onClick={handleSaveGraph} startIcon={<SaveRoundedIcon />}>
            Save Graph
          </Button>
        </Stack>
      </Paper>

      {notice && (
        <Alert severity={notice.severity} onClose={() => setNotice(null)}>
          {notice.message}
        </Alert>
      )}

      {generateError && <Alert severity="error">{generateError}</Alert>}

      <Paper
        sx={{
          ...graphGlassPanelSx,
          overflow: 'hidden',
          p: 1.25,
          flex: 1,
          minHeight: 560,
        }}
      >
        <Box
          sx={{
            ...graphCanvasSx,
            height: '100%',
            minHeight: 520,
            cursor: draggingNodeId ? 'grabbing' : 'default',
          }}
        >
          <Box sx={{ ...graphGlassPanelSx, position: 'absolute', top: 16, left: 16, zIndex: 3, px: 2, py: 1.25 }}>
            <Stack spacing={0.5}>
              <Typography variant="subtitle2">Interaction guide</Typography>
              <Typography variant="caption" color="text.secondary">
                Drag to reposition. Double-click nodes or links to edit. Shift+click adds to the selection set.
              </Typography>
            </Stack>
          </Box>

          <Box sx={{ ...graphGlassPanelSx, position: 'absolute', top: 16, right: 16, zIndex: 3, px: 2, py: 1.25 }}>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              {NODE_TYPES.map((type) => {
                const appearance = getArchitectureNodeAppearance(type);
                return (
                  <Chip
                    key={type}
                    size="small"
                    label={appearance.label}
                    sx={{
                      bgcolor: alpha(appearance.accent, 0.12),
                      color: appearance.accent,
                      border: `1px solid ${alpha(appearance.accent, 0.24)}`,
                    }}
                  />
                );
              })}
            </Stack>
          </Box>

          {generating && (
            <Box
              sx={{
                ...graphGlassPanelSx,
                position: 'absolute',
                left: 16,
                right: 16,
                bottom: 16,
                zIndex: 3,
                px: 2,
                py: 1.25,
              }}
            >
              <Typography variant="body2" color="text.secondary">
                Cloning the repo and analyzing it with the architecture generator. This can take a few minutes.
              </Typography>
            </Box>
          )}

          <Box
            sx={{ position: 'relative', width: canvasWidth, height: canvasHeight }}
            onMouseDown={handlePaneClick}
          >
            <svg
              width={canvasWidth}
              height={canvasHeight}
              style={{ position: 'absolute', inset: 0, pointerEvents: 'auto' }}
            >
              <GraphSvgDefs scope={scope} />
              {edges.map((edge) => {
                const source = nodeMap.get(edge.source);
                const target = nodeMap.get(edge.target);
                if (!source || !target) return null;

                const startX = getNodePos(source).x + GRAPH_NODE_WIDTH;
                const startY = getNodePos(source).y + GRAPH_NODE_HEIGHT / 2;
                const endX = getNodePos(target).x;
                const endY = getNodePos(target).y + GRAPH_NODE_HEIGHT / 2;

                return (
                  <g
                    key={edge.id}
                    onDoubleClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      setEditingNodeId(null);
                      setEditingEdgeId(edge.id);
                    }}
                    style={{ cursor: 'pointer' }}
                  >
                    <path
                      d={buildCurvedPath(startX, startY, endX, endY)}
                      stroke={`url(#${svgIds.edgeGradient})`}
                      strokeWidth={6}
                      fill="none"
                      opacity={0.16}
                      filter={`url(#${svgIds.edgeGlow})`}
                    />
                    <path
                      d={buildCurvedPath(startX, startY, endX, endY)}
                      stroke={`url(#${svgIds.edgeGradient})`}
                      strokeWidth={2.5}
                      fill="none"
                      markerEnd={`url(#${svgIds.arrow})`}
                      opacity={0.96}
                    />
                    <path
                      d={buildCurvedPath(startX, startY, endX, endY)}
                      stroke="#0085FA"
                      strokeWidth={1.1}
                      fill="none"
                      strokeDasharray="10 12"
                      className="graph-edge-flow"
                      opacity={0.36}
                    />
                    <path
                      d={buildCurvedPath(startX, startY, endX, endY)}
                      stroke="transparent"
                      strokeWidth={18}
                      fill="none"
                    />
                  </g>
                );
              })}
            </svg>

            {edges.map((edge) => {
              const source = nodeMap.get(edge.source);
              const target = nodeMap.get(edge.target);
              if (!source || !target) return null;
              if (!edge.data?.label && !edge.data?.protocol) return null;

              const startX = getNodePos(source).x + GRAPH_NODE_WIDTH;
              const startY = getNodePos(source).y + GRAPH_NODE_HEIGHT / 2;
              const endX = getNodePos(target).x;
              const endY = getNodePos(target).y + GRAPH_NODE_HEIGHT / 2;
              const labelPos = getEdgeLabelPosition(startX, startY, endX, endY);

              return (
                <Box
                  key={`${edge.id}-label`}
                  onDoubleClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setEditingNodeId(null);
                    setEditingEdgeId(edge.id);
                  }}
                  sx={{
                    ...graphGlassPanelSx,
                    position: 'absolute',
                    left: labelPos.x - 88,
                    top: labelPos.y - 18,
                    zIndex: 2,
                    px: 1.25,
                    py: 0.75,
                    minWidth: 176,
                    borderColor: 'divider',
                    cursor: 'pointer',
                  }}
                >
                  <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
                    {edge.data.protocol && (
                      <Chip
                        size="small"
                        label={edge.data.protocol}
                        color="info"
                        sx={{
                          height: 22,
                          fontFamily: '"JetBrains Mono", monospace',
                        }}
                      />
                    )}
                    <Typography variant="caption" sx={{ fontWeight: 600 }}>
                      {edge.data.label || 'Link'}
                    </Typography>
                  </Stack>
                </Box>
              );
            })}

            {nodes.map((node) => {
              const appearance = getArchitectureNodeAppearance(node.type);
              const selected = selectedIds.has(node.id);
              const dragging = draggingNodeId === node.id;
              const meta = summarizeNodeMeta(node);

              return (
                <Box
                  key={node.id}
                  onMouseDown={(event) => handleNodeMouseDown(event, node.id)}
                  onDoubleClick={(event) => handleNodeDoubleClick(event, node.id)}
                  sx={{
                    ...graphGlassPanelSx,
                    position: 'absolute',
                    left: node.position.x,
                    top: node.position.y,
                    width: GRAPH_NODE_WIDTH,
                    minHeight: GRAPH_NODE_HEIGHT,
                    p: 1.5,
                    overflow: 'hidden',
                    cursor: dragging ? 'grabbing' : 'grab',
                    borderColor: selected ? alpha(appearance.accent, 0.68) : alpha(appearance.accent, 0.22),
                    boxShadow: 'none',
                    transform: dragging ? 'scale(1.02)' : selected ? 'translateY(-2px)' : 'none',
                    transition: 'transform 150ms ease, box-shadow 150ms ease, border-color 150ms ease',
                  }}
                >
                  <Box
                    sx={{
                      position: 'absolute',
                      inset: 0,
                      background: appearance.surface,
                      pointerEvents: 'none',
                    }}
                  />
                  <Stack spacing={1.1} sx={{ position: 'relative', zIndex: 1 }}>
                    <Stack direction="row" justifyContent="space-between" spacing={1}>
                      <Chip
                        size="small"
                        label={`${appearance.icon} ${appearance.label}`}
                        sx={{
                          height: 22,
                          bgcolor: alpha(appearance.accent, 0.14),
                          color: appearance.accent,
                          border: `1px solid ${alpha(appearance.accent, 0.24)}`,
                          fontFamily: '"JetBrains Mono", monospace',
                          fontWeight: 600,
                        }}
                      />
                      {selected && <Chip size="small" label="Selected" color="primary" sx={{ height: 22 }} />}
                    </Stack>
                    <Box>
                      <Typography variant="subtitle2" sx={{ lineHeight: 1.2 }}>
                        {node.data.label}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{
                          mt: 0.5,
                          display: '-webkit-box',
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                          minHeight: 32,
                        }}
                      >
                        {node.data.description || 'Double-click to document this part of the system.'}
                      </Typography>
                    </Box>
                    <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                      {meta.length > 0 ? (
                        meta.map((entry) => (
                          <Chip
                            key={entry}
                            size="small"
                            label={entry}
                            sx={{
                              height: 22,
                              bgcolor: 'rgba(255, 255, 255, 0.04)',
                              color: 'text.secondary',
                              fontFamily: '"JetBrains Mono", monospace',
                            }}
                          />
                        ))
                      ) : (
                        <Typography variant="caption" color="text.secondary">
                          Add tech, ports, or security notes
                        </Typography>
                      )}
                    </Stack>
                  </Stack>
                </Box>
              );
            })}

            {nodes.length === 0 && (
              <GraphEmptyState
                title="Start mapping the system"
                description="Drop in nodes manually or generate a first pass from the repository to seed the architecture model."
                hint="Saved node positions and graph data keep the existing backend schema. This refresh only changes the interaction layer."
                action={
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                    <Button variant="contained" onClick={handleAddNode} startIcon={<AddCircleOutlineRoundedIcon />}>
                      Add First Node
                    </Button>
                    <Button
                      variant="outlined"
                      color="secondary"
                      onClick={handleGenerateGraph}
                      disabled={generating}
                      startIcon={<AutoAwesomeRoundedIcon />}
                    >
                      Generate from Repo
                    </Button>
                  </Stack>
                }
              />
            )}
          </Box>
        </Box>
      </Paper>

      <Dialog open={editingNodeId !== null} onClose={() => setEditingNodeId(null)}>
        <DialogTitle>Edit node</DialogTitle>
        <DialogContent>
          {editingNodeId && (() => {
            const node = nodes.find((candidate) => candidate.id === editingNodeId);
            if (!node) return null;

            return (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1, minWidth: 360 }}>
                <FormControl size="small" fullWidth>
                  <InputLabel>Type</InputLabel>
                  <Select
                    label="Type"
                    value={NODE_TYPES.includes(node.type as typeof NODE_TYPES[number]) ? node.type : 'service'}
                    onChange={(event) => handleUpdateNode(editingNodeId, { type: event.target.value })}
                  >
                    {NODE_TYPES.map((type) => (
                      <MenuItem key={type} value={type}>
                        {type}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <TextField
                  size="small"
                  label="Label"
                  value={node.data.label}
                  onChange={(event) => handleUpdateNode(editingNodeId, { label: event.target.value })}
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Functionality description"
                  value={node.data.description ?? ''}
                  onChange={(event) => handleUpdateNode(editingNodeId, { description: event.target.value })}
                  placeholder="What this node does in the system"
                  fullWidth
                  multiline
                  minRows={2}
                />
                <TextField
                  size="small"
                  label="Technologies (comma-separated)"
                  value={node.data.tech.join(', ')}
                  onChange={(event) =>
                    handleUpdateNode(editingNodeId, {
                      tech: event.target.value.split(',').map((item) => item.trim()),
                    })
                  }
                  placeholder="FastAPI, PostgreSQL"
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Ports (comma-separated)"
                  value={node.data.ports.join(', ')}
                  onChange={(event) =>
                    handleUpdateNode(editingNodeId, {
                      ports: event.target.value.split(',').map((item) => item.trim()),
                    })
                  }
                  placeholder="8000, 5432"
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Security (comma-separated)"
                  value={node.data.security.join(', ')}
                  onChange={(event) =>
                    handleUpdateNode(editingNodeId, {
                      security: event.target.value.split(',').map((item) => item.trim()),
                    })
                  }
                  placeholder="TLS, auth"
                  fullWidth
                />
              </Box>
            );
          })()}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditingNodeId(null)}>Done</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={editingEdgeId !== null} onClose={() => setEditingEdgeId(null)}>
        <DialogTitle>Edit edge</DialogTitle>
        <DialogContent>
          {editingEdgeId && (() => {
            const edge = edges.find((candidate) => candidate.id === editingEdgeId);
            if (!edge) return null;

            const source = nodeMap.get(edge.source);
            const target = nodeMap.get(edge.target);
            return (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1, minWidth: 340 }}>
                <Typography variant="body2" color="text.secondary">
                  {source?.data.label ?? edge.source} → {target?.data.label ?? edge.target}
                </Typography>
                <TextField
                  size="small"
                  label="Label"
                  value={edge.data?.label ?? ''}
                  onChange={(event) => handleUpdateEdge(editingEdgeId, { label: event.target.value })}
                  placeholder="API calls"
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Protocol"
                  value={edge.data?.protocol ?? ''}
                  onChange={(event) => handleUpdateEdge(editingEdgeId, { protocol: event.target.value })}
                  placeholder="HTTP, gRPC"
                  fullWidth
                />
                <Typography variant="caption" color="text.secondary">
                  Label and protocol are the only stored edge metadata fields used by the backend.
                </Typography>
              </Box>
            );
          })()}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditingEdgeId(null)}>Done</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default GraphEditorPage;
