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
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch';
import StopIcon from '@mui/icons-material/Stop';
import ListAltIcon from '@mui/icons-material/ListAlt';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import BlockIcon from '@mui/icons-material/Block';
import {
  getKanban,
  getProject,
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
  getExecutionReady,
  startProject,
  rerunTicketFromCurrentFrontier,
  AGENTHUB_URL,
  ticketChannelName,
  type Project,
  type Ticket,
  type KanbanColumn,
  type Note,
  type ExecutionLogEntry,
  type ReadyMissing,
} from '../utils/api';
import { LineageField } from '../components/LineageField';

interface GraphNodeOption { id: string; label: string; }
interface GraphEdgeOption { id: string; label: string; }

const DEFAULT_COLUMNS: KanbanColumn[] = [
  { id: 'backlog', title: 'Backlog', order: 0 },
  { id: 'queued', title: 'Queued', order: 1 },
  { id: 'in_progress', title: 'In Progress', order: 2 },
  { id: 'done', title: 'Done', order: 3 },
];

const COLUMN_TITLE_BY_ID: Record<string, string> = {
  backlog: 'Backlog',
  queued: 'Queued',
  in_progress: 'In Progress',
  done: 'Done',
};

const CANONICAL_COLUMN_ORDER: Record<string, number> = {
  backlog: 0,
  queued: 1,
  in_progress: 2,
  done: 3,
};

