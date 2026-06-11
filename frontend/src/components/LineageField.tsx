import React from 'react';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { Box, IconButton, Stack, Tooltip, Typography } from '@mui/material';

export function formatLineageId(value: string | null | undefined, width = 12) {
  return value ? value.slice(0, width) : '(not set)';
}

export function LineageField({
  label,
  value,
  width = 12,
  stopPropagation = false,
}: {
  label: string;
  value: string | null | undefined;
  width?: number;
  stopPropagation?: boolean;
}) {
  const handleCopy = async (event: React.MouseEvent) => {
    if (stopPropagation) {
      event.stopPropagation();
    }
    if (!value) {
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Best-effort only; compact lineage rendering should not fail on copy errors.
    }
  };

  return (
    <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap">
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Tooltip title={value || '(not set)'}>
        <Box
          component="code"
          sx={{
            fontFamily: 'monospace',
            fontSize: '0.72rem',
            color: value ? 'text.primary' : 'text.secondary',
          }}
        >
          {formatLineageId(value, width)}
        </Box>
      </Tooltip>
      {value && (
        <Tooltip title="Copy full id">
          <IconButton size="small" onClick={handleCopy} sx={{ p: 0.25 }}>
            <ContentCopyIcon sx={{ fontSize: '0.85rem' }} />
          </IconButton>
        </Tooltip>
      )}
    </Stack>
  );
}
