import React, { useState, useEffect, useRef } from 'react';
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
  CircularProgress,
  Chip,
  Stack,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import ListAltIcon from '@mui/icons-material/ListAlt';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
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
  getSettingsCheck,
  type Ticket,
  type KanbanColumn,
  type Note,
  type ExecutionLogEntry,
  type SettingIssue,
} from '../utils/api';

interface GraphNodeOption { id: string; label: string; }
interface GraphEdgeOption { id: string; label: string; }

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

const CANONICAL_COLUMN_ORDER: Record<string, number> = {
  backlog: 0,
  in_progress: 1,
  in_review: 2,
  done: 3,
};

/** Columns shown in the board — In Progress is intentionally excluded (shown in Running strip above). */
const BOARD_COLUMN_IDS = new Set(['backlog', 'in_review', 'done']);

const PRIORITY_COLOR: Record<string, 'error' | 'warning' | 'success'> = {
  high: 'error',
  medium: 'warning',
  low: 'success',
};

// ---------------------------------------------------------------------------
// Running strip
// ---------------------------------------------------------------------------

interface RunningStripProps {
  tickets: Ticket[];
  projectId: string;
  onStop: (ticketId: string) => Promise<void>;
  onTicketUpdated: (ticket: Ticket) => void;
}

const RunningStrip: React.FC<RunningStripProps> = ({ tickets, projectId, onStop, onTicketUpdated }) => {
  const [logs, setLogs] = useState<Record<string, ExecutionLogEntry[]>>({});
  const [logsModal, setLogsModal] = useState<string | null>(null);
  const [stopping, setStopping] = useState<Set<string>>(new Set());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const ticketIds = tickets.map((t) => t.id).join(',');

  useEffect(() => {
    if (tickets.length === 0) {
      setLogs({});
      return;
    }

    const fetchAll = async () => {
      const updates: Record<string, ExecutionLogEntry[]> = {};
      await Promise.all(
        tickets.map(async (t) => {
          try {
            updates[t.id] = await getTicketLogs(projectId, t.id);
          } catch {
            updates[t.id] = [];
          }
        })
      );
      setLogs((prev) => ({ ...prev, ...updates }));
    };

    fetchAll();
    intervalRef.current = setInterval(fetchAll, 10_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [ticketIds, projectId]); // ticketIds is a stable string derived from ticket IDs

  const handleStop = async (ticketId: string) => {
    setStopping((prev) => new Set(prev).add(ticketId));
    try {
      await onStop(ticketId);
    } finally {
      setStopping((prev) => {
        const next = new Set(prev);
        next.delete(ticketId);
        return next;
      });
    }
  };

  if (tickets.length === 0) return null;

  const modalTicket = logsModal ? tickets.find((t) => t.id === logsModal) : null;
  const modalLogs = logsModal ? (logs[logsModal] ?? []) : [];

  return (
    <>
      <Paper
        sx={{
          p: 2,
          mb: 3,
          borderLeft: 4,
          borderLeftColor: 'primary.main',
          bgcolor: 'background.paper',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
          <CircularProgress size={14} thickness={5} color="primary" />
          <Typography variant="subtitle1" fontWeight={600}>
            Running
          </Typography>
          <Chip label={tickets.length} size="small" color="primary" sx={{ height: 18, fontSize: '0.65rem' }} />
        </Box>
        <Stack spacing={1}>
          {tickets.map((ticket) => {
            const ticketLogs = logs[ticket.id] ?? [];
            const lastLog = ticketLogs[ticketLogs.length - 1];
            const isStopping = stopping.has(ticket.id);
            return (
              <Box
                key={ticket.id}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 2,
                  p: 1.5,
                  borderRadius: 1,
                  bgcolor: 'background.default',
                  flexWrap: 'wrap',
                }}
              >
                <CircularProgress size={12} thickness={6} color="primary" sx={{ flexShrink: 0 }} />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={600} noWrap>
                    {ticket.title}
                  </Typography>
                  {lastLog ? (
                    <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                      <strong>{lastLog.step}</strong>
                      {lastLog.summary ? ` · ${lastLog.summary.slice(0, 120)}${lastLog.summary.length > 120 ? '…' : ''}` : ''}
                    </Typography>
                  ) : (
                    <Typography variant="caption" color="text.secondary">
                      Starting…
                    </Typography>
                  )}
                </Box>
                <Box sx={{ display: 'flex', gap: 1, flexShrink: 0 }}>
                  <Tooltip title="Show logs">
                    <IconButton size="small" onClick={() => setLogsModal(ticket.id)}>
                      <ListAltIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title={isStopping ? 'Stopping…' : 'Stop and return to Backlog'}>
                    <span>
                      <IconButton
                        size="small"
                        color="error"
                        disabled={isStopping}
                        onClick={() => handleStop(ticket.id)}
                      >
                        {isStopping ? <CircularProgress size={14} color="inherit" /> : <StopIcon fontSize="small" />}
                      </IconButton>
                    </span>
                  </Tooltip>
                </Box>
              </Box>
            );
          })}
        </Stack>
      </Paper>

      {/* Logs modal */}
      <Dialog
        open={!!logsModal}
        onClose={() => setLogsModal(null)}
        maxWidth="md"
        fullWidth
      >
        {modalTicket && (
          <>
            <DialogTitle>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <CircularProgress size={14} thickness={5} />
                <span>Logs — {modalTicket.title}</span>
              </Box>
            </DialogTitle>
            <DialogContent dividers>
              {modalLogs.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No logs yet.
                </Typography>
              ) : (
                [...modalLogs].reverse().map((log) => (
                  <Paper key={log.id} sx={{ p: 1.5, mb: 1, bgcolor: 'background.default' }}>
                    <Typography variant="caption" color="text.secondary">
                      {log.step}
                      {log.created_at ? ` · ${log.created_at}` : ''}
                    </Typography>
                    {log.summary && (
                      <Typography variant="body2" sx={{ mt: 0.5 }}>
                        {log.summary}
                      </Typography>
                    )}
                    {log.raw_output && (
                      <Typography
                        component="pre"
                        variant="caption"
                        sx={{
                          mt: 1,
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          maxHeight: 200,
                          overflowY: 'auto',
                          fontSize: '0.7rem',
                          display: 'block',
                          bgcolor: 'rgba(0,0,0,0.2)',
                          p: 1,
                          borderRadius: 0.5,
                        }}
                      >
                        {log.raw_output}
                      </Typography>
                    )}
                  </Paper>
                ))
              )}
            </DialogContent>
            <DialogActions>
              <Button
                size="small"
                color="error"
                startIcon={<StopIcon />}
                disabled={stopping.has(modalTicket.id)}
                onClick={async () => {
                  await handleStop(modalTicket.id);
                  setLogsModal(null);
                }}
              >
                Stop
              </Button>
              <Button onClick={() => setLogsModal(null)}>Close</Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </>
  );
};

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const KanbanPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [columns, setColumns] = useState<KanbanColumn[]>(DEFAULT_COLUMNS);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);

  // Create ticket dialog
  const [createTicketOpen, setCreateTicketOpen] = useState(false);
  const [newTicketTitle, setNewTicketTitle] = useState('');
  const [newTicketDescription, setNewTicketDescription] = useState('');
  const [newTicketPriority, setNewTicketPriority] = useState<string>('medium');
  const [newTicketNodeIds, setNewTicketNodeIds] = useState<string[]>([]);
  const [newTicketEdgeIds, setNewTicketEdgeIds] = useState<string[]>([]);
  const [newTicketAllNodesAndEdges, setNewTicketAllNodesAndEdges] = useState(false);
  const [addTicketLoading, setAddTicketLoading] = useState(false);
  const [addTicketError, setAddTicketError] = useState<string | null>(null);

  // Edit ticket dialog
  const [editTicket, setEditTicket] = useState<Ticket | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editPriority, setEditPriority] = useState<string>('medium');
  const [editNodeIds, setEditNodeIds] = useState<string[]>([]);
  const [editEdgeIds, setEditEdgeIds] = useState<string[]>([]);
  const [editAllNodesAndEdges, setEditAllNodesAndEdges] = useState(false);

  // Graph options
  const [graphNodes, setGraphNodes] = useState<GraphNodeOption[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdgeOption[]>([]);

  // Notes
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

  // Columns editor
  const [editColumnsOpen, setEditColumnsOpen] = useState(false);
  const [editColumnTitles, setEditColumnTitles] = useState<{ id: string; title: string; order: number }[]>([]);

  // Card-level action error (e.g. Run fails due to missing settings)
  const [actionError, setActionError] = useState<string | null>(null);

  // Settings readiness — determines whether Run is allowed
  const [missingRequired, setMissingRequired] = useState<SettingIssue[]>([]);

  useEffect(() => {
    if (projectId) fetchKanban();
    getSettingsCheck()
      .then((res) => setMissingRequired(res.missing_required ?? []))
      .catch(() => {}); // non-fatal — Run button will fall back to backend validation
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
        kanbanRes.columns && kanbanRes.columns.length > 0 ? kanbanRes.columns : DEFAULT_COLUMNS;
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

      const nodes = Array.isArray(graphRes.nodes)
        ? (graphRes.nodes as Array<{ id?: string; data?: { label?: string } }>)
        : [];
      const edges = Array.isArray(graphRes.edges)
        ? (graphRes.edges as Array<{ id?: string; source?: string; target?: string; data?: { label?: string } }>)
        : [];
      const nodeLabelById: Record<string, string> = {};
      nodes.forEach((n) => {
        nodeLabelById[n.id ?? ''] = (n.data?.label ?? n.id) || 'Unnamed';
      });
      setGraphNodes(nodes.map((n) => ({ id: n.id ?? '', label: nodeLabelById[n.id ?? ''] ?? 'Unnamed' })));
      setGraphEdges(
        edges.map((e) => {
          const src = e.source ? (nodeLabelById[e.source] ?? e.source) : '';
          const tgt = e.target ? (nodeLabelById[e.target] ?? e.target) : '';
          const fallback = src && tgt ? `${src} → ${tgt}` : (e.id ?? 'Unnamed');
          return { id: e.id ?? '', label: (e.data?.label?.trim() || '') || fallback };
        })
      );
    } catch (error) {
      console.error('Failed to fetch kanban:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleMoveTicket = async (ticketId: string, targetColumnId: string) => {
    if (!projectId) return;
    try {
      const updated = await updateTicket(projectId, ticketId, { column_id: targetColumnId });
      setTickets((prev) => prev.map((t) => (t.id === ticketId ? updated : t)));
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to move ticket';
      setActionError(msg);
    }
  };

  const handleRunTicket = async (ticket: Ticket) => {
    await handleMoveTicket(ticket.id, 'in_progress');
  };

  const handleApproveTicket = async (ticket: Ticket) => {
    await handleMoveTicket(ticket.id, 'done');
  };

  const handleStopTicket = async (ticketId: string) => {
    if (!projectId) return;
    try {
      await cancelTicketExecution(projectId, ticketId);
      // Move back to backlog so the ticket leaves the Running strip
      const updated = await updateTicket(projectId, ticketId, { column_id: 'backlog' });
      setTickets((prev) => prev.map((t) => (t.id === ticketId ? updated : t)));
    } catch (error) {
      console.error('Failed to stop ticket:', error);
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

  const openEditTicket = (ticket: Ticket) => {
    setEditTicket(ticket);
    setEditTitle(ticket.title);
    setEditDescription(ticket.description || '');
    setEditPriority(ticket.priority);
    const nodeIds = ticket.associated_node_ids ?? [];
    const edgeIds = ticket.associated_edge_ids ?? [];
    const isAll = nodeIds.length === 1 && nodeIds[0] === '*';
    setEditAllNodesAndEdges(isAll);
    setEditNodeIds(isAll ? [] : nodeIds);
    setEditEdgeIds(isAll ? [] : edgeIds);
  };

  const handleSaveTicket = async () => {
    if (!projectId || !editTicket) return;
    try {
      const updated = await updateTicket(projectId, editTicket.id, {
        title: editTitle.trim(),
        description: editDescription.trim() || undefined,
        priority: editPriority,
        associated_node_ids: editAllNodesAndEdges ? ['*'] : editNodeIds,
        associated_edge_ids: editAllNodesAndEdges ? ['*'] : editEdgeIds,
      });
      setTickets((prev) => prev.map((t) => (t.id === editTicket.id ? updated : t)));
      setEditTicket(null);
    } catch (error) {
      console.error('Failed to update ticket:', error);
    }
  };

  const handleAddTicket = async () => {
    if (!newTicketTitle.trim() || !projectId) return;
    setAddTicketError(null);
    setAddTicketLoading(true);
    try {
      const data = await createTicket(projectId, {
        column_id: 'backlog',
        title: newTicketTitle.trim(),
        description: newTicketDescription.trim() || undefined,
        priority: newTicketPriority,
        status: 'todo',
        associated_node_ids: newTicketAllNodesAndEdges ? ['*'] : newTicketNodeIds,
        associated_edge_ids: newTicketAllNodesAndEdges ? ['*'] : newTicketEdgeIds,
      });
      setTickets((prev) => [...prev, data]);
      setNewTicketTitle('');
      setNewTicketDescription('');
      setNewTicketPriority('medium');
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
    setNewTicketNodeIds([]);
    setNewTicketEdgeIds([]);
    setNewTicketAllNodesAndEdges(false);
    setAddTicketError(null);
    setCreateTicketOpen(true);
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
        <CircularProgress />
      </Box>
    );
  }

  const inProgressTickets = tickets.filter((t) => t.column_id === 'in_progress');
  const boardColumns = columns.filter((c) => BOARD_COLUMN_IDS.has(c.id));

  return (
    <Box>
      {/* Header */}
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

      {/* Action error */}
      <Collapse in={!!actionError}>
        {actionError && (
          <Alert severity="error" onClose={() => setActionError(null)} sx={{ mb: 2 }}>
            {actionError}
          </Alert>
        )}
      </Collapse>

      {/* Running strip */}
      {projectId && (
        <RunningStrip
          tickets={inProgressTickets}
          projectId={projectId}
          onStop={handleStopTicket}
          onTicketUpdated={(t) => setTickets((prev) => prev.map((x) => (x.id === t.id ? t : x)))}
        />
      )}

      {/* Board — Backlog / In Review / Done only */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box sx={{ display: 'flex', gap: 3, alignItems: 'stretch' }}>
          {boardColumns.map((column) => {
            const colTickets = tickets.filter((t) => t.column_id === column.id);
            return (
              <Box key={column.id} sx={{ flex: 1, minWidth: 220, display: 'flex', flexDirection: 'column' }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                  <Typography variant="h6" fontWeight="bold">
                    {column.title}
                  </Typography>
                  {colTickets.length > 0 && (
                    <Chip label={colTickets.length} size="small" sx={{ height: 18, fontSize: '0.65rem' }} />
                  )}
                </Box>
                <Paper
                  sx={{
                    flex: 1,
                    minHeight: 360,
                    maxHeight: '70vh',
                    overflowY: 'auto',
                    p: 1.5,
                    backgroundColor: 'background.default',
                  }}
                >
                  {colTickets.map((ticket) => (
                    <TicketCard
                      key={ticket.id}
                      ticket={ticket}
                      columnId={column.id}
                      projectId={projectId!}
                      graphNodes={graphNodes}
                      missingRequired={missingRequired}
                      onEdit={openEditTicket}
                      onRun={handleRunTicket}
                      onApprove={handleApproveTicket}
                      onDelete={handleDeleteTicket}
                    />
                  ))}
                  {colTickets.length === 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ p: 1 }}>
                      No tickets
                    </Typography>
                  )}
                </Paper>
              </Box>
            );
          })}
        </Box>
      </Paper>

      {/* Create ticket dialog */}
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
              autoFocus
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
                    : (selected as string[]).map((id) => graphNodes.find((n) => n.id === id)?.label ?? id).join(', ') || 'None'
                }
              >
                {graphNodes.map((n) => (
                  <MenuItem key={n.id} value={n.id}>{n.label}</MenuItem>
                ))}
                {graphNodes.length === 0 && <MenuItem disabled>No nodes in graph yet</MenuItem>}
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
                    : (selected as string[]).map((id) => graphEdges.find((edge) => edge.id === id)?.label ?? id).join(', ') || 'None'
                }
              >
                {graphEdges.map((edge) => (
                  <MenuItem key={edge.id} value={edge.id}>{edge.label}</MenuItem>
                ))}
                {graphEdges.length === 0 && <MenuItem disabled>No edges in graph yet</MenuItem>}
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
          <Button
            variant="contained"
            onClick={handleAddTicket}
            disabled={!newTicketTitle.trim() || addTicketLoading}
          >
            {addTicketLoading ? 'Creating…' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit ticket dialog */}
      <Dialog open={!!editTicket} onClose={() => setEditTicket(null)} maxWidth="md" fullWidth>
        {editTicket && (
          <>
            <DialogTitle>Edit ticket</DialogTitle>
            <DialogContent>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
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
                        : (selected as string[]).map((id) => graphNodes.find((n) => n.id === id)?.label ?? id).join(', ') || 'None'
                    }
                  >
                    {graphNodes.map((n) => (
                      <MenuItem key={n.id} value={n.id}>{n.label}</MenuItem>
                    ))}
                    {graphNodes.length === 0 && <MenuItem disabled>No nodes in graph yet</MenuItem>}
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
                        : (selected as string[]).map((id) => graphEdges.find((e) => e.id === id)?.label ?? id).join(', ') || 'None'
                    }
                  >
                    {graphEdges.map((e) => (
                      <MenuItem key={e.id} value={e.id}>{e.label}</MenuItem>
                    ))}
                    {graphEdges.length === 0 && <MenuItem disabled>No edges in graph yet</MenuItem>}
                  </Select>
                </FormControl>
              </Box>
            </DialogContent>
            <DialogActions>
              <Button
                color="error"
                onClick={() => { handleDeleteTicket(editTicket.id); }}
                sx={{ mr: 'auto' }}
              >
                Delete
              </Button>
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
          <Typography variant="h6">Notes</Typography>
          <Button variant="outlined" size="small" onClick={openCreateNote}>
            Create note
          </Button>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {notes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No notes yet.
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
                  setNewNoteNodeIds(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)
                }
                renderValue={(selected) =>
                  (selected as string[]).map((id) => graphNodes.find((n) => n.id === id)?.label ?? id).join(', ') || 'None'
                }
              >
                {graphNodes.map((n) => <MenuItem key={n.id} value={n.id}>{n.label}</MenuItem>)}
                {graphNodes.length === 0 && <MenuItem disabled>No nodes in graph yet</MenuItem>}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Edges</InputLabel>
              <Select
                multiple
                value={newNoteEdgeIds}
                label="Edges"
                onChange={(e) =>
                  setNewNoteEdgeIds(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)
                }
                renderValue={(selected) =>
                  (selected as string[]).map((id) => graphEdges.find((edge) => edge.id === id)?.label ?? id).join(', ') || 'None'
                }
              >
                {graphEdges.map((edge) => <MenuItem key={edge.id} value={edge.id}>{edge.label}</MenuItem>)}
                {graphEdges.length === 0 && <MenuItem disabled>No edges in graph yet</MenuItem>}
              </Select>
            </FormControl>
            <Collapse in={!!addNoteError}>
              {addNoteError && (
                <Alert severity="error" onClose={() => setAddNoteError(null)}>{addNoteError}</Alert>
              )}
            </Collapse>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateNoteOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleAddNote} disabled={!newNoteTitle.trim() || addNoteLoading}>
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
                      setEditNoteNodeIds(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)
                    }
                    renderValue={(selected) =>
                      (selected as string[]).map((id) => graphNodes.find((n) => n.id === id)?.label ?? id).join(', ') || 'None'
                    }
                  >
                    {graphNodes.map((n) => <MenuItem key={n.id} value={n.id}>{n.label}</MenuItem>)}
                    {graphNodes.length === 0 && <MenuItem disabled>No nodes in graph yet</MenuItem>}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Edges</InputLabel>
                  <Select
                    multiple
                    value={editNoteEdgeIds}
                    label="Edges"
                    onChange={(e) =>
                      setEditNoteEdgeIds(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)
                    }
                    renderValue={(selected) =>
                      (selected as string[]).map((id) => graphEdges.find((e) => e.id === id)?.label ?? id).join(', ') || 'None'
                    }
                  >
                    {graphEdges.map((edge) => <MenuItem key={edge.id} value={edge.id}>{edge.label}</MenuItem>)}
                    {graphEdges.length === 0 && <MenuItem disabled>No edges in graph yet</MenuItem>}
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

// ---------------------------------------------------------------------------
// Ticket card
// ---------------------------------------------------------------------------

interface TicketCardProps {
  ticket: Ticket;
  columnId: string;
  projectId: string;
  graphNodes: GraphNodeOption[];
  missingRequired: SettingIssue[];
  onEdit: (ticket: Ticket) => void;
  onRun: (ticket: Ticket) => void;
  onApprove: (ticket: Ticket) => void;
  onDelete: (ticketId: string) => void;
}

const TicketCard: React.FC<TicketCardProps> = ({
  ticket,
  columnId,
  projectId,
  graphNodes,
  missingRequired,
  onEdit,
  onRun,
  onApprove,
  onDelete,
}) => {
  const [running, setRunning] = useState(false);
  const [approving, setApproving] = useState(false);

  const handleRun = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRunning(true);
    try {
      await onRun(ticket);
    } finally {
      setRunning(false);
    }
  };

  const handleApprove = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setApproving(true);
    try {
      await onApprove(ticket);
    } finally {
      setApproving(false);
    }
  };

  return (
    <Card
      sx={{
        mb: 1.5,
        borderLeft: 4,
        borderLeftColor:
          ticket.priority === 'high'
            ? 'error.main'
            : ticket.priority === 'medium'
              ? 'warning.main'
              : 'success.main',
      }}
    >
      <CardContent onClick={() => onEdit(ticket)} sx={{ cursor: 'pointer', pb: '8px !important' }}>
        <Typography variant="subtitle2" fontWeight="bold">
          {ticket.title}
        </Typography>
        {ticket.description && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {ticket.description.length > 100
              ? `${ticket.description.slice(0, 100)}…`
              : ticket.description}
          </Typography>
        )}
        <Box sx={{ mt: 1, display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip
            label={ticket.priority}
            size="small"
            color={PRIORITY_COLOR[ticket.priority] ?? 'default'}
            sx={{ height: 16, fontSize: '0.6rem' }}
          />
          {ticket.pr_url && (
            <>
              <Typography
                component={Link}
                to={`/projects/${projectId}/review/${ticket.id}`}
                onClick={(e: React.MouseEvent) => e.stopPropagation()}
                variant="caption"
                sx={{ color: 'primary.main', textDecoration: 'none' }}
              >
                Review
              </Typography>
              <Typography variant="caption" color="text.secondary">·</Typography>
              <Typography
                component="a"
                href={ticket.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e: React.MouseEvent) => e.stopPropagation()}
                variant="caption"
                sx={{ color: 'text.secondary', textDecoration: 'none' }}
              >
                PR{ticket.pr_number ? ` #${ticket.pr_number}` : ''}
              </Typography>
            </>
          )}
        </Box>
      </CardContent>
      <CardActions sx={{ justifyContent: 'space-between', pt: 0, px: 1.5, pb: 1 }}>
        <Box>
          {columnId === 'backlog' && (() => {
            const noGraph = graphNodes.length === 0;
            const blocked = missingRequired.length > 0 || noGraph;
            const tooltipLines: string[] = [];
            if (noGraph) tooltipLines.push('Add at least one node to the graph first.');
            missingRequired.forEach((s) => tooltipLines.push(`Missing: ${s.label} — ${s.reason}`));
            const tooltipText = blocked
              ? tooltipLines.join('\n')
              : 'Run ticket';
            return (
              <Tooltip
                title={
                  blocked ? (
                    <Box component="span" sx={{ whiteSpace: 'pre-line', display: 'block' }}>
                      {tooltipLines.join('\n')}
                    </Box>
                  ) : 'Run ticket'
                }
              >
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    color={blocked ? 'inherit' : 'primary'}
                    startIcon={running ? <CircularProgress size={12} color="inherit" /> : <PlayArrowIcon fontSize="small" />}
                    disabled={running || blocked}
                    onClick={handleRun}
                    sx={blocked ? { opacity: 0.5 } : undefined}
                  >
                    {running ? 'Starting…' : 'Run'}
                  </Button>
                </span>
              </Tooltip>
            );
          })()}
          {columnId === 'in_review' && (
            <Button
              size="small"
              variant="outlined"
              color="success"
              startIcon={approving ? <CircularProgress size={12} color="inherit" /> : <CheckCircleOutlineIcon fontSize="small" />}
              disabled={approving}
              onClick={handleApprove}
            >
              {approving ? 'Approving…' : 'Approve'}
            </Button>
          )}
        </Box>
        <Tooltip title="Delete ticket">
          <IconButton
            size="small"
            color="error"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(ticket.id);
            }}
            aria-label="Delete ticket"
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </CardActions>
    </Card>
  );
};

export default KanbanPage;
