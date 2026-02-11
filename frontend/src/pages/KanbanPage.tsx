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
  type Ticket,
  type KanbanColumn,
  type Note,
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
  { id: 'done', title: 'Done', order: 2 },
];

const KanbanPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [columns, setColumns] = useState<KanbanColumn[]>(DEFAULT_COLUMNS);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTicketTitle, setNewTicketTitle] = useState('');
  const [newTicketDescription, setNewTicketDescription] = useState('');
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
  const [graphNodes, setGraphNodes] = useState<GraphNodeOption[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdgeOption[]>([]);
  const [newNoteTitle, setNewNoteTitle] = useState('');
  const [newNoteContent, setNewNoteContent] = useState('');
  const [addNoteLoading, setAddNoteLoading] = useState(false);
  const [addNoteError, setAddNoteError] = useState<string | null>(null);
  const [editNote, setEditNote] = useState<Note | null>(null);
  const [editNoteTitle, setEditNoteTitle] = useState('');
  const [editNoteContent, setEditNoteContent] = useState('');
  const [editNoteNodeId, setEditNoteNodeId] = useState('');
  const [editNoteEdgeId, setEditNoteEdgeId] = useState('');
  const [editColumnsOpen, setEditColumnsOpen] = useState(false);
  const [editColumnTitles, setEditColumnTitles] = useState<{ id: string; title: string; order: number }[]>([]);

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
      setColumns(
        kanbanRes.columns && kanbanRes.columns.length > 0
          ? kanbanRes.columns
          : DEFAULT_COLUMNS
      );
      setTickets(ticketsRes);
      setNotes(notesRes);
      const nodes = Array.isArray(graphRes.nodes) ? graphRes.nodes as Array<{ id?: string; data?: { label?: string } }> : [];
      const edges = Array.isArray(graphRes.edges) ? graphRes.edges as Array<{ id?: string; source?: string; target?: string; data?: { label?: string } }> : [];
      setGraphNodes(nodes.map((n) => ({ id: n.id ?? '', label: (n.data?.label ?? n.id) || 'Unnamed' })));
      setGraphEdges(edges.map((e) => ({
        id: e.id ?? '',
        label: e.data?.label ?? (e.source && e.target ? `${e.source} → ${e.target}` : e.id ?? 'Unnamed'),
      })));
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
        column_id: 'backlog',
        title: newTicketTitle.trim(),
        description: newTicketDescription.trim() || undefined,
        priority: 'medium',
        status: 'todo',
      });
      setTickets((prev) => [...prev, data]);
      setNewTicketTitle('');
      setNewTicketDescription('');
    } catch (error) {
      setAddTicketError(error instanceof Error ? error.message : 'Failed to add ticket');
    } finally {
      setAddTicketLoading(false);
    }
  };

  const openEditTicket = (ticket: Ticket) => {
    setEditTicket(ticket);
    setEditTitle(ticket.title);
    setEditDescription(ticket.description || '');
    setEditPriority(ticket.priority);
    setEditStatus(ticket.status);
    setEditColumnId(ticket.column_id);
    setEditNodeIds(ticket.associated_node_ids ?? []);
    setEditEdgeIds(ticket.associated_edge_ids ?? []);
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
        associated_node_ids: editNodeIds,
        associated_edge_ids: editEdgeIds,
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
      });
      setNotes((prev) => [...prev, data]);
      setNewNoteTitle('');
      setNewNoteContent('');
    } catch (error) {
      setAddNoteError(error instanceof Error ? error.message : 'Failed to add note');
    } finally {
      setAddNoteLoading(false);
    }
  };

  const openEditNote = (note: Note) => {
    setEditNote(note);
    setEditNoteTitle(note.title ?? '');
    setEditNoteContent(note.content ?? '');
    setEditNoteNodeId(note.node_id ?? '');
    setEditNoteEdgeId(note.edge_id ?? '');
  };

  const handleSaveNote = async () => {
    if (!projectId || !editNote) return;
    try {
      const updated = await updateNote(projectId, editNote.id, {
        title: editNoteTitle.trim() || undefined,
        content: editNoteContent.trim() || undefined,
        node_id: editNoteNodeId.trim() || undefined,
        edge_id: editNoteEdgeId.trim() || undefined,
      });
      setNotes((prev) => prev.map((n) => (n.id === editNote.id ? updated : n)));
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
          <Button variant="outlined" size="small" onClick={openEditColumns}>
            Edit columns
          </Button>
          <Button component={Link} to={`/projects/${projectId}/graph`} variant="outlined" size="small">
            Graph
          </Button>
        </Box>
      </Box>

      {/* Add ticket form */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          New ticket
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 500 }}>
          <TextField
            label="Title"
            value={newTicketTitle}
            onChange={(e) => setNewTicketTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddTicket()}
            placeholder="What needs to be done?"
            required
            fullWidth
            size="small"
          />
          <TextField
            label="Description (optional)"
            value={newTicketDescription}
            onChange={(e) => setNewTicketDescription(e.target.value)}
            placeholder="More details..."
            multiline
            minRows={2}
            fullWidth
            size="small"
          />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Button
              variant="contained"
              color="primary"
              onClick={handleAddTicket}
              disabled={!newTicketTitle.trim() || addTicketLoading}
            >
              {addTicketLoading ? 'Adding…' : 'Add ticket'}
            </Button>
          </Box>
        </Box>
        <Collapse in={!!addTicketError}>
          {addTicketError && (
            <Alert severity="error" sx={{ mt: 2 }} onClose={() => setAddTicketError(null)}>
              {addTicketError}
            </Alert>
          )}
        </Collapse>
      </Paper>

      {/* Board */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box sx={{ display: 'flex', gap: 3 }}>
          {columns.map((column) => (
            <Box key={column.id} sx={{ flex: 1, minWidth: 250 }}>
              <Typography variant="h6" sx={{ mb: 2, fontWeight: 'bold' }}>
                {column.title}
              </Typography>
              <Paper sx={{ minHeight: 400, p: 2, backgroundColor: 'background.default' }}>
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
                      </CardContent>
                      <CardActions sx={{ justifyContent: 'space-between', pt: 0 }}>
                        <Box>
                          {columns
                            .filter((col) => col.id !== column.id)
                            .map((col) => (
                              <Button
                                key={col.id}
                                size="small"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleMoveTicket(ticket.id, col.id);
                                }}
                              >
                                → {col.title}
                              </Button>
                            ))}
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

      {/* Ticket edit dialog */}
      <Dialog open={!!editTicket} onClose={() => setEditTicket(null)} maxWidth="sm" fullWidth>
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
                <FormControl size="small" fullWidth>
                  <InputLabel>Nodes</InputLabel>
                  <Select
                    multiple
                    value={editNodeIds}
                    label="Nodes"
                    onChange={(e) => setEditNodeIds(typeof e.target.value === 'string' ? [] : e.target.value)}
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
                    value={editEdgeIds}
                    label="Edges"
                    onChange={(e) => setEditEdgeIds(typeof e.target.value === 'string' ? [] : e.target.value)}
                    renderValue={(selected) =>
                      (selected as string[])
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
        <Typography variant="h6" sx={{ mb: 2 }}>
          Notes
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 500, mb: 3 }}>
          <TextField
            label="Note title"
            value={newNoteTitle}
            onChange={(e) => setNewNoteTitle(e.target.value)}
            placeholder="Title"
            fullWidth
            size="small"
          />
          <TextField
            label="Content"
            value={newNoteContent}
            onChange={(e) => setNewNoteContent(e.target.value)}
            placeholder="Write a note..."
            multiline
            minRows={3}
            fullWidth
            size="small"
          />
          <Button
            variant="outlined"
            onClick={handleAddNote}
            disabled={!newNoteTitle.trim() || addNoteLoading}
          >
            {addNoteLoading ? 'Adding…' : 'Add note'}
          </Button>
        </Box>
        <Collapse in={!!addNoteError}>
          {addNoteError && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setAddNoteError(null)}>
              {addNoteError}
            </Alert>
          )}
        </Collapse>
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
                <TextField
                  label="Node ID"
                  value={editNoteNodeId}
                  onChange={(e) => setEditNoteNodeId(e.target.value)}
                  placeholder="Link to graph node"
                  fullWidth
                  size="small"
                />
                <TextField
                  label="Edge ID"
                  value={editNoteEdgeId}
                  onChange={(e) => setEditNoteEdgeId(e.target.value)}
                  placeholder="Link to graph edge"
                  fullWidth
                  size="small"
                />
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
