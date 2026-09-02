import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Box, CssBaseline, ThemeProvider, createTheme } from '@mui/material';
import { alpha } from '@mui/material/styles';
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

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#0085fa',
      light: '#4da6ff',
      dark: '#0066cc',
    },
    secondary: {
      main: '#4169e1',
      light: '#6b8de8',
      dark: '#2d4db3',
    },
    info: {
      main: '#0085fa',
    },
    success: {
      main: '#10b981',
    },
    warning: {
      main: '#f59e0b',
    },
    error: {
      main: '#ef4444',
    },
    background: {
      default: '#f5f9ff',
      paper: '#ffffff',
    },
    text: {
      primary: '#1a1a1a',
      secondary: '#6b7280',
    },
    divider: '#e0e0e0',
  },
  typography: {
    fontFamily: '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: {
      fontSize: '2rem',
      fontWeight: 700,
      letterSpacing: '-0.04em',
    },
    h5: {
      fontSize: '1.5rem',
      fontWeight: 700,
      letterSpacing: '-0.035em',
    },
    h6: {
      fontWeight: 700,
      letterSpacing: '-0.025em',
    },
    subtitle1: {
      fontWeight: 600,
      letterSpacing: '-0.02em',
    },
    subtitle2: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    button: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    overline: {
      letterSpacing: '0.16em',
      fontWeight: 700,
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#f5f9ff',
          color: '#1a1a1a',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          background: '#ffffff',
          borderRadius: 12,
          border: '1px solid #e0e0e0',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.08)',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          background: '#ffffff',
          borderRadius: 12,
          border: '1px solid #e0e0e0',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.08)',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 8,
          paddingInline: 16,
          minHeight: 40,
        },
        contained: {
          boxShadow: '0 2px 4px rgba(0, 133, 250, 0.16)',
          '&:hover': {
            boxShadow: '0 4px 8px rgba(0, 133, 250, 0.24)',
          },
        },
        outlined: {
          borderColor: '#d4d4d4',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#ffffff',
          borderBottom: '1px solid #e0e0e0',
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.08)',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 6,
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          background: '#ffffff',
          border: '1px solid #e0e0e0',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.16)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            backgroundColor: '#ffffff',
            '& fieldset': { borderColor: '#d4d4d4' },
            '&:hover fieldset': { borderColor: '#0085fa' },
            '&.Mui-focused fieldset': { borderColor: '#0085fa' },
          },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 8,
        },
      },
    },
  },
});

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Router>
        <Box
          sx={{
            position: 'relative',
            minHeight: '100vh',
            overflow: 'hidden',
            backgroundColor: '#f5f9ff',
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

export default App;
