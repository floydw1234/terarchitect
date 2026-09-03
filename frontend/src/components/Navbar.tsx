import React from 'react';
import { AppBar, Toolbar, Typography, Button, Box, Stack } from '@mui/material';
import { Link, useLocation } from 'react-router-dom';
import { useThemeMode } from '../contexts/ThemeContext';
import ThemeToggle from './ThemeToggle';

interface NavbarProps {}

const Navbar: React.FC<NavbarProps> = () => {
  const location = useLocation();
  const { mode } = useThemeMode();
  const isDark = mode === 'dark';

  const items = [
    { label: 'Projects', to: '/projects', active: location.pathname === '/projects' || location.pathname === '/' || location.pathname.startsWith('/projects/') },
    { label: 'AgentHub', to: '/agenthub', active: location.pathname === '/agenthub' },
  ];

  return (
    <AppBar
      position="sticky"
      elevation={0}
      sx={{
        backgroundColor: isDark ? 'background.paper' : '#FFFFFF',
        borderBottom: '2px solid',
        borderColor: isDark ? 'primary.main' : '#0085FA',
      }}
    >
      <Toolbar
        sx={{
          minHeight: 74,
          maxWidth: 1440,
          width: '100%',
          mx: 'auto',
          px: { xs: 0.5, md: 1 },
          gap: 2,
        }}
      >
        <Box
          component={Link}
          to="/"
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 0.15,
            color: 'text.primary',
            textDecoration: 'none',
          }}
        >
          <Typography variant="overline" color="secondary.main">
            Operational Architecture
          </Typography>
          <Typography variant="h6" sx={{ color: 'primary.main' }}>
            Terarchitect
          </Typography>
        </Box>
        <Box sx={{ flex: 1 }} />
        <Box
          sx={{
            p: 0.5,
            borderRadius: 1,
            border: '1px solid',
            borderColor: isDark ? 'divider' : '#45C3F8',
            bgcolor: isDark ? 'background.default' : '#ECF4FF',
          }}
        >
          <Stack direction="row" spacing={0.5}>
            {items.map((item) => (
              <Button
                key={item.to}
                component={Link}
                to={item.to}
                variant={item.active ? 'contained' : 'text'}
                color={item.active ? 'primary' : 'inherit'}
                sx={{
                  color: item.active
                    ? (isDark ? 'background.default' : 'common.white')
                    : 'text.primary',
                  px: 2,
                  fontWeight: 600,
                  bgcolor: item.active
                    ? (isDark ? 'primary.main' : '#0085FA')
                    : 'transparent',
                  '&:hover': {
                    bgcolor: item.active
                      ? (isDark ? 'primary.light' : '#0066cc')
                      : (isDark ? 'rgba(69, 195, 248, 0.12)' : 'rgba(0, 133, 250, 0.12)'),
                  },
                }}
              >
                {item.label}
              </Button>
            ))}
          </Stack>
        </Box>
        <ThemeToggle />
      </Toolbar>
    </AppBar>
  );
};

export default Navbar;
