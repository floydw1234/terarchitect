import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Box,
  Typography,
  Button,
  Paper,
  TextField,
  Card,
  CardContent,
  CardActions,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  Collapse,
  Checkbox,
  FormControlLabel,
  Tooltip,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import {
  getKanban,
  getTickets,
  getGraph,
  createTicket,
  updateTicket,
  deleteTicket,
  getNotes,
  createNote,
  updateNote,
  deleteNote,
  updateKanban,
  getTicketLogs,
  cancelTicketExecution,
  type Ticket,
  type KanbanColumn,
  type Note,
  type ExecutionLogEntry,
} from '../utils/api';

/** Graph node/edge shape from API (minimal for dropdowns). */
interface GraphNodeOption {
  id: string;
  label: string;
}
interface GraphEdgeOption {
  id: string;
  label: string;
}

const DEFAULT_COLUMNS: KanbanColumn[] = [
  { id: 'backlog', title: 'Backlog', order: 0 },
  { id: 'in_progress', title: 'In Progress', order: 1 },
  { id: 'in_review', title: 'In Review', order: 2 },
  { id: 'done', title: 'Done', order: 3 },
];

const COLUMN_TITLE_BY_ID: Record<string, string> = {
  backlog: 'Backlog',
  in_progress: 'In Progress',
  in_review: 'In Review',
  done: 'Done',
};

/** Canonical order so In Review is always left of Done (Backlog → In Progress → In Review → Done). */
const CANONICAL_COLUMN_ORDER: Record<string, number> = {
  backlog: 0,
  in_progress: 1,
  in_review: 2,
  done: 3,
};

const KanbanPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [columns, setColumns] = useState<KanbanColumn[]>(DEFAULT_COLUMNS);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  const [createTicketOpen, setCreateTicketOpen] = useState(false);
  const [newTicketTitle, setNewTicketTitle] = useState('');
  const [newTicketDescription, setNewTicketDescription] = useState('');
  const [newTicketPriority, setNewTicketPriority] = useState<string>('medium');
  const [newTicketStatus, setNewTicketStatus] = useState<string>('todo');
  const [newTicketColumnId, setNewTicketColumnId] = useState('backlog');
  const [newTicketNodeIds, setNewTicketNodeIds] = useState<string[]>([]);
  const [newTicketEdgeIds, setNewTicketEdgeIds] = useState<string[]>([]);
  const [newTicketAllNodesAndEdges, setNewTicketAllNodesAndEdges] = useState(false);
  const [addTicketLoading, setAddTicketLoading] = useState(false);
  const [addTicketError, setAddTicketError] = useState<string | null>(null);
  const [editTicket, setEditTicket] = useState<Ticket | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editPriority, setEditPriority] = useState<string>('medium');
  const [editStatus, setEditStatus] = useState<string>('todo');
  const [editColumnId, setEditColumnId] = useState('');
  const [editNodeIds, setEditNodeIds] = useState<string[]>([]);
  const [editEdgeIds, setEditEdgeIds] = useState<string[]>([]);
  const [editAllNodesAndEdges, setEditAllNodesAndEdges] = useState(false);
  const [graphNodes, setGraphNodes] = useState<GraphNodeOption[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdgeOption[]>([]);
  const [newNoteTitle, setNewNoteTitle] = useState('');
  const [newNoteContent, setNewNoteContent] = useState('');
  const [newNoteNodeIds, setNewNoteNodeIds] = useState<string[]>([]);
  const [newNoteEdgeIds, setNewNoteEdgeIds] = useState<string[]>([]);
  const [createNoteOpen, setCreateNoteOpen] = useState(false);
  const [addNoteLoading, setAddNoteLoading] = useState(false);
  const [addNoteError, setAddNoteError] = useState<string | null>(null);
  const [editNote, setEditNote] = useState<Note | null>(null);
  const [editNoteTitle, setEditNoteTitle] = useState('');
  const [editNoteContent, setEditNoteContent] = useState('');
  const [editNoteNodeIds, setEditNoteNodeIds] = useState<string[]>([]);
  const [editNoteEdgeIds, setEditNoteEdgeIds] = useState<string[]>([]);
  const [editColumnsOpen, setEditColumnsOpen] = useState(false);
  const [editColumnTitles, setEditColumnTitles] = useState<{ id: string; title: string; order: number }[]>([]);
  const [executionLogs, setExecutionLogs] = useState<ExecutionLogEntry[]>([]);
  const [logsExpanded, setLogsExpanded] = useState(false);
  const [cancelRunning, setCancelRunning] = useState(false);

  useEffect(() => {
    if (projectId) {
      fetchKanban();
    }
  }, [projectId]);

  const fetchKanban = async () => {
    if (!projectId) return;
    try {
      const [kanbanRes, ticketsRes, notesRes, graphRes] = await Promise.all([
        getKanban(projectId),
        getTickets(projectId),
        getNotes(projectId),
        getGraph(projectId).catch(() => ({ nodes: [], edges: [] })),
      ]);
      const apiColumns =
        kanbanRes.columns && kanbanRes.columns.length > 0
          ? kanbanRes.columns
          : DEFAULT_COLUMNS;
      const ticketColumnIds = [...new Set((ticketsRes as Ticket[]).map((t) => t.column_id))];
      const columnIds = new Set(apiColumns.map((c) => c.id));
      const nextColumns = [...apiColumns];
      for (const id of ticketColumnIds) {
        if (!columnIds.has(id)) {
          columnIds.add(id);
          nextColumns.push({
            id,
            title: COLUMN_TITLE_BY_ID[id] ?? id.replace(/_/g, ' '),
            order: nextColumns.length,
          });
        }
      }
      nextColumns.sort(
        (a, b) =>
          (CANONICAL_COLUMN_ORDER[a.id] ?? a.order ?? 999) -
          (CANONICAL_COLUMN_ORDER[b.id] ?? b.order ?? 999)
      );
      setColumns(nextColumns);
      setTickets(ticketsRes);
      setNotes(
        (notesRes as Note[]).map((n) => ({
          ...n,
          node_ids: Array.isArray(n.node_ids) ? n.node_ids : [],
          edge_ids: Array.isArray(n.edge_ids) ? n.edge_ids : [],
        }))
      );
      const nodes = Array.isArray(graphRes.nodes) ? graphRes.nodes as Array<{ id?: string; data?: { label?: string } }> : [];
      const edges = Array.isArray(graphRes.edges) ? graphRes.edges as Array<{ id?: string; source?: string; target?: string; data?: { label?: string } }> : [];
      const nodeLabelById: Record<string, string> = {};
      nodes.forEach((n) => {
        const id = n.id ?? '';
        nodeLabelById[id] = (n.data?.label ?? n.id) || 'Unnamed';
      });
      setGraphNodes(nodes.map((n) => ({ id: n.id ?? '', label: nodeLabelById[n.id ?? ''] ?? 'Unnamed' })));
      setGraphEdges(edges.map((e) => {
        const sourceLabel = e.source ? (nodeLabelById[e.source] ?? e.source) : '';
        const targetLabel = e.target ? (nodeLabelById[e.target] ?? e.target) : '';
        const fallback = sourceLabel && targetLabel ? `${sourceLabel} → ${targetLabel}` : (e.id ?? 'Unnamed');
        return {
          id: e.id ?? '',
          label: (e.data?.label?.trim() || '') || fallback,
        };
      }));
    } catch (error) {
      console.error('Failed to fetch kanban:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddTicket = async () => {
    if (!newTicketTitle.trim() || !projectId) return;
    setAddTicketError(null);
    setAddTicketLoading(true);
    try {
      const data = await createTicket(projectId, {
        column_id: newTicketColumnId || 'backlog',
        title: newTicketTitle.trim(),
        description: newTicketDescription.trim() || undefined,
        priority: newTicketPriority,
        status: newTicketStatus,
        associated_node_ids: newTicketAllNodesAndEdges ? ['*'] : newTicketNodeIds,
        associated_edge_ids: newTicketAllNodesAndEdges ? ['*'] : newTicketEdgeIds,
      });
      setTickets((prev) => [...prev, data]);
      setNewTicketTitle('');
      setNewTicketDescription('');
      setNewTicketPriority('medium');
      setNewTicketStatus('todo');
      setNewTicketColumnId(columns.find((c) => c.id === 'backlog')?.id || columns[0]?.id || 'backlog');
      setNewTicketNodeIds([]);
      setNewTicketEdgeIds([]);
      setNewTicketAllNodesAndEdges(false);
      setCreateTicketOpen(false);
    } catch (error) {
      setAddTicketError(error instanceof Error ? error.message : 'Failed to add ticket');
    } finally {
      setAddTicketLoading(false);
    }
  };

  const openCreateTicket = () => {
    setNewTicketTitle('');
    setNewTicketDescription('');
    setNewTicketPriority('medium');
    setNewTicketStatus('todo');
    setNewTicketColumnId(columns.find((c) => c.id === 'backlog')?.id || columns[0]?.id || 'backlog');
    setNewTicketNodeIds([]);
    setNewTicketEdgeIds([]);
    setNewTicketAllNodesAndEdges(false);
    setAddTicketError(null);
    setCreateTicketOpen(true);
  };

  const openEditTicket = (ticket: Ticket) => {
    setEditTicket(ticket);
    setEditTitle(ticket.title);
    setEditDescription(ticket.description || '');
    setEditPriority(ticket.priority);
    setEditStatus(ticket.status);
    setEditColumnId(ticket.column_id);
    const nodeIds = ticket.associated_node_ids ?? [];
    const edgeIds = ticket.associated_edge_ids ?? [];
    const isAll = nodeIds.length === 1 && nodeIds[0] === '*';
    setEditAllNodesAndEdges(isAll);
    setEditNodeIds(isAll ? [] : nodeIds);
    setEditEdgeIds(isAll ? [] : edgeIds);
    setExecutionLogs([]);
    setLogsExpanded(false);
  };

  const handleShowLogs = async () => {
    if (!projectId || !editTicket || logsExpanded) return;
    setLogsExpanded(true);
    try {
      const logs = await getTicketLogs(projectId, editTicket.id);
      setExecutionLogs(logs);
    } catch {
      setExecutionLogs([]);
    }
  };

  const handleCancelExecution = async () => {
    if (!projectId || !editTicket) return;
    setCancelRunning(true);
    try {
      await cancelTicketExecution(projectId, editTicket.id);
      const logs = await getTicketLogs(projectId, editTicket.id).catch(() => []);
      if (Array.isArray(logs)) {
        setExecutionLogs(logs);
        setLogsExpanded(true);
      }
    } catch (error) {
      console.error('Failed to cancel execution:', error);
    } finally {
      setCancelRunning(false);
    }
  };

  const handleSaveTicket = async () => {
    if (!projectId || !editTicket) return;
    try {
      const updated = await updateTicket(projectId, editTicket.id, {
        title: editTitle.trim(),
        description: editDescription.trim() || undefined,
        priority: editPriority,
        status: editStatus,
        column_id: editColumnId,
        associated_node_ids: editAllNodesAndEdges ? ['*'] : editNodeIds,
        associated_edge_ids: editAllNodesAndEdges ? ['*'] : editEdgeIds,
      });
      setTickets((prev) => prev.map((t) => (t.id === editTicket.id ? updated : t)));
      setEditTicket(null);
    } catch (error) {
      console.error('Failed to update ticket:', error);
    }
  };

  const handleMoveTicket = async (ticketId: string, targetColumnId: string) => {
    if (!projectId) return;
    try {
      const updated = await updateTicket(projectId, ticketId, { column_id: targetColumnId });
      setTickets((prev) => prev.map((t) => (t.id === ticketId ? updated : t)));
    } catch (error) {
      console.error('Failed to move ticket:', error);
    }
  };

  const handleDeleteTicket = async (ticketId: string) => {
    if (!projectId) return;
    try {
      await deleteTicket(projectId, ticketId);
      setTickets((prev) => prev.filter((t) => t.id !== ticketId));
      if (editTicket?.id === ticketId) setEditTicket(null);
    } catch (error) {
      console.error('Failed to delete ticket:', error);
    }
  };

  const handleAddNote = async () => {
    if (!newNoteTitle.trim() || !projectId) return;
    setAddNoteError(null);
    setAddNoteLoading(true);
    try {
      const data = await createNote(projectId, {
        title: newNoteTitle.trim(),
        content: newNoteContent.trim() || '',
        node_ids: newNoteNodeIds,
        edge_ids: newNoteEdgeIds,
      });
      setNotes((prev) => [...prev, data]);
      setNewNoteTitle('');
      setNewNoteContent('');
      setNewNoteNodeIds([]);
      setNewNoteEdgeIds([]);
      setCreateNoteOpen(false);
    } catch (error) {
      setAddNoteError(error instanceof Error ? error.message : 'Failed to add note');
    } finally {
      setAddNoteLoading(false);
    }
  };

  const openCreateNote = () => {
    setNewNoteTitle('');
    setNewNoteContent('');
    setNewNoteNodeIds([]);
    setNewNoteEdgeIds([]);
    setAddNoteError(null);
    setCreateNoteOpen(true);
  };

  const openEditNote = (note: Note) => {
    setEditNote(note);
    setEditNoteTitle(note.title ?? '');
    setEditNoteContent(note.content ?? '');
    setEditNoteNodeIds(Array.isArray(note.node_ids) ? note.node_ids : []);
    setEditNoteEdgeIds(Array.isArray(note.edge_ids) ? note.edge_ids : []);
  };

  const handleSaveNote = async () => {
    if (!projectId || !editNote) return;
    try {
      const updated = await updateNote(projectId, editNote.id, {
        title: editNoteTitle.trim() || undefined,
        content: editNoteContent.trim() || undefined,
        node_ids: editNoteNodeIds,
        edge_ids: editNoteEdgeIds,
      });
      const normalized = {
        ...updated,
        node_ids: Array.isArray(updated.node_ids) ? updated.node_ids : [],
        edge_ids: Array.isArray(updated.edge_ids) ? updated.edge_ids : [],
      };
      setNotes((prev) => prev.map((n) => (n.id === editNote.id ? normalized : n)));
      setEditNote(null);
    } catch (error) {
      console.error('Failed to update note:', error);
    }
  };

  const handleDeleteNote = async (noteId: string) => {
    if (!projectId) return;
    try {
      await deleteNote(projectId, noteId);
      setNotes((prev) => prev.filter((n) => n.id !== noteId));
      if (editNote?.id === noteId) setEditNote(null);
    } catch (error) {
      console.error('Failed to delete note:', error);
    }
  };

  const openEditColumns = () => {
    setEditColumnTitles(columns.map((c) => ({ id: c.id, title: c.title, order: c.order })));
    setEditColumnsOpen(true);
  };

  const handleSaveColumns = async () => {
    if (!projectId) return;
    try {
      const newColumns = editColumnTitles.map((c, i) => ({ ...c, order: i }));
      await updateKanban(projectId, { columns: newColumns });
      setColumns(newColumns);
      setEditColumnsOpen(false);
    } catch (error) {
      console.error('Failed to update columns:', error);
    }
  };

  const addColumnRow = () => {
    const id = `col_${Date.now()}`;
    setEditColumnTitles((prev) => [...prev, { id, title: 'New column', order: prev.length }]);
  };

  const removeColumnRow = (index: number) => {
    setEditColumnTitles((prev) => prev.filter((_, i) => i !== index));
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
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3, flexWrap: 'wrap', gap: 2 }}>
        <Typography variant="h4">Kanban Board</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="contained" size="small" onClick={openCreateTicket}>
            Create ticket
          </Button>
          <Button variant="outlined" size="small" onClick={openEditColumns}>
            Edit columns
          </Button>
          <Button component={Link} to={`/projects/${projectId}/graph`} variant="outlined" size="small">
            Graph
          </Button>
        </Box>
      </Box>

      {/* Board */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box sx={{ display: 'flex', gap: 3, alignItems: 'stretch' }}>
          {columns.map((column) => (
            <Box key={column.id} sx={{ flex: 1, minWidth: 250, display: 'flex', flexDirection: 'column' }}>
              <Typography variant="h6" sx={{ mb: 2, fontWeight: 'bold' }}>
                {column.title}
              </Typography>
              <Paper
                sx={{
                  minHeight: 400,
                  maxHeight: '70vh',
                  overflowY: 'auto',
                  p: 2,
                  backgroundColor: 'background.default',
                }}
              >
                {tickets
                  .filter((ticket) => ticket.column_id === column.id)
                  .map((ticket) => (
                    <Card
                      key={ticket.id}
                      sx={{
                        mb: 2,
                        borderLeft: 4,
                        borderLeftColor:
                          ticket.priority === 'high'
                            ? 'error.main'
                            : ticket.priority === 'medium'
                              ? 'warning.main'
                              : 'success.main',
                      }}
                    >
                      <CardContent onClick={() => openEditTicket(ticket)} sx={{ cursor: 'pointer' }}>
                        <Typography variant="subtitle1" fontWeight="bold">
                          {ticket.title}
                        </Typography>
                        {ticket.description && (
                          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                            {ticket.description}
                          </Typography>
                        )}
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                          Priority: {ticket.priority}
                        </Typography>
                        {ticket.pr_url && (
                          <Typography variant="caption" sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                            <Link
                              to={`/projects/${projectId}/review/${ticket.id}`}
                              onClick={(e) => e.stopPropagation()}
                              style={{ color: 'inherit' }}
                            >
                              Review
                            </Link>
                            <span>·</span>
                            <a href={ticket.pr_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                              Open PR{ticket.pr_number ? ` #${ticket.pr_number}` : ''}
                            </a>
                          </Typography>
                        )}
                      </CardContent>
                      <CardActions sx={{ justifyContent: 'space-between', pt: 0 }}>
                        <Box>
                          {columns
                            .filter((col) => col.id !== column.id)
                            .map((col) => {
                              const isInProgress = col.id === 'in_progress';
                              const disableInProgress = isInProgress && graphNodes.length === 0;
                              const btn = (
                                <Button
                                  key={col.id}
                                  size="small"
                                  disabled={disableInProgress}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleMoveTicket(ticket.id, col.id);
                                  }}
                                >
                                  → {col.title}
                                </Button>
                              );
                              return disableInProgress ? (
                                <Tooltip key={col.id} title="Add at least one node to the graph first">
                                  <span>{btn}</span>
                                </Tooltip>
                              ) : (
                                btn
                              );
                            })}
                        </Box>
                        <IconButton
                          size="small"
                          color="error"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteTicket(ticket.id);
                          }}
                          aria-label="Delete ticket"
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </CardActions>
                    </Card>
                  ))}
              </Paper>
            </Box>
          ))}
        </Box>
      </Paper>

      {/* Ticket create dialog */}
      <Dialog open={createTicketOpen} onClose={() => setCreateTicketOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create ticket</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label="Title"
              value={newTicketTitle}
              onChange={(e) => setNewTicketTitle(e.target.value)}
              fullWidth
              size="small"
            />
            <TextField
              label="Description"
              value={newTicketDescription}
              onChange={(e) => setNewTicketDescription(e.target.value)}
              multiline
              minRows={3}
              fullWidth
              size="small"
            />
            <FormControl size="small" fullWidth>
              <InputLabel>Priority</InputLabel>
              <Select
                value={newTicketPriority}
                label="Priority"
                onChange={(e) => setNewTicketPriority(e.target.value)}
              >
                <MenuItem value="low">Low</MenuItem>
                <MenuItem value="medium">Medium</MenuItem>
                <MenuItem value="high">High</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Column</InputLabel>
              <Select
                value={newTicketColumnId}
                label="Column"
                onChange={(e) => setNewTicketColumnId(e.target.value)}
              >
                {columns.map((col) => (
                  <MenuItem key={col.id} value={col.id}>
                    {col.title}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Status</InputLabel>
              <Select
                value={newTicketStatus}
                label="Status"
                onChange={(e) => setNewTicketStatus(e.target.value)}
              >
                <MenuItem value="todo">Todo</MenuItem>
                <MenuItem value="in_progress">In progress</MenuItem>
                <MenuItem value="done">Done</MenuItem>
              </Select>
            </FormControl>
            <FormControlLabel
              control={
                <Checkbox
                  checked={newTicketAllNodesAndEdges}
                  onChange={(e) => setNewTicketAllNodesAndEdges(e.target.checked)}
                />
              }
              label="All nodes and edges (full graph context)"
            />
            <FormControl size="small" fullWidth disabled={newTicketAllNodesAndEdges}>
              <InputLabel>Nodes</InputLabel>
              <Select
                multiple
                value={newTicketNodeIds}
                label="Nodes"
                onChange={(e) => setNewTicketNodeIds(typeof e.target.value === 'string' ? [] : e.target.value)}
                renderValue={(selected) =>
                  newTicketAllNodesAndEdges
                    ? 'All'
                    : (selected as string[])
                        .map((id) => graphNodes.find((n) => n.id === id)?.label ?? id)
                        .join(', ') || 'None'
                }
              >
                {graphNodes.map((n) => (
                  <MenuItem key={n.id} value={n.id}>
                    {n.label}
                  </MenuItem>
                ))}
                {graphNodes.length === 0 && (
                  <MenuItem disabled>No nodes in graph yet</MenuItem>
                )}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth disabled={newTicketAllNodesAndEdges}>
              <InputLabel>Edges</InputLabel>
              <Select
                multiple
                value={newTicketEdgeIds}
                label="Edges"
                onChange={(e) => setNewTicketEdgeIds(typeof e.target.value === 'string' ? [] : e.target.value)}
                renderValue={(selected) =>
                  newTicketAllNodesAndEdges
                    ? 'All'
                    : (selected as string[])
                        .map((id) => graphEdges.find((edge) => edge.id === id)?.label ?? id)
                        .join(', ') || 'None'
                }
              >
                {graphEdges.map((edge) => (
                  <MenuItem key={edge.id} value={edge.id}>
                    {edge.label}
                  </MenuItem>
                ))}
                {graphEdges.length === 0 && (
                  <MenuItem disabled>No edges in graph yet</MenuItem>
                )}
              </Select>
            </FormControl>
            <Collapse in={!!addTicketError}>
              {addTicketError && (
                <Alert severity="error" onClose={() => setAddTicketError(null)}>
                  {addTicketError}
                </Alert>
              )}
            </Collapse>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateTicketOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleAddTicket} disabled={!newTicketTitle.trim() || addTicketLoading}>
            {addTicketLoading ? 'Creating…' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Ticket edit dialog */}
      <Dialog open={!!editTicket} onClose={() => setEditTicket(null)} maxWidth="md" fullWidth>
        {editTicket && (
          <>
            <DialogTitle>Edit ticket</DialogTitle>
            <DialogContent>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
                {/* Execution logs - visible at top when agent runs */}
                <Box sx={{ mb: 2, p: 2, borderRadius: 1, bgcolor: 'background.default' }}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Execution logs
                  </Typography>
                  <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={handleShowLogs}
                      disabled={!projectId}
                    >
                      {logsExpanded ? 'Refresh' : 'View'} execution logs
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      color="error"
                      onClick={handleCancelExecution}
                      disabled={!projectId || cancelRunning}
                    >
                      {cancelRunning ? 'Stopping…' : 'Stop agent'}
                    </Button>
                  </Box>
                  {logsExpanded && (
                    <Box sx={{ mt: 2, maxHeight: 200, overflow: 'auto' }}>
                      {executionLogs.length === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                          No logs yet. Move ticket to In Progress to trigger the agent.
                        </Typography>
                      ) : (
                        executionLogs.map((log) => (
                          <Paper key={log.id} sx={{ p: 1.5, mb: 1 }}>
                            <Typography variant="caption" color="text.secondary">
                              {log.step} • {log.created_at}
                            </Typography>
                            {log.summary && (
                              <Typography variant="body2" sx={{ mt: 0.5 }}>{log.summary}</Typography>
                            )}
                            {log.raw_output && (
                              <Typography
                                component="pre"
                                variant="caption"
                                sx={{
                                  mt: 1,
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-word',
                                  maxHeight: 100,
                                  overflow: 'auto',
                                  fontSize: '0.7rem',
                                }}
                              >
                                {log.raw_output}
                              </Typography>
                            )}
                          </Paper>
                        ))
                      )}
                    </Box>
                  )}
                </Box>
                <TextField
                  label="Title"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  fullWidth
                  size="small"
                />
                <TextField
                  label="Description"
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  multiline
                  minRows={3}
                  fullWidth
                  size="small"
                />
                <FormControl size="small" fullWidth>
                  <InputLabel>Priority</InputLabel>
                  <Select
                    value={editPriority}
                    label="Priority"
                    onChange={(e) => setEditPriority(e.target.value)}
                  >
                    <MenuItem value="low">Low</MenuItem>
                    <MenuItem value="medium">Medium</MenuItem>
                    <MenuItem value="high">High</MenuItem>
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Column</InputLabel>
                  <Select
                    value={editColumnId}
                    label="Column"
                    onChange={(e) => setEditColumnId(e.target.value)}
                  >
                    {columns.map((col) => (
                      <MenuItem key={col.id} value={col.id}>
                        {col.title}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Status</InputLabel>
                  <Select
                    value={editStatus}
                    label="Status"
                    onChange={(e) => setEditStatus(e.target.value)}
                  >
                    <MenuItem value="todo">Todo</MenuItem>
                    <MenuItem value="in_progress">In progress</MenuItem>
                    <MenuItem value="done">Done</MenuItem>
                  </Select>
                </FormControl>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={editAllNodesAndEdges}
                      onChange={(e) => setEditAllNodesAndEdges(e.target.checked)}
                    />
                  }
                  label="All nodes and edges (full graph context)"
                />
                <FormControl size="small" fullWidth disabled={editAllNodesAndEdges}>
                  <InputLabel>Nodes</InputLabel>
                  <Select
                    multiple
                    value={editNodeIds}
                    label="Nodes"
                    onChange={(e) => setEditNodeIds(typeof e.target.value === 'string' ? [] : e.target.value)}
                    renderValue={(selected) =>
                      editAllNodesAndEdges
                        ? 'All'
                        : (selected as string[])
                            .map((id) => graphNodes.find((n) => n.id === id)?.label ?? id)
                            .join(', ') || 'None'
                    }
                  >
                    {graphNodes.map((n) => (
                      <MenuItem key={n.id} value={n.id}>
                        {n.label}
                      </MenuItem>
                    ))}
                    {graphNodes.length === 0 && (
                      <MenuItem disabled>No nodes in graph yet</MenuItem>
                    )}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth disabled={editAllNodesAndEdges}>
                  <InputLabel>Edges</InputLabel>
                  <Select
                    multiple
                    value={editEdgeIds}
                    label="Edges"
                    onChange={(e) => setEditEdgeIds(typeof e.target.value === 'string' ? [] : e.target.value)}
                    renderValue={(selected) =>
                      editAllNodesAndEdges
                        ? 'All'
                        : (selected as string[])
                            .map((id) => graphEdges.find((e) => e.id === id)?.label ?? id)
                            .join(', ') || 'None'
                    }
                  >
                    {graphEdges.map((e) => (
                      <MenuItem key={e.id} value={e.id}>
                        {e.label}
                      </MenuItem>
                    ))}
                    {graphEdges.length === 0 && (
                      <MenuItem disabled>No edges in graph yet</MenuItem>
                    )}
                  </Select>
                </FormControl>
              </Box>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setEditTicket(null)}>Cancel</Button>
              <Button variant="contained" onClick={handleSaveTicket}>
                Save
              </Button>
            </DialogActions>
          </>
        )}
      </Dialog>

      {/* Notes section */}
      <Paper sx={{ p: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2, gap: 2, flexWrap: 'wrap' }}>
          <Typography variant="h6">
            Notes
          </Typography>
          <Button variant="outlined" size="small" onClick={openCreateNote}>
            Create note
          </Button>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {notes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No notes yet. Add one above.
            </Typography>
          ) : (
            notes.map((note) => (
              <Paper key={note.id} sx={{ p: 2, backgroundColor: 'background.default' }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <Box sx={{ flex: 1 }} onClick={() => openEditNote(note)} style={{ cursor: 'pointer' }}>
                    <Typography variant="subtitle2" fontWeight="bold">
                      {note.title || '(Untitled)'}
                    </Typography>
                    {note.content && (
                      <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', mt: 0.5 }}>
                        {note.content}
                      </Typography>
                    )}
                  </Box>
                  <Box>
                    <Button size="small" onClick={() => openEditNote(note)}>Edit</Button>
                    <IconButton size="small" color="error" onClick={() => handleDeleteNote(note.id)} aria-label="Delete note">
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Box>
                </Box>
              </Paper>
            ))
          )}
        </Box>
      </Paper>

      {/* Note create dialog */}
      <Dialog open={createNoteOpen} onClose={() => setCreateNoteOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Create note</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label="Title"
              value={newNoteTitle}
              onChange={(e) => setNewNoteTitle(e.target.value)}
              fullWidth
              size="small"
            />
            <TextField
              label="Content"
              value={newNoteContent}
              onChange={(e) => setNewNoteContent(e.target.value)}
              multiline
              minRows={3}
              fullWidth
              size="small"
            />
            <FormControl size="small" fullWidth>
              <InputLabel>Nodes</InputLabel>
              <Select
                multiple
                value={newNoteNodeIds}
                label="Nodes"
                onChange={(e) =>
                  setNewNoteNodeIds(
                    typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value
                  )
                }
                renderValue={(selected) =>
                  (selected as string[])
                    .map((id) => graphNodes.find((n) => n.id === id)?.label ?? id)
                    .join(', ') || 'None'
                }
              >
                {graphNodes.map((n) => (
                  <MenuItem key={n.id} value={n.id}>
                    {n.label}
                  </MenuItem>
                ))}
                {graphNodes.length === 0 && (
                  <MenuItem disabled>No nodes in graph yet</MenuItem>
                )}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Edges</InputLabel>
              <Select
                multiple
                value={newNoteEdgeIds}
                label="Edges"
                onChange={(e) =>
                  setNewNoteEdgeIds(
                    typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value
                  )
                }
                renderValue={(selected) =>
                  (selected as string[])
                    .map((id) => graphEdges.find((e) => e.id === id)?.label ?? id)
                    .join(', ') || 'None'
                }
              >
                {graphEdges.map((edge) => (
                  <MenuItem key={edge.id} value={edge.id}>
                    {edge.label}
                  </MenuItem>
                ))}
                {graphEdges.length === 0 && (
                  <MenuItem disabled>No edges in graph yet</MenuItem>
                )}
              </Select>
            </FormControl>
            <Collapse in={!!addNoteError}>
              {addNoteError && (
                <Alert severity="error" onClose={() => setAddNoteError(null)}>
                  {addNoteError}
                </Alert>
              )}
            </Collapse>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateNoteOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleAddNote}
            disabled={!newNoteTitle.trim() || addNoteLoading}
          >
            {addNoteLoading ? 'Creating…' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Note edit dialog */}
      <Dialog open={!!editNote} onClose={() => setEditNote(null)} maxWidth="sm" fullWidth>
        {editNote && (
          <>
            <DialogTitle>Edit note</DialogTitle>
            <DialogContent>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
                <TextField
                  label="Title"
                  value={editNoteTitle}
                  onChange={(e) => setEditNoteTitle(e.target.value)}
                  fullWidth
                  size="small"
                />
                <TextField
                  label="Content"
                  value={editNoteContent}
                  onChange={(e) => setEditNoteContent(e.target.value)}
                  multiline
                  minRows={3}
                  fullWidth
                  size="small"
                />
                <FormControl size="small" fullWidth>
                  <InputLabel>Nodes</InputLabel>
                  <Select
                    multiple
                    value={editNoteNodeIds}
                    label="Nodes"
                    onChange={(e) =>
                      setEditNoteNodeIds(
                        typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value
                      )
                    }
                    renderValue={(selected) =>
                      (selected as string[])
                        .map((id) => graphNodes.find((n) => n.id === id)?.label ?? id)
                        .join(', ') || 'None'
                    }
                  >
                    {graphNodes.map((n) => (
                      <MenuItem key={n.id} value={n.id}>
                        {n.label}
                      </MenuItem>
                    ))}
                    {graphNodes.length === 0 && (
                      <MenuItem disabled>No nodes in graph yet</MenuItem>
                    )}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Edges</InputLabel>
                  <Select
                    multiple
                    value={editNoteEdgeIds}
                    label="Edges"
                    onChange={(e) =>
                      setEditNoteEdgeIds(
                        typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value
                      )
                    }
                    renderValue={(selected) =>
                      (selected as string[])
                        .map((id) => graphEdges.find((e) => e.id === id)?.label ?? id)
                        .join(', ') || 'None'
                    }
                  >
                    {graphEdges.map((edge) => (
                      <MenuItem key={edge.id} value={edge.id}>
                        {edge.label}
                      </MenuItem>
                    ))}
                    {graphEdges.length === 0 && (
                      <MenuItem disabled>No edges in graph yet</MenuItem>
                    )}
                  </Select>
                </FormControl>
              </Box>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setEditNote(null)}>Cancel</Button>
              <Button variant="contained" onClick={handleSaveNote}>Save</Button>
            </DialogActions>
          </>
        )}
      </Dialog>

      {/* Edit columns dialog */}
      <Dialog open={editColumnsOpen} onClose={() => setEditColumnsOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Edit columns</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            {editColumnTitles.map((col, index) => (
              <Box key={col.id} sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                <TextField
                  label="Column title"
                  value={col.title}
                  onChange={(e) => {
                    const next = [...editColumnTitles];
                    next[index] = { ...next[index], title: e.target.value };
                    setEditColumnTitles(next);
                  }}
                  size="small"
                  fullWidth
                />
                <IconButton
                  size="small"
                  color="error"
                  onClick={() => removeColumnRow(index)}
                  aria-label="Remove column"
                  disabled={editColumnTitles.length <= 1}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Box>
            ))}
            <Button variant="outlined" size="small" onClick={addColumnRow}>
              Add column
            </Button>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditColumnsOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveColumns}>Save</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default KanbanPage;
