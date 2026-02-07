import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Box, Typography, Button, Paper, TextField, Dialog, DialogTitle, DialogContent, DialogActions } from '@mui/material';
import { getGraph, updateGraph } from '../utils/api';

interface NodeData {
  label: string;
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

function normalizeNode(n: Partial<GraphNode>): GraphNode {
  const data = (n.data ?? {}) as Partial<NodeData>;
  return {
    id: n.id ?? `node-${Date.now()}`,
    type: n.type ?? 'service',
    position: n.position ?? { x: 0, y: 0 },
    data: {
      label: data.label ?? 'Service',
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

const NODE_R = 36;

function getNodePos(node: GraphNode) {
  return node.position;
}

const GraphEditorPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null);
  const dragRef = useRef<{ nodeId: string; startX: number; startY: number; startPos: { x: number; y: number } } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const setNodesRef = useRef(setNodes);
  setNodesRef.current = setNodes;

  const fetchGraph = useCallback(async () => {
    if (!projectId) return;
    try {
      const data = await getGraph(projectId);
      setNodes(
        Array.isArray(data.nodes)
          ? (data.nodes as Partial<GraphNode>[]).map(normalizeNode)
          : []
      );
      setEdges(
        Array.isArray(data.edges)
          ? (data.edges as Partial<GraphEdge>[]).map(normalizeEdge)
          : []
      );
    } catch (error) {
      console.error('Failed to fetch graph:', error);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) fetchGraph();
  }, [projectId, fetchGraph]);

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
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;
    setEditingNodeId(null);
    setEditingEdgeId(null);
    dragRef.current = {
      nodeId,
      startX: e.clientX,
      startY: e.clientY,
      startPos: { ...node.position },
    };
  };

  // Always-attached window listeners for drag (so they exist when dragRef gets set)
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const dx = e.clientX - d.startX;
      const dy = e.clientY - d.startY;
      setNodesRef.current((prev: GraphNode[]) =>
        prev.map((n) =>
          n.id === d.nodeId
            ? { ...n, position: { x: d.startPos.x + dx, y: d.startPos.y + dy } }
            : n
        )
      );
    };
    const onUp = () => {
      dragRef.current = null;
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  const handlePaneClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      setSelectedIds(new Set());
      setEditingNodeId(null);
      setEditingEdgeId(null);
    }
  };

  const handleAddNode = () => {
    const id = `node-${Date.now()}`;
    setNodes((prev) => [
      ...prev,
      {
        id,
        type: 'service',
        position: { x: 150 + Math.random() * 200, y: 150 + Math.random() * 200 },
        data: { label: 'New Service', tech: [], ports: [], security: [] },
      },
    ]);
  };

  const handleConnectSelected = () => {
    if (selectedIds.size !== 2) {
      alert('Select exactly 2 nodes to connect (shift+click two nodes).');
      return;
    }
    const [a, b] = Array.from(selectedIds);
    setEdges((prev) => [
      ...prev,
      normalizeEdge({ id: `edge-${Date.now()}`, source: a, target: b }),
    ]);
    setSelectedIds(new Set());
  };

  const handleDeleteSelected = () => {
    if (selectedIds.size === 0) return;
    const toRemove = Array.from(selectedIds);
    setNodes((prev) => prev.filter((n) => !toRemove.includes(n.id)));
    setEdges((prev) =>
      prev.filter((e) => !toRemove.includes(e.source) && !toRemove.includes(e.target))
    );
    setSelectedIds(new Set());
  };

  const handleNodeDoubleClick = (e: React.MouseEvent, nodeId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setEditingEdgeId(null);
    setEditingNodeId(nodeId);
  };

  const handleUpdateNode = (nodeId: string, updates: Partial<NodeData>) => {
    setNodes((prev) =>
      prev.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...updates } } : n
      )
    );
  };

  const handleUpdateEdge = (edgeId: string, updates: Partial<EdgeData>) => {
    setEdges((prev) =>
      prev.map((e) =>
        e.id === edgeId ? { ...e, data: { ...e.data, ...updates } } : e
      )
    );
  };

  const handleSaveGraph = async () => {
    if (!projectId) return;
    try {
      await updateGraph(projectId, { nodes, edges });
      alert('Graph saved successfully!');
    } catch (error) {
      console.error('Failed to save graph:', error);
      alert('Failed to save graph');
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Typography>Loading...</Typography>
      </Box>
    );
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  return (
    <Box sx={{ height: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h4">Graph Editor</Typography>
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          <Button component={Link} to={`/projects/${projectId}/kanban`} color="primary">
            Kanban Board
          </Button>
          <Button variant="contained" color="primary" onClick={handleAddNode}>
            Add Node
          </Button>
          <Button variant="outlined" color="primary" onClick={handleConnectSelected}>
            Connect selected
          </Button>
          <Button variant="outlined" color="error" onClick={handleDeleteSelected}>
            Delete selected
          </Button>
          <Button variant="contained" color="primary" onClick={handleSaveGraph}>
            Save Graph
          </Button>
        </Box>
      </Box>

      <Paper
        sx={{
          flex: 1,
          minHeight: 400,
          overflow: 'hidden',
          backgroundColor: '#1a1a2e',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Typography variant="body2" color="textSecondary" sx={{ p: 1, flexShrink: 0 }}>
          Drag nodes to move. Double-click a node to edit; double-click an edge (the line) to edit its label and protocol. Shift+click nodes to select; “Connect selected” adds an edge.
        </Typography>
        <Box
          ref={containerRef}
          sx={{
            width: '100%',
            flex: 1,
            minHeight: 360,
            position: 'relative',
            cursor: dragRef.current ? 'grabbing' : 'default',
            userSelect: 'none',
          }}
          onMouseDown={handlePaneClick}
        >
          <svg
            width="100%"
            height="100%"
            style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'auto' }}
          >
            <defs>
              <marker
                id="arrow"
                markerWidth="10"
                markerHeight="10"
                refX="9"
                refY="3"
                orient="auto"
                markerUnits="strokeWidth"
              >
                <path d="M0,0 L0,6 L9,3 z" fill="#6366f1" />
              </marker>
            </defs>
            {edges.map((e) => {
              const src = nodeMap.get(e.source);
              const tgt = nodeMap.get(e.target);
              if (!src || !tgt) return null;
              const sx = getNodePos(src).x + NODE_R;
              const sy = getNodePos(src).y + NODE_R;
              const tx = getNodePos(tgt).x + NODE_R;
              const ty = getNodePos(tgt).y + NODE_R;
              return (
                <g
                  key={e.id}
                  onDoubleClick={(ev) => {
                    ev.stopPropagation();
                    ev.preventDefault();
                    setEditingEdgeId(e.id);
                  }}
                  style={{ cursor: 'pointer' }}
                >
                  <line
                    x1={sx}
                    y1={sy}
                    x2={tx}
                    y2={ty}
                    stroke="#6366f1"
                    strokeWidth={2}
                    markerEnd="url(#arrow)"
                  />
                  <line
                    x1={sx}
                    y1={sy}
                    x2={tx}
                    y2={ty}
                    stroke="transparent"
                    strokeWidth={16}
                  />
                </g>
              );
            })}
          </svg>
          {nodes.map((node) => {
            const pos = getNodePos(node);
            const selected = selectedIds.has(node.id);
            return (
              <Box
                key={node.id}
                onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
                onDoubleClick={(e) => handleNodeDoubleClick(e, node.id)}
                sx={{
                  position: 'absolute',
                  left: pos.x,
                  top: pos.y,
                  width: NODE_R * 2,
                  height: NODE_R * 2,
                  borderRadius: '50%',
                  bgcolor: selected ? '#818cf8' : '#6366f1',
                  border: '2px solid',
                  borderColor: selected ? '#a5b4fc' : '#818cf8',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'grab',
                  pointerEvents: 'auto',
                  userSelect: 'none',
                  '&:active': { cursor: 'grabbing' },
                }}
              >
                <Typography
                  variant="caption"
                  component="span"
                  sx={{
                    color: '#e0e0e0',
                    textAlign: 'center',
                    px: 0.5,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: NODE_R * 2,
                    pointerEvents: 'none',
                  }}
                >
                  {node.data.label}
                </Typography>
              </Box>
            );
          })}
        </Box>
      </Paper>

      <Dialog
        open={editingNodeId !== null}
        onClose={() => setEditingNodeId(null)}
        PaperProps={{ sx: { backgroundColor: '#1a1a2e' } }}
      >
        <DialogTitle>Edit node</DialogTitle>
        <DialogContent>
          {editingNodeId && (() => {
            const node = nodes.find((n) => n.id === editingNodeId);
            if (!node) return null;
            return (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1, minWidth: 340 }}>
                <TextField
                  size="small"
                  label="Label"
                  value={node.data.label}
                  onChange={(e) => handleUpdateNode(editingNodeId, { label: e.target.value })}
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Technologies (comma-separated)"
                  value={node.data.tech.join(', ')}
                  onChange={(e) =>
                    handleUpdateNode(editingNodeId, {
                      tech: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="e.g. FastAPI, PostgreSQL"
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Ports (comma-separated)"
                  value={node.data.ports.join(', ')}
                  onChange={(e) =>
                    handleUpdateNode(editingNodeId, {
                      ports: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="e.g. 8000, 5432"
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Security (comma-separated)"
                  value={node.data.security.join(', ')}
                  onChange={(e) =>
                    handleUpdateNode(editingNodeId, {
                      security: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="e.g. TLS, auth"
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

      <Dialog
        open={editingEdgeId !== null}
        onClose={() => setEditingEdgeId(null)}
        PaperProps={{ sx: { backgroundColor: '#1a1a2e' } }}
      >
        <DialogTitle>Edit edge</DialogTitle>
        <DialogContent>
          {editingEdgeId && (() => {
            const edge = edges.find((e) => e.id === editingEdgeId);
            if (!edge) return null;
            const src = nodeMap.get(edge.source);
            const tgt = nodeMap.get(edge.target);
            return (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1, minWidth: 320 }}>
                <Typography variant="body2" color="textSecondary">
                  {src?.data.label ?? edge.source} → {tgt?.data.label ?? edge.target}
                </Typography>
                <TextField
                  size="small"
                  label="Label"
                  value={edge.data?.label ?? ''}
                  onChange={(e) => handleUpdateEdge(editingEdgeId, { label: e.target.value })}
                  placeholder="e.g. API calls"
                  fullWidth
                />
                <TextField
                  size="small"
                  label="Protocol"
                  value={edge.data?.protocol ?? ''}
                  onChange={(e) => handleUpdateEdge(editingEdgeId, { protocol: e.target.value })}
                  placeholder="e.g. HTTP, gRPC"
                  fullWidth
                />
                <Typography variant="caption" color="textSecondary">
                  Stored in DB: label and protocol (these are the only edge data fields we use).
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
