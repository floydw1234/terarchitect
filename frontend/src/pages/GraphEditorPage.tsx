import React, { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Box, Typography, Button, Paper, TextField } from '@mui/material';

interface Node {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: {
    label: string;
    tech: string[];
    ports: string[];
    security: string[];
  };
}

interface Edge {
  id: string;
  source: string;
  target: string;
  data?: {
    label?: string;
    protocol?: string;
  };
}

const GraphEditorPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchGraph();
  }, [projectId]);

  const fetchGraph = async () => {
    try {
      const response = await fetch(`/api/projects/${projectId}/graph`);
      const data = await response.json();
      setNodes(data.nodes || []);
      setEdges(data.edges || []);
    } catch (error) {
      console.error('Failed to fetch graph:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddNode = useCallback(() => {
    const newNode: Node = {
      id: `node-${Date.now()}`,
      type: 'service',
      position: { x: 100 + Math.random() * 200, y: 100 + Math.random() * 200 },
      data: {
        label: 'New Service',
        tech: [],
        ports: [],
        security: [],
      },
    };
    setNodes((prev) => [...prev, newNode]);
  }, []);

  const handleDeleteNode = useCallback((nodeId: string) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setEdges((prev) => prev.filter((e) => e.source !== nodeId && e.target !== nodeId));
  }, []);

  const handleSaveGraph = async () => {
    try {
      await fetch(`/api/projects/${projectId}/graph`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ nodes, edges }),
      });
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

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4">Graph Editor</Typography>
        <Box sx={{ display: 'flex', gap: 2 }}>
          <Button variant="contained" color="primary" onClick={handleAddNode}>
            Add Node
          </Button>
          <Button variant="contained" color="primary" onClick={handleSaveGraph}>
            Save Graph
          </Button>
        </Box>
      </Box>

      <Paper sx={{ p: 2, backgroundColor: '#1a1a2e' }}>
        <Typography variant="body2" color="textSecondary">
          Note: Full graph editor with drag-and-drop requires React Flow integration.
          This is a simplified placeholder implementation.
        </Typography>

        <Box sx={{ mt: 3 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Nodes ({nodes.length})
          </Typography>
          {nodes.map((node) => (
            <Paper
              key={node.id}
              sx={{
                p: 2,
                mb: 1,
                backgroundColor: '#16162a',
                cursor: 'pointer',
              }}
              onClick={() => handleDeleteNode(node.id)}
            >
              <Typography variant="body2">
                {node.data.label} ({node.id})
              </Typography>
              {node.data.tech.length > 0 && (
                <Typography variant="body2" color="textSecondary">
                  Tech: {node.data.tech.join(', ')}
                </Typography>
              )}
            </Paper>
          ))}
        </Box>

        <Box sx={{ mt: 3 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Edges ({edges.length})
          </Typography>
          {edges.map((edge) => (
            <Paper
              key={edge.id}
              sx={{ p: 2, mb: 1, backgroundColor: '#16162a' }}
            >
              <Typography variant="body2">
                {edge.source} → {edge.target}
              </Typography>
            </Paper>
          ))}
        </Box>
      </Paper>
    </Box>
  );
};

export default GraphEditorPage;
