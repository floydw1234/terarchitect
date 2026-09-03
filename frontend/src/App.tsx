import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Box, CssBaseline, ThemeProvider } from '@mui/material';
import { ThemeContextProvider, useThemeMode } from './contexts/ThemeContext';
import Navbar from './components/Navbar';
import ProjectsPage from './pages/ProjectsPage';
import ProjectPage from './pages/ProjectPage';
import GraphEditorPage from './pages/GraphEditorPage';
import KanbanPage from './pages/KanbanPage';
import AgenthubPage from './pages/AgenthubPage';
import ShipRoomPage from './pages/ShipRoomPage';
import WorkspacePage from './pages/WorkspacePage';
import IntentInboxPage from './pages/IntentInboxPage';
import AttemptDetailPage from './pages/AttemptDetailPage';

function AppContent() {
  const { theme, mode } = useThemeMode();

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Router>
        <Box
          sx={{
            position: 'relative',
            minHeight: '100vh',
            overflow: 'hidden',
            backgroundColor: mode === 'dark' ? 'background.default' : '#E9F7FF',
          }}
        >
          <Box sx={{ position: 'relative', zIndex: 1, display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
            <Navbar />
            <Box sx={{ flex: 1, px: { xs: 2, md: 3 }, pb: 3, pt: { xs: 2, md: 3 } }}>
              <Routes>
                <Route path="/" element={<ProjectsPage />} />
                <Route path="/projects" element={<ProjectsPage />} />
                <Route path="/projects/:projectId" element={<ProjectPage />} />
                <Route path="/projects/:projectId/graph" element={<GraphEditorPage />} />
                <Route path="/projects/:projectId/kanban" element={<KanbanPage />} />
                <Route path="/projects/:projectId/tickets/:ticketId/attempts/:attemptId" element={<AttemptDetailPage />} />
                <Route path="/projects/:projectId/ship" element={<ShipRoomPage />} />
                <Route path="/projects/:projectId/workspace" element={<WorkspacePage />} />
                <Route path="/projects/:projectId/intents" element={<IntentInboxPage />} />
                <Route path="/agenthub" element={<AgenthubPage />} />
              </Routes>
            </Box>
          </Box>
        </Box>
      </Router>
    </ThemeProvider>
  );
}

function App() {
  return (
    <ThemeContextProvider>
      <AppContent />
    </ThemeContextProvider>
  );
}

export default App;
