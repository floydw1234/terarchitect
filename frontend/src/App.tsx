import React, { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { Box, CssBaseline, ThemeProvider, createTheme, Alert, AlertTitle, Collapse, IconButton, Stack, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import Navbar from './components/Navbar';
import ProjectsPage from './pages/ProjectsPage';
import ProjectPage from './pages/ProjectPage';
import GraphEditorPage from './pages/GraphEditorPage';
import KanbanPage from './pages/KanbanPage';
import ReviewPage from './pages/ReviewPage';
import ReviewDetailPage from './pages/ReviewDetailPage';
import SettingsPage from './pages/SettingsPage';
import { getSettingsCheck, SettingIssue } from './utils/api';

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#22d3ee',
      light: '#67e8f9',
      dark: '#06b6d4',
    },
    background: {
      default: '#0f172a',
      paper: '#1e293b',
    },
    text: {
      primary: '#f8fafc',
      secondary: '#94a3b8',
    },
  },
  typography: {
    fontFamily: '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  shape: {
    borderRadius: 12,
  },
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundColor: '#1e293b',
          borderRadius: 12,
          border: '1px solid rgba(148, 163, 184, 0.35)',
          boxShadow: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundColor: '#1e293b',
          borderRadius: 12,
          border: '1px solid rgba(148, 163, 184, 0.35)',
          boxShadow: 'none',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#0f172a',
          borderBottom: '1px solid rgba(148, 163, 184, 0.12)',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          backgroundColor: '#1e293b',
          border: '1px solid rgba(148, 163, 184, 0.12)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            '& fieldset': { borderColor: 'rgba(148, 163, 184, 0.2)' },
            '&:hover fieldset': { borderColor: 'rgba(148, 163, 184, 0.4)' },
          },
        },
      },
    },
  },
});

function SetupBanner() {
  const [missingRequired, setMissingRequired] = useState<SettingIssue[]>([]);
  const [warningItems, setWarningItems] = useState<SettingIssue[]>([]);
  const [dismissedError, setDismissedError] = useState(false);
  const [dismissedWarn, setDismissedWarn] = useState(false);

  useEffect(() => {
    getSettingsCheck()
      .then(res => {
        setMissingRequired(res.missing_required ?? []);
        setWarningItems(res.warnings ?? []);
      })
      .catch(() => {});
  }, []);

  const errorVisible = !dismissedError && missingRequired.length > 0;
  const warnVisible = !dismissedWarn && warningItems.length > 0;

  if (!errorVisible && !warnVisible) return null;

  return (
    <Box sx={{ px: 3, pt: 1.5 }}>
      <Collapse in={errorVisible}>
        <Alert
          severity="error"
          icon={<ErrorOutlineIcon />}
          action={
            <IconButton size="small" color="inherit" onClick={() => setDismissedError(true)}>
              <CloseIcon fontSize="inherit" />
            </IconButton>
          }
          sx={{ mb: warnVisible ? 1 : 0 }}
        >
          <AlertTitle>Required settings missing — execution is blocked</AlertTitle>
          <Stack spacing={0.5}>
            {missingRequired.map(item => (
              <Typography key={item.key} variant="body2">
                <strong>{item.label}:</strong> {item.reason}
              </Typography>
            ))}
          </Stack>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Go to{' '}
            <Link to="/settings" style={{ color: 'inherit', fontWeight: 600 }}>
              Settings
            </Link>{' '}
            to configure these before moving tickets to In Progress.
          </Typography>
        </Alert>
      </Collapse>
      <Collapse in={warnVisible}>
        <Alert
          severity="warning"
          icon={<WarningAmberIcon />}
          action={
            <IconButton size="small" color="inherit" onClick={() => setDismissedWarn(true)}>
              <CloseIcon fontSize="inherit" />
            </IconButton>
          }
        >
          <AlertTitle>Recommended settings not configured</AlertTitle>
          <Stack spacing={0.5}>
            {warningItems.map(item => (
              <Typography key={item.key} variant="body2">
                <strong>{item.label}:</strong> {item.reason}
              </Typography>
            ))}
          </Stack>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Configure these in{' '}
            <Link to="/settings" style={{ color: 'inherit', fontWeight: 600 }}>
              Settings
            </Link>.
          </Typography>
        </Alert>
      </Collapse>
    </Box>
  );
}

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Router>
        <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
          <Navbar />
          <SetupBanner />
          <Box sx={{ flex: 1, p: 3 }}>
            <Routes>
              <Route path="/" element={<ProjectsPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:projectId" element={<ProjectPage />} />
              <Route path="/projects/:projectId/graph" element={<GraphEditorPage />} />
              <Route path="/projects/:projectId/kanban" element={<KanbanPage />} />
              <Route path="/projects/:projectId/review" element={<ReviewPage />} />
              <Route path="/projects/:projectId/review/:ticketId" element={<ReviewDetailPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </Box>
        </Box>
      </Router>
    </ThemeProvider>
  );
}

export default App;
