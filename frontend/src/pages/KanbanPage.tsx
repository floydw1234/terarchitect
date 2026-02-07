import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Box, Typography, Button, Paper, TextField, Card, CardContent, CardActions, IconButton } from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';

interface Column {
  id: string;
  title: string;
  order: number;
}

interface Ticket {
  id: string;
  column_id: string;
  title: string;
  description: string;
  priority: string;
  status: string;
}

const KanbanPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [columns, setColumns] = useState<Column[]>([
    { id: 'backlog', title: 'Backlog', order: 0 },
    { id: 'in_progress', title: 'In Progress', order: 1 },
    { id: 'done', title: 'Done', order: 2 },
  ]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTicketTitle, setNewTicketTitle] = useState('');

  useEffect(() => {
    fetchKanban();
  }, [projectId]);

  const fetchKanban = async () => {
    try {
      const [kanbanRes, ticketsRes] = await Promise.all([
        fetch(`/api/projects/${projectId}/kanban`),
        fetch(`/api/projects/${projectId}/tickets`),
      ]);
      const kanbanData = await kanbanRes.json();
      const ticketsData = await ticketsRes.json();
      setColumns(kanbanData.columns || columns);
      setTickets(ticketsData);
    } catch (error) {
      console.error('Failed to fetch kanban:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddTicket = async () => {
    if (!newTicketTitle.trim()) return;

    try {
      const response = await fetch(`/api/projects/${projectId}/tickets`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          column_id: 'backlog',
          title: newTicketTitle,
          description: '',
          priority: 'medium',
          status: 'todo',
        }),
      });
      const data = await response.json();
      setTickets((prev) => [...prev, { ...data, column_id: 'backlog' }]);
      setNewTicketTitle('');
    } catch (error) {
      console.error('Failed to create ticket:', error);
    }
  };

  const handleDeleteTicket = async (ticketId: string) => {
    try {
      await fetch(`/api/tickets/${ticketId}`, {
        method: 'DELETE',
      });
      setTickets((prev) => prev.filter((t) => t.id !== ticketId));
    } catch (error) {
      console.error('Failed to delete ticket:', error);
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
        <Typography variant="h4">Kanban Board</Typography>
        <Button variant="contained" color="primary" onClick={handleAddTicket}>
          Add Ticket
        </Button>
      </Box>

      <Paper sx={{ p: 2, backgroundColor: '#1a1a2e' }}>
        <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
          <TextField
            label="New Ticket"
            value={newTicketTitle}
            onChange={(e) => setNewTicketTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleAddTicket();
            }}
            fullWidth
          />
        </Box>

        <Box sx={{ display: 'flex', gap: 3 }}>
          {columns.map((column) => (
            <Box key={column.id} sx={{ flex: 1, minWidth: 250 }}>
              <Typography variant="h6" sx={{ mb: 2, fontWeight: 'bold' }}>
                {column.title}
              </Typography>
              <Paper sx={{ minHeight: 400, p: 2, backgroundColor: '#16162a' }}>
                {tickets
                  .filter((ticket) => ticket.column_id === column.id)
                  .map((ticket) => (
                    <Card
                      key={ticket.id}
                      sx={{
                        mb: 2,
                        backgroundColor: '#1a1a2e',
                        borderLeft: 4,
                        borderLeftColor:
                          ticket.priority === 'high'
                            ? 'error.main'
                            : ticket.priority === 'medium'
                              ? 'warning.main'
                              : 'success.main',
                      }}
                    >
                      <CardContent>
                        <Typography variant="h6">{ticket.title}</Typography>
                        {ticket.description && (
                          <Typography variant="body2" color="textSecondary">
                            {ticket.description}
                          </Typography>
                        )}
                        <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
                          Priority: {ticket.priority}
                        </Typography>
                      </CardContent>
                      <CardActions>
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => handleDeleteTicket(ticket.id)}
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
    </Box>
  );
};

export default KanbanPage;
