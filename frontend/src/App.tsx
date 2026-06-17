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
    mode: 'dark',
    primary: {
      main: '#8b5cf6',
      light: '#c4b5fd',
      dark: '#6d28d9',
    },
    secondary: {
      main: '#22d3ee',
      light: '#9ae8ff',
      dark: '#0ea5b7',
    },
    info: {
      main: '#6366f1',
    },
    background: {
      default: '#08090a',
      paper: '#111318',
    },
    text: {
      primary: '#f4f7fb',
      secondary: '#98a2b3',
    },
    divider: 'rgba(255, 255, 255, 0.08)',
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
    borderRadius: 18,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#08090a',
          backgroundImage: `
            radial-gradient(circle at top left, rgba(99, 102, 241, 0.18), transparent 28%),
            radial-gradient(circle at 85% 12%, rgba(34, 211, 238, 0.12), transparent 24%),
            linear-gradient(180deg, #08090a 0%, #0b0d10 100%)
          `,
          color: '#f4f7fb',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          background: 'linear-gradient(180deg, rgba(17, 19, 24, 0.84), rgba(11, 13, 17, 0.78))',
          borderRadius: 20,
          border: '1px solid rgba(255, 255, 255, 0.08)',
          backdropFilter: 'blur(18px)',
          boxShadow: '0 24px 70px rgba(0, 0, 0, 0.34)',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          background: 'linear-gradient(180deg, rgba(17, 19, 24, 0.84), rgba(11, 13, 17, 0.78))',
          borderRadius: 20,
          border: '1px solid rgba(255, 255, 255, 0.08)',
          backdropFilter: 'blur(18px)',
          boxShadow: '0 24px 70px rgba(0, 0, 0, 0.34)',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 999,
          paddingInline: 16,
          minHeight: 40,
        },
        contained: {
          boxShadow: '0 12px 28px rgba(139, 92, 246, 0.28)',
        },
        outlined: {
          borderColor: 'rgba(255, 255, 255, 0.12)',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: alpha('#08090a', 0.72),
          borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
          backdropFilter: 'blur(20px)',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 999,
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          background: 'linear-gradient(180deg, rgba(18, 20, 27, 0.96), rgba(10, 12, 17, 0.94))',
          border: '1px solid rgba(255, 255, 255, 0.08)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            backgroundColor: 'rgba(255, 255, 255, 0.02)',
            '& fieldset': { borderColor: 'rgba(255, 255, 255, 0.1)' },
            '&:hover fieldset': { borderColor: 'rgba(139, 92, 246, 0.28)' },
            '&.Mui-focused fieldset': { borderColor: '#8b5cf6' },
          },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 14,
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
            '&::before': {
              content: '""',
              position: 'fixed',
              inset: 0,
              pointerEvents: 'none',
              background:
                'radial-gradient(circle at 15% 18%, rgba(139, 92, 246, 0.12), transparent 24%), radial-gradient(circle at 82% 14%, rgba(34, 211, 238, 0.08), transparent 22%)',
            },
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
