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
      main: '#0085FA',
      light: '#45C3F8',
      dark: '#0066cc',
    },
    secondary: {
      main: '#45C3F8',
      light: '#7dd4fa',
      dark: '#0085FA',
    },
    info: {
      main: '#0085FA',
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
      default: '#FFFFFF',
      paper: '#FFFFFF',
    },
    text: {
      primary: 'rgba(0, 0, 0, 0.87)',
      secondary: 'rgba(0, 0, 0, 0.6)',
    },
    divider: '#D4D4D4',
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
    borderRadius: 4,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#FFFFFF',
          color: 'rgba(0, 0, 0, 0.87)',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          background: '#FFFFFF',
          borderRadius: 6,
          border: '1px solid #D4D4D4',
          boxShadow: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          background: '#FFFFFF',
          borderRadius: 6,
          border: '1px solid #D4D4D4',
          boxShadow: 'none',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 4,
          paddingInline: 20,
          minHeight: 40,
          fontWeight: 600,
        },
        contained: {
          boxShadow: 'none',
          '&:hover': {
            boxShadow: 'none',
          },
        },
        outlined: {
          borderColor: '#D4D4D4',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#FFFFFF',
          borderBottom: '1px solid #D4D4D4',
          boxShadow: 'none',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 4,
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          background: '#FFFFFF',
          border: '1px solid #D4D4D4',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.15)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            backgroundColor: '#FFFFFF',
            '& fieldset': { borderColor: '#D4D4D4' },
            '&:hover fieldset': { borderColor: '#0085FA' },
            '&.Mui-focused fieldset': { borderColor: '#0085FA' },
          },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 4,
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
            backgroundColor: '#FFFFFF',
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
