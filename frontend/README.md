# Terarchitect Frontend

React application for the Terarchitect visual SDLC orchestrator.

## Features

### Implemented
- Project management (create, list, view details)
- Kanban board with tickets
- Graph editor (basic - displays nodes/edges)
- Dark theme with Material UI

### In Progress
- Full React Flow integration for graph editor
- Ticket comment system
- RAG search interface

### TODO
- Drag-and-drop graph editing
- Node/edge property editing
- Ticket priority/status management
- Real-time updates with WebSockets

## Setup

```bash
cd frontend
npm install
npm start
```

## Environment Variables
- `REACT_APP_API_URL` - Backend API URL (default: http://localhost:5010)