/** Columns shown in the board — In Progress is intentionally excluded (shown in Running strip above). */
const BOARD_COLUMN_IDS = new Set(['backlog', 'queued', 'done']);

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

  const fetchAll = useRef(async (ticketList: typeof tickets) => {
    const updates: Record<string, ExecutionLogEntry[]> = {};
    await Promise.all(
      ticketList.map(async (t) => {
        try {
          updates[t.id] = await getTicketLogs(projectId, t.id);
        } catch {
          updates[t.id] = [];
        }
      })
    );
    setLogs((prev) => ({ ...prev, ...updates }));
  });

  useEffect(() => {
    if (tickets.length === 0) {
      setLogs({});
      return;
    }

    fetchAll.current(tickets);
    const interval = logsModal ? 3_000 : 10_000;
    intervalRef.current = setInterval(() => fetchAll.current(tickets), interval);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [ticketIds, projectId, logsModal]); // logsModal included so interval speeds up when modal is open

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
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
                    <Typography variant="body2" fontWeight={600} noWrap>
                      {ticket.title}
                    </Typography>
                  </Box>
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
  const [newTicketDependsOn, setNewTicketDependsOn] = useState<string[]>([]);
  const [newTicketRationale, setNewTicketRationale] = useState('');
  const [newTicketAcceptanceCriteria, setNewTicketAcceptanceCriteria] = useState('');
  const [newTicketConstraints, setNewTicketConstraints] = useState('');
  const [addTicketLoading, setAddTicketLoading] = useState(false);
  const [addTicketError, setAddTicketError] = useState<string | null>(null);

  // Edit ticket dialog
  const [editTicket, setEditTicket] = useState<Ticket | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editPriority, setEditPriority] = useState<string>('medium');
  const [editColumnId, setEditColumnId] = useState<string>('backlog');
  const [editNodeIds, setEditNodeIds] = useState<string[]>([]);
  const [editEdgeIds, setEditEdgeIds] = useState<string[]>([]);
  const [editAllNodesAndEdges, setEditAllNodesAndEdges] = useState(false);
  const [editDependsOn, setEditDependsOn] = useState<string[]>([]);
  const [editIntentStatus, setEditIntentStatus] = useState<string>('ready');
  const [editRationale, setEditRationale] = useState('');
  const [editAcceptanceCriteria, setEditAcceptanceCriteria] = useState('');
  const [editConstraints, setEditConstraints] = useState('');

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

  const [missingRequired, setMissingRequired] = useState<ReadyMissing[]>([]);

  // Go button
  const [goLoading, setGoLoading] = useState(false);
  const [goResult, setGoResult] = useState<string | null>(null);
  const [gitMode, setGitMode] = useState<string>('swarm');
  const [project, setProject] = useState<Project | null>(null);

  useEffect(() => {
    if (projectId) fetchKanban();
  }, [projectId]);

  const [workspaceEnabled, setWorkspaceEnabled] = useState(false);
  useEffect(() => {
    getExecutionReady()
      .then((res) => {
        setMissingRequired(res.missing ?? []);
        setWorkspaceEnabled(res.features?.composite_workspace ?? false);
      })
      .catch(() => {});
  }, []);

  // Poll ticket statuses while any ticket is actively running so the Running strip
  // and log poller stay current without a full page refresh.
  // Also poll at a slower rate unconditionally so is_running stays fresh after jobs start.
  const activeCount = tickets.filter((t) => t.is_running).length;
  useEffect(() => {
    if (!projectId) return;
    const interval = activeCount > 0 ? 5_000 : 15_000;
    const id = setInterval(async () => {
      try {
        const updated = await getTickets(projectId);
        setTickets(updated as Ticket[]);
      } catch {
        // non-fatal
      }
    }, interval);
    return () => clearInterval(id);
  }, [projectId, activeCount]);

  const fetchKanban = async () => {
    if (!projectId) return;
    try {
      const [kanbanRes, ticketsRes, notesRes, graphRes, projectRes] = await Promise.all([
        getKanban(projectId),
        getTickets(projectId),
        getNotes(projectId),
        getGraph(projectId).catch(() => ({ nodes: [], edges: [] })),
        getProject(projectId),
      ]);

      setProject(projectRes);
      setGitMode(projectRes.git_mode ?? 'swarm');

      const apiColumns =
        kanbanRes.columns && kanbanRes.columns.length > 0 ? kanbanRes.columns : DEFAULT_COLUMNS;
      // Always ensure all system columns are present (e.g. queued may be missing from older boards)
      const columnIds = new Set(apiColumns.map((c: KanbanColumn) => c.id));
      const nextColumns = [...apiColumns];
      for (const col of DEFAULT_COLUMNS) {
        if (!columnIds.has(col.id)) {
          columnIds.add(col.id);
          nextColumns.push(col);
        }
      }
      const ticketColumnIds = [...new Set((ticketsRes as Ticket[]).map((t) => t.column_id))];
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

  const handleGo = async () => {
    if (!projectId) return;
    setGoLoading(true);
    setGoResult(null);
    try {
      const result = await startProject(projectId);
      const updated = await getTickets(projectId);
      setTickets(updated as Ticket[]);
      setGoResult(`${result.queued} queued, ${result.dispatched} dispatched`);
      setTimeout(() => setGoResult(null), 4000);
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to start project';
      setActionError(msg);
    } finally {
      setGoLoading(false);
    }
  };

  const handleStopTicket = async (ticketId: string) => {
    if (!projectId) return;
    try {
      await cancelTicketExecution(projectId, ticketId);
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
    setEditColumnId(ticket.column_id);
    const nodeIds = ticket.associated_node_ids ?? [];
    const edgeIds = ticket.associated_edge_ids ?? [];
    const isAll = nodeIds.length === 1 && nodeIds[0] === '*';
    setEditAllNodesAndEdges(isAll);
    setEditNodeIds(isAll ? [] : nodeIds);
    setEditEdgeIds(isAll ? [] : edgeIds);
    setEditDependsOn(ticket.depends_on_ticket_ids ?? []);
    setEditIntentStatus(ticket.intent_status ?? 'ready');
    setEditRationale(ticket.rationale ?? '');
    setEditAcceptanceCriteria(ticket.acceptance_criteria ?? '');
    setEditConstraints(ticket.constraints ?? '');
  };

  const handleSaveTicket = async () => {
    if (!projectId || !editTicket) return;
    try {
      const updated = await updateTicket(projectId, editTicket.id, {
        title: editTitle.trim(),
        description: editDescription.trim() || undefined,
        priority: editPriority,
        column_id: editColumnId,
        associated_node_ids: editAllNodesAndEdges ? ['*'] : editNodeIds,
        associated_edge_ids: editAllNodesAndEdges ? ['*'] : editEdgeIds,
        depends_on_ticket_ids: editDependsOn,
        intent_status: editIntentStatus as any,
        rationale: editRationale.trim() || undefined,
        acceptance_criteria: editAcceptanceCriteria.trim() || undefined,
        constraints: editConstraints.trim() || undefined,
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
        depends_on_ticket_ids: newTicketDependsOn,
        rationale: newTicketRationale.trim() || undefined,
        acceptance_criteria: newTicketAcceptanceCriteria.trim() || undefined,
        constraints: newTicketConstraints.trim() || undefined,
      });
      setTickets((prev) => [...prev, data]);
      setNewTicketTitle('');
      setNewTicketDescription('');
      setNewTicketPriority('medium');
      setNewTicketNodeIds([]);
      setNewTicketEdgeIds([]);
      setNewTicketAllNodesAndEdges(false);
      setNewTicketDependsOn([]);
      setNewTicketRationale('');
      setNewTicketAcceptanceCriteria('');
      setNewTicketConstraints('');
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
    setNewTicketDependsOn([]);
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

  const isTicketBlocked = (ticket: Ticket): boolean => {
    const deps = ticket.depends_on_ticket_ids ?? [];
    if (deps.length === 0) return false;
    return deps.some((depId) => {
      const dep = tickets.find((t) => t.id === depId);
      return !dep || dep.column_id !== 'done';
    });
  };

  const inProgressTickets = tickets.filter((t) => t.is_running);
  const boardColumns = columns.filter((c) => BOARD_COLUMN_IDS.has(c.id));

  return (
    <Box>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3, flexWrap: 'wrap', gap: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Typography variant="h4">Kanban Board</Typography>
          {goResult && (
            <Typography variant="body2" color="success.main" fontWeight="medium">
              ✓ {goResult}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Tooltip title={tickets.filter((t) => t.column_id === 'backlog').length === 0 ? 'No backlog tickets to start' : 'Move all backlog tickets to queued and dispatch wave-0'}>
            <span>
              <Button
                variant="contained"
                size="small"
                color="success"
                startIcon={goLoading ? <CircularProgress size={14} color="inherit" /> : <RocketLaunchIcon />}
                onClick={handleGo}
                disabled={goLoading || tickets.filter((t) => t.column_id === 'backlog').length === 0}
              >
                {goLoading ? 'Starting…' : 'Go'}
              </Button>
            </span>
          </Tooltip>
          <Button variant="contained" size="small" onClick={openCreateTicket}>
            Create ticket
          </Button>
          <Button variant="outlined" size="small" onClick={openEditColumns}>
            Edit columns
          </Button>
          <Button component={Link} to={`/projects/${projectId}/intents`} variant="outlined" size="small">
            Intent Inbox
          </Button>
          <Button component={Link} to={`/projects/${projectId}/graph`} variant="outlined" size="small">
            Graph
          </Button>
          <Button component={Link} to={`/projects/${projectId}/ship`} variant="outlined" size="small">
            Ship Room
          </Button>
          {workspaceEnabled && (
            <Button component={Link} to={`/projects/${projectId}/workspace`} variant="outlined" size="small">
              Workspace
            </Button>
          )}
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

      {/* Board — Backlog / Queued / Done columns */}
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
                      isBlocked={isTicketBlocked(ticket)}
                      allTickets={tickets}
                      onEdit={openEditTicket}
                      onTicketUpdated={(updatedTicket) => setTickets((prev) => prev.map((t) => (t.id === updatedTicket.id ? updatedTicket : t)))}
                      onRun={handleRunTicket}
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
            <TextField
              label="Rationale — why this matters"
              value={newTicketRationale}
              onChange={(e) => setNewTicketRationale(e.target.value)}
              multiline
              minRows={2}
              fullWidth
              size="small"
              placeholder="Why is this work valuable? What problem does it solve?"
            />
            <TextField
              label="Acceptance criteria — what done looks like"
              value={newTicketAcceptanceCriteria}
              onChange={(e) => setNewTicketAcceptanceCriteria(e.target.value)}
              multiline
              minRows={2}
              fullWidth
              size="small"
              placeholder="List the specific outcomes that define completion."
            />
            <TextField
              label="Constraints — limits and non-goals"
              value={newTicketConstraints}
              onChange={(e) => setNewTicketConstraints(e.target.value)}
              multiline
              minRows={2}
              fullWidth
              size="small"
              placeholder="What should the agent NOT do? Any hard limits?"
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
            <FormControl size="small" fullWidth>
              <InputLabel>Depends on (runs after)</InputLabel>
              <Select
                multiple
                value={newTicketDependsOn}
                label="Depends on (runs after)"
                onChange={(e) => setNewTicketDependsOn(typeof e.target.value === 'string' ? [] : e.target.value)}
                renderValue={(selected) =>
                  (selected as string[]).map((id) => tickets.find((t) => t.id === id)?.title ?? id).join(', ') || 'None'
                }
              >
                {tickets.length === 0 && <MenuItem disabled>No other tickets yet</MenuItem>}
                {tickets.map((t) => (
                  <MenuItem key={t.id} value={t.id}>{t.title}</MenuItem>
                ))}
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
                <Paper variant="outlined" sx={{ p: 1.5, bgcolor: 'background.default' }}>
                  <Stack spacing={0.5}>
                    <LineageField label="Ticket base" value={editTicket.base_leaf_id} />
                    <LineageField label="Accepted frontier" value={editTicket.accepted_frontier_id ?? project?.accepted_frontier_id ?? project?.shipped_frontier} />
                    {editTicket.stale !== null && editTicket.stale !== undefined && (
                      <Typography variant="caption" color={editTicket.stale ? 'warning.main' : 'text.secondary'}>
                        {editTicket.stale ? (editTicket.stale_reason || 'Ticket base differs from the current frontier.') : 'Ticket base matches the current frontier.'}
                      </Typography>
                    )}
                  </Stack>
                </Paper>
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
                <TextField
                  label="Rationale — why this matters"
                  value={editRationale}
                  onChange={(e) => setEditRationale(e.target.value)}
                  multiline
                  minRows={2}
                  fullWidth
                  size="small"
                  placeholder="Why is this work valuable? What problem does it solve?"
                />
                <TextField
                  label="Acceptance criteria — what done looks like"
                  value={editAcceptanceCriteria}
                  onChange={(e) => setEditAcceptanceCriteria(e.target.value)}
                  multiline
                  minRows={2}
                  fullWidth
                  size="small"
                  placeholder="List the specific outcomes that define completion."
                />
                <TextField
                  label="Constraints — limits and non-goals"
                  value={editConstraints}
                  onChange={(e) => setEditConstraints(e.target.value)}
                  multiline
                  minRows={2}
                  fullWidth
                  size="small"
                  placeholder="What should the agent NOT do? Any hard limits?"
                />
                <FormControl size="small" fullWidth>
                  <InputLabel>Intent status</InputLabel>
                  <Select
                    value={editIntentStatus}
                    label="Intent status"
                    onChange={(e) => setEditIntentStatus(e.target.value)}
                  >
                    <MenuItem value="draft">Draft — not yet fully defined</MenuItem>
                    <MenuItem value="ready">Ready — approved for agents</MenuItem>
                    <MenuItem value="active">Active — work is in progress</MenuItem>
                    <MenuItem value="blocked">Blocked — external blocker</MenuItem>
                    <MenuItem value="archived">Archived — no longer relevant</MenuItem>
                  </Select>
                </FormControl>
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
                  <InputLabel>Status</InputLabel>
                  <Select
                    value={editColumnId}
                    label="Status"
                    onChange={(e) => setEditColumnId(e.target.value)}
                  >
                    <MenuItem value="backlog">Backlog</MenuItem>
                    <MenuItem value="in_progress">In Progress</MenuItem>

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
                <FormControl size="small" fullWidth>
                  <InputLabel>Depends on (runs after)</InputLabel>
                  <Select
                    multiple
                    value={editDependsOn}
                    label="Depends on (runs after)"
                    onChange={(e) => setEditDependsOn(typeof e.target.value === 'string' ? [] : e.target.value)}
                    renderValue={(selected) =>
                      (selected as string[]).map((id) => tickets.find((t) => t.id === id)?.title ?? id).join(', ') || 'None'
                    }
                  >
                    {tickets.filter((t) => t.id !== editTicket.id).map((t) => (
                      <MenuItem key={t.id} value={t.id}>{t.title}</MenuItem>
                    ))}
                    {tickets.length <= 1 && <MenuItem disabled>No other tickets yet</MenuItem>}
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
  missingRequired: ReadyMissing[];
  isBlocked: boolean;
  allTickets: Ticket[];
  onEdit: (ticket: Ticket) => void;
  onTicketUpdated: (ticket: Ticket) => void;
  onRun: (ticket: Ticket) => void;
  onDelete: (ticketId: string) => void;
}

const TicketCard: React.FC<TicketCardProps> = ({
  ticket,
  columnId,
  projectId,
  graphNodes,
  missingRequired,
  isBlocked,
  allTickets,
  onEdit,
  onTicketUpdated,
  onRun,
  onDelete,
}) => {
  const [running, setRunning] = useState(false);
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [rerunResult, setRerunResult] = useState<string | null>(null);
  const [competeDialogOpen, setCompeteDialogOpen] = useState(false);
  const [rerunAttemptCount, setRerunAttemptCount] = useState<number | null>(null);

  const latestAttemptCount = ticket.attempts_count ?? ticket.latest_attempt?.attempt_num ?? null;
  const ticketChannel = ticketChannelName(ticket.id);
  const rerunPending = rerunAttemptCount !== null;

  const handleRun = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRunning(true);
    try {
      await onRun(ticket);
    } finally {
      setRunning(false);
    }
  };

  const startRerun = async (attemptCount: number) => {
    setRerunAttemptCount(attemptCount);
    setRerunError(null);
    setRerunResult(null);
    try {
      const updated = await rerunTicketFromCurrentFrontier(
        projectId,
        ticket.id,
        attemptCount > 1 ? { attemptCount } : undefined,
      );
      onTicketUpdated(updated);
      setRerunResult(
        attemptCount > 1
          ? `Queued ${attemptCount} competing attempts from current frontier.`
          : "Queued rerun from current frontier.",
      );
      setCompeteDialogOpen(false);
    } catch (error) {
      setRerunError(error instanceof Error ? error.message : 'Failed to rerun ticket from frontier');
    } finally {
      setRerunAttemptCount(null);
    }
  };

  const handleRerun = (e: React.MouseEvent) => {
    e.stopPropagation();
    void startRerun(1);
  };

  const handleOpenCompetingAttempts = (e: React.MouseEvent) => {
    e.stopPropagation();
    setRerunError(null);
    setRerunResult(null);
    setCompeteDialogOpen(true);
  };

  const handleCloseCompetingAttempts = () => {
    if (rerunPending) return;
    setCompeteDialogOpen(false);
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
          {isBlocked && columnId === 'backlog' && (() => {
            const blockingTitles = (ticket.depends_on_ticket_ids ?? [])
              .map((id) => allTickets.find((t) => t.id === id))
              .filter((t) => t && t.column_id !== 'done')
              .map((t) => t!.title);
            return (
              <Tooltip title={`Waiting on: ${blockingTitles.join(', ')}`}>
                <Chip
                  icon={<BlockIcon sx={{ fontSize: '0.65rem !important' }} />}
                  label="Blocked"
                  size="small"
                  color="warning"
                  variant="outlined"
                  sx={{ height: 16, fontSize: '0.6rem' }}
                />
              </Tooltip>
            );
          })()}
          {ticket.display_state && !['queued', 'running', 'draft'].includes(ticket.display_state) && (
            <Chip
              label={ticket.display_state.replace(/_/g, ' ')}
              size="small"
              color={
                ticket.display_state === 'shipped' ? 'success'
                : ticket.display_state === 'accepted' ? 'success'
                : ticket.display_state === 'failed' ? 'error'
                : ticket.display_state === 'blocked' ? 'error'
                : ticket.display_state === 'stale' ? 'warning'
                : ticket.display_state === 'attempt_ready' ? 'info'
                : 'default'
              }
              variant="outlined"
              sx={{ height: 16, fontSize: '0.6rem' }}
            />
          )}
          {(ticket.failed_count ?? 0) > 0 && (
            <Chip
              label={`Failed ${ticket.failed_count}×`}
              size="small"
              color="error"
              variant="outlined"
              sx={{ height: 16, fontSize: '0.6rem' }}
            />
          )}
          {latestAttemptCount !== null && latestAttemptCount !== undefined && latestAttemptCount > 0 && (
            <Chip
              label={`${latestAttemptCount} attempt${latestAttemptCount === 1 ? '' : 's'}`}
              size="small"
              variant="outlined"
              sx={{ height: 16, fontSize: '0.6rem' }}
            />
          )}
          {ticket.latest_attempt && (
            <Chip
              label={`${ticket.latest_attempt.status}${ticket.latest_attempt.short_commit_hash ? ' · ' + ticket.latest_attempt.short_commit_hash : ''}`}
              size="small"
              color={
                ticket.latest_attempt.status === 'accepted' || ticket.latest_attempt.status === 'shipped'
                  ? 'success'
                  : ticket.latest_attempt.status === 'failed' || ticket.latest_attempt.status === 'rejected'
                  ? 'error'
                  : 'default'
              }
              variant="outlined"
              sx={{ height: 16, fontSize: '0.6rem', maxWidth: 180 }}
              title={ticket.latest_attempt.summary ?? undefined}
            />
          )}
          {ticket.latest_attempt?.stale && (
            <Chip label="stale" size="small" color="warning" variant="outlined" sx={{ height: 16, fontSize: '0.6rem' }} />
          )}
          {ticket.latest_attempt?.wave_num !== undefined && (
            <Typography variant="caption" color="text.secondary">
              wave {ticket.latest_attempt.wave_num}
            </Typography>
          )}
          {AGENTHUB_URL && ticketChannel && (
            <Tooltip title={`AgentHub channel: ${ticketChannel}`}>
              <Typography
                component="a"
                href={`${AGENTHUB_URL}`}
                target="_blank"
                rel="noopener noreferrer"
                variant="caption"
                onClick={(e: React.MouseEvent) => e.stopPropagation()}
                sx={{ color: 'primary.dark', textDecoration: 'none', fontSize: '0.6rem' }}
              >
                #{ticketChannel.slice(0, 14)}…
              </Typography>
            </Tooltip>
          )}
        </Box>
        <Stack spacing={0.25} sx={{ mt: 1 }}>
          <LineageField label="base" value={ticket.base_leaf_id} stopPropagation />
          {ticket.stale_reason && (
            <Typography variant="caption" color="warning.main">
              {ticket.stale_reason}
            </Typography>
          )}
          {rerunResult && (
            <Typography variant="caption" color="success.main">
              {rerunResult}
            </Typography>
          )}
          {rerunError && (
            <Typography variant="caption" color="error.main">
              {rerunError}
            </Typography>
          )}
        </Stack>
      </CardContent>
      <CardActions sx={{ justifyContent: 'space-between', pt: 0, px: 1.5, pb: 1 }}>
        <Box>
          {columnId === 'backlog' && (() => {
            const noGraph = graphNodes.length === 0;
            const blocked = noGraph || missingRequired.length > 0 || isBlocked;
            const tooltipLines: string[] = [];
            if (noGraph) tooltipLines.push('Add at least one node to the graph first.');
            missingRequired.forEach((m) => tooltipLines.push(`Missing: ${m.label} — set ${m.key} in .env`));
            if (isBlocked) {
              const blockingTitles = (ticket.depends_on_ticket_ids ?? [])
                .map((id) => allTickets.find((t) => t.id === id))
                .filter((t) => t && t.column_id !== 'done')
                .map((t) => t!.title);
              tooltipLines.push(`Blocked by: ${blockingTitles.join(', ')}`);
            }
            const tooltipText = blocked ? tooltipLines.join('\n') : 'Run ticket';
            return (
              <Tooltip title={tooltipText}>
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
        </Box>
        {(ticket.stale || ticket.latest_attempt?.stale) && (
          <>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <Button
                size="small"
                color="warning"
                onClick={handleRerun}
                disabled={rerunPending}
              >
                {rerunAttemptCount === 1 ? 'Starting…' : 'Rerun from frontier'}
              </Button>
              <Button
                size="small"
                color="warning"
                variant="outlined"
                onClick={handleOpenCompetingAttempts}
                disabled={rerunPending}
              >
                Run competing attempts
              </Button>
            </Box>
            <Dialog
              open={competeDialogOpen}
              onClose={handleCloseCompetingAttempts}
              maxWidth="xs"
              fullWidth
            >
              <DialogTitle>Run competing attempts</DialogTitle>
              <DialogContent>
                <Typography variant="body2" color="text.secondary" sx={{ pt: 1 }}>
                  Start 2 or 3 fresh attempts from the current frontier for this ticket.
                </Typography>
                {rerunError && (
                  <Alert severity="error" sx={{ mt: 2 }}>
                    {rerunError}
                  </Alert>
                )}
              </DialogContent>
              <DialogActions>
                <Button onClick={handleCloseCompetingAttempts} disabled={rerunPending}>
                  Cancel
                </Button>
                <Button onClick={() => void startRerun(2)} color="warning" disabled={rerunPending}>
                  {rerunAttemptCount === 2 ? 'Starting…' : 'Start 2 attempts'}
                </Button>
                <Button
                  onClick={() => void startRerun(3)}
                  color="warning"
                  variant="contained"
                  disabled={rerunPending}
                >
                  {rerunAttemptCount === 3 ? 'Starting…' : 'Start 3 attempts'}
                </Button>
              </DialogActions>
            </Dialog>
          </>
        )}
      </CardActions>
    </Card>
  );
};

export default KanbanPage;
