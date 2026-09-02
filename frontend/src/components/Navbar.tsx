import React from 'react';
import { AppBar, Toolbar, Typography, Button, Box, Stack } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { Link, useLocation } from 'react-router-dom';

interface NavbarProps {}

const Navbar: React.FC<NavbarProps> = () => {
  const location = useLocation();
  const items = [
    { label: 'Projects', to: '/projects', active: location.pathname === '/projects' || location.pathname === '/' || location.pathname.startsWith('/projects/') },
    { label: 'AgentHub', to: '/agenthub', active: location.pathname === '/agenthub' },
  ];

  return (
    <AppBar
      position="sticky"
      elevation={0}
      sx={{
        backgroundColor: '#FFFFFF',
        borderBottom: '2px solid #0085FA',
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
            border: '1px solid #45C3F8',
            bgcolor: '#ECF4FF',
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
                  color: item.active ? 'common.white' : 'text.primary',
                  px: 2,
                  fontWeight: 600,
                  bgcolor: item.active ? '#0085FA' : 'transparent',
                  '&:hover': {
                    bgcolor: item.active ? '#0066cc' : 'rgba(0, 133, 250, 0.12)',
                  },
                }}
              >
                {item.label}
              </Button>
            ))}
          </Stack>
        </Box>
      </Toolbar>
    </AppBar>
  );
};

export default Navbar;
