import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  IconButton,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ScienceIcon from '@mui/icons-material/Science';
import {
  addEvidenceApproval,
  addEvidenceWaiver,
  compareEvidence,
  collectEvidence,
  createEvidenceRepairTicket,
  getEvidenceRuns,
  getEvidence,
  getEvidencePolicy,
  queueEvidenceRun,
  rerunEvidenceChecks,
  runCommandEvidence,
  runEvidenceSuite,
  type EvidenceArtifact,
  type EvidenceBundle,
  type EvidenceCheckStatus,
  type EvidenceLlmFinding,
  type EvidencePolicyEvaluation,
  type EvidenceRun,
  type EvidenceStatus,
  type EvidenceTargetType,
} from '../utils/api';

const STATUS_COLOR: Record<EvidenceStatus | EvidenceCheckStatus | 'missing' | 'waived', 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  collecting: 'info',
  incomplete: 'warning',
  passed: 'success',
  failed: 'error',
  warning: 'warning',
  skipped: 'default',
  missing: 'warning',
  waived: 'warning',
};

function shortHash(value?: string | null): string {
  return value ? value.slice(0, 12) : '';
}

function latestBundle(policy: EvidencePolicyEvaluation | null, bundles: EvidenceBundle[]): EvidenceBundle | null {
  return policy?.bundle || bundles[0] || null;
}

function isExternalArtifact(ref?: string): boolean {
  return !!ref && /^https?:\/\//i.test(ref);
}

function checkArtifacts(check: { artifact_url: string | null; metadata: Record<string, unknown> }): EvidenceArtifact[] {
  const artifacts = check.metadata.artifacts;
  if (Array.isArray(artifacts)) {
    return artifacts.filter((artifact): artifact is EvidenceArtifact => (
      !!artifact
      && typeof artifact === 'object'
      && ('url' in artifact || 'path' in artifact)
    ));
  }
  if (check.artifact_url) {
    return [{ kind: 'other', label: 'artifact', url: check.artifact_url }];
  }
  return [];
}

function llmFindings(metadata: Record<string, unknown>): EvidenceLlmFinding[] {
  const findings = metadata.findings;
  if (!Array.isArray(findings)) return [];
  return findings.filter((finding): finding is EvidenceLlmFinding => (
    !!finding
    && typeof finding === 'object'
    && typeof (finding as EvidenceLlmFinding).claim === 'string'
  ));
}

export default function EvidencePanel({
  projectId,
  targetType,
  targetId,
  defaultCheckType = 'unit',
}: {
  projectId: string;
  targetType: EvidenceTargetType;
  targetId: string;
  defaultCheckType?: string;
}) {
  const [policy, setPolicy] = useState<EvidencePolicyEvaluation | null>(null);
  const [bundles, setBundles] = useState<EvidenceBundle[]>([]);
  const [runs, setRuns] = useState<EvidenceRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [collecting, setCollecting] = useState(false);
  const [runningCommand, setRunningCommand] = useState(false);
  const [queueingCommand, setQueueingCommand] = useState(false);
  const [runningSuite, setRunningSuite] = useState(false);
  const [queueingSuite, setQueueingSuite] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [command, setCommand] = useState('');
  const [checkType, setCheckType] = useState(defaultCheckType);
  const [waiverCheckType, setWaiverCheckType] = useState(defaultCheckType);
  const [waiverReason, setWaiverReason] = useState('');
  const [waiverActor, setWaiverActor] = useState('');
  const [waiving, setWaiving] = useState(false);
  const [approvalActor, setApprovalActor] = useState('');
  const [approvalReason, setApprovalReason] = useState('');
  const [approving, setApproving] = useState(false);
  const [creatingRepair, setCreatingRepair] = useState(false);
  const [repairCreated, setRepairCreated] = useState(false);
  const [rerunning, setRerunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [policyResult, bundleResult, runResult] = await Promise.all([
        getEvidencePolicy(projectId, targetType, targetId),
        getEvidence(projectId, { target_type: targetType, target_id: targetId }),
        getEvidenceRuns(projectId, { target_type: targetType, target_id: targetId }),
      ]);
      setPolicy(policyResult);
      setBundles(bundleResult);
      setRuns(runResult);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId, targetType, targetId]);

  useEffect(() => { load(); }, [load]);

  const handleCollect = async () => {
    setCollecting(true);
    setError(null);
    try {
      await collectEvidence(projectId, {
        target_type: targetType,
        target_id: targetId,
        check_type: defaultCheckType,
      });
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCollecting(false);
    }
  };

  const handleRunCommand = async () => {
    if (!command.trim() || !checkType.trim()) return;
    setRunningCommand(true);
    setError(null);
    try {
      await runCommandEvidence(projectId, {
        target_type: targetType,
        target_id: targetId,
        check_type: checkType.trim(),
        command: command.trim(),
      });
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunningCommand(false);
    }
  };

  const handleQueueCommand = async () => {
    if (!command.trim() || !checkType.trim()) return;
    setQueueingCommand(true);
    setError(null);
    try {
      await queueEvidenceRun(projectId, {
        run_type: 'command',
        target_type: targetType,
        target_id: targetId,
        check_type: checkType.trim(),
        command: command.trim(),
      });
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setQueueingCommand(false);
    }
  };

  const handleCompare = async () => {
    setComparing(true);
    setError(null);
    try {
      await compareEvidence(projectId, {
        target_type: targetType,
        target_id: targetId,
      });
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setComparing(false);
    }
  };

  const handleRunSuite = async () => {
    setRunningSuite(true);
    setError(null);
    try {
      await runEvidenceSuite(projectId, {
        target_type: targetType,
        target_id: targetId,
      });
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunningSuite(false);
    }
  };

  const handleQueueSuite = async () => {
    setQueueingSuite(true);
    setError(null);
    try {
      await queueEvidenceRun(projectId, {
        run_type: 'suite',
        target_type: targetType,
        target_id: targetId,
      });
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setQueueingSuite(false);
    }
  };

  const handleWaive = async () => {
    if (!bundle || !waiverCheckType.trim() || !waiverReason.trim()) return;
    setWaiving(true);
    setError(null);
    try {
      await addEvidenceWaiver(projectId, bundle.id, {
        check_type: waiverCheckType.trim(),
        reason: waiverReason.trim(),
        actor: waiverActor.trim() || undefined,
      });
      setWaiverReason('');
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setWaiving(false);
    }
  };

  const handleApprove = async () => {
    if (!bundle || !approvalActor.trim() || !approvalReason.trim()) return;
    setApproving(true);
    setError(null);
    try {
      await addEvidenceApproval(projectId, bundle.id, {
        actor: approvalActor.trim(),
        reason: approvalReason.trim(),
      });
      setApprovalReason('');
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setApproving(false);
    }
  };

  const handleCreateRepair = async () => {
    if (!bundle) return;
    setCreatingRepair(true);
    setError(null);
    try {
      await createEvidenceRepairTicket(projectId, bundle.id);
      setRepairCreated(true);
      await load();
      setExpanded(true);
      setTimeout(() => setRepairCreated(false), 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreatingRepair(false);
    }
  };

  const handleRerunFailed = async () => {
    if (!bundle) return;
    setRerunning(true);
    setError(null);
    try {
      await rerunEvidenceChecks(projectId, bundle.id);
      await load();
      setExpanded(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRerunning(false);
    }
  };

  const bundle = latestBundle(policy, bundles);
  const requiredEntries = policy ? Object.entries(policy.required_checks) : [];
  const requiredLlmEntries = policy ? Object.entries(policy.required_llm_reviewers || {}) : [];
  const requiredCount = (policy?.policy.required_checks.length ?? 0) + (policy?.policy.required_llm_reviewers.length ?? 0);
  const approvalRequired = policy?.policy.block_on.includes('missing_human_approval') ?? false;
  const suiteCount = policy?.policy.check_suites?.length ?? 0;
  const activeRuns = runs.filter(run => run.status === 'queued' || run.status === 'running');
  const hasFailure = !!bundle && (
    bundle.status === 'failed'
    || bundle.status === 'incomplete'
    || !!bundle.checks?.some(check => check.status === 'failed')
  );
  const hasRerunnableFailure = !!bundle?.checks?.some(check => check.status === 'failed' && !!check.command);

  return (
    <Paper sx={{ p: 1.5, mt: 2, bgcolor: 'background.default', border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography variant="subtitle2">Evidence</Typography>
          {loading ? (
            <CircularProgress size={14} />
          ) : policy ? (
            <Chip
              label={policy.allowed ? 'Policy pass' : 'Policy blocked'}
              color={policy.allowed ? 'success' : 'error'}
              size="small"
              variant="outlined"
            />
          ) : null}
          {bundle && (
            <Chip
              label={bundle.status}
              color={STATUS_COLOR[bundle.status] ?? 'default'}
              size="small"
            />
          )}
          {requiredCount > 0 && (
            <Typography variant="caption" color="text.secondary">
              {requiredCount} required
            </Typography>
          )}
        </Stack>
        <Stack direction="row" spacing={0.5}>
          <Tooltip title="Collect from current target results">
            <span>
              <IconButton size="small" onClick={handleCollect} disabled={loading || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || comparing || waiving || approving || creatingRepair || rerunning}>
                {collecting ? <CircularProgress size={16} /> : <ScienceIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Refresh evidence">
            <span>
              <IconButton size="small" onClick={load} disabled={loading || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || comparing || waiving || approving || creatingRepair || rerunning}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <IconButton size="small" onClick={() => setExpanded(v => !v)} disabled={!bundle && !policy}>
            {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
          </IconButton>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}

      {policy?.reasons.length ? (
        <Alert severity="warning" sx={{ mt: 1 }}>
          {policy.reasons.join(' ')}
        </Alert>
      ) : null}

      <Collapse in={expanded}>
        <Divider sx={{ my: 1 }} />
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 1 }}>
          <TextField
            size="small"
            label="Check"
            value={checkType}
            onChange={e => setCheckType(e.target.value)}
            sx={{ width: { xs: '100%', sm: 130 } }}
          />
          <TextField
            size="small"
            label="Command"
            value={command}
            onChange={e => setCommand(e.target.value)}
            fullWidth
          />
          <Button
            size="small"
            variant="outlined"
            onClick={handleRunCommand}
            disabled={runningCommand || queueingCommand || runningSuite || queueingSuite || collecting || comparing || waiving || approving || creatingRepair || rerunning || !command.trim() || !checkType.trim()}
            sx={{ minWidth: 92 }}
          >
            {runningCommand ? <CircularProgress size={14} /> : 'Run'}
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={handleQueueCommand}
            disabled={runningCommand || queueingCommand || runningSuite || queueingSuite || collecting || comparing || waiving || approving || creatingRepair || rerunning || !command.trim() || !checkType.trim()}
            sx={{ minWidth: 92 }}
          >
            {queueingCommand ? <CircularProgress size={14} /> : 'Queue'}
          </Button>
        </Stack>

        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          {suiteCount > 0 && (
            <Button
              size="small"
              variant="outlined"
              onClick={handleRunSuite}
              disabled={loading || runningSuite || queueingSuite || comparing || collecting || runningCommand || queueingCommand || waiving || approving || creatingRepair || rerunning}
              sx={{ minWidth: 128 }}
            >
              {runningSuite ? <CircularProgress size={14} /> : 'Run Suite'}
            </Button>
          )}
          {suiteCount > 0 && (
            <Button
              size="small"
              variant="outlined"
              onClick={handleQueueSuite}
              disabled={loading || runningSuite || queueingSuite || comparing || collecting || runningCommand || queueingCommand || waiving || approving || creatingRepair || rerunning}
              sx={{ minWidth: 128 }}
            >
              {queueingSuite ? <CircularProgress size={14} /> : 'Queue Suite'}
            </Button>
          )}
          <Button
            size="small"
            variant="outlined"
            onClick={handleCompare}
            disabled={loading || comparing || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || waiving || approving || creatingRepair || rerunning}
            sx={{ minWidth: 128 }}
          >
            {comparing ? <CircularProgress size={14} /> : 'Compare'}
          </Button>
        </Stack>

        {hasFailure && (
          <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
            {hasRerunnableFailure && (
              <Button
                size="small"
                variant="outlined"
                onClick={handleRerunFailed}
                disabled={!bundle || rerunning || creatingRepair || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || comparing || waiving || approving}
                sx={{ minWidth: 128 }}
              >
                {rerunning ? <CircularProgress size={14} /> : 'Rerun Failed'}
              </Button>
            )}
            <Button
              size="small"
              variant="outlined"
              color="error"
              onClick={handleCreateRepair}
              disabled={!bundle || creatingRepair || rerunning || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || comparing || waiving || approving}
              sx={{ minWidth: 128 }}
            >
              {creatingRepair ? <CircularProgress size={14} /> : repairCreated ? 'Repair Created' : 'Create Repair'}
            </Button>
          </Stack>
        )}

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 1 }}>
          <TextField
            size="small"
            label="Waive"
            value={waiverCheckType}
            onChange={e => setWaiverCheckType(e.target.value)}
            sx={{ width: { xs: '100%', sm: 130 } }}
          />
          <TextField
            size="small"
            label="Reason"
            value={waiverReason}
            onChange={e => setWaiverReason(e.target.value)}
            fullWidth
          />
          <TextField
            size="small"
            label="Actor"
            value={waiverActor}
            onChange={e => setWaiverActor(e.target.value)}
            sx={{ width: { xs: '100%', sm: 140 } }}
          />
          <Button
            size="small"
            variant="outlined"
            color="warning"
            onClick={handleWaive}
            disabled={!bundle || waiving || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || comparing || approving || creatingRepair || rerunning || !waiverCheckType.trim() || !waiverReason.trim()}
            sx={{ minWidth: 92 }}
          >
            {waiving ? <CircularProgress size={14} /> : 'Waive'}
          </Button>
        </Stack>

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 1 }}>
          <TextField
            size="small"
            label="Approver"
            value={approvalActor}
            onChange={e => setApprovalActor(e.target.value)}
            sx={{ width: { xs: '100%', sm: 140 } }}
          />
          <TextField
            size="small"
            label="Approval"
            value={approvalReason}
            onChange={e => setApprovalReason(e.target.value)}
            fullWidth
          />
          <Button
            size="small"
            variant={approvalRequired && !policy?.human_approval ? 'contained' : 'outlined'}
            color="success"
            onClick={handleApprove}
            disabled={!bundle || approving || collecting || runningCommand || queueingCommand || runningSuite || queueingSuite || comparing || waiving || creatingRepair || rerunning || !approvalActor.trim() || !approvalReason.trim()}
            sx={{ minWidth: 92 }}
          >
            {approving ? <CircularProgress size={14} /> : 'Approve'}
          </Button>
        </Stack>

        {policy?.human_approval && (
          <Alert severity="success" sx={{ mb: 1 }}>
            Approved by {String(policy.human_approval.metadata.actor || 'unknown')}: {policy.human_approval.output}
          </Alert>
        )}

        {requiredEntries.length > 0 && (
          <Stack direction="row" spacing={0.75} flexWrap="wrap" sx={{ mb: 1 }}>
            {requiredEntries.map(([checkType, check]) => (
              <Chip
                key={checkType}
                label={`${checkType}: ${check.status}`}
                color={STATUS_COLOR[check.status] ?? 'default'}
                size="small"
                variant="outlined"
              />
            ))}
          </Stack>
        )}

        {requiredLlmEntries.length > 0 && (
          <Stack direction="row" spacing={0.75} flexWrap="wrap" sx={{ mb: 1 }}>
            {requiredLlmEntries.map(([reviewer, review]) => (
              <Chip
                key={reviewer}
                label={`${reviewer}: ${review.status}`}
                color={STATUS_COLOR[review.status] ?? 'default'}
                size="small"
                variant="outlined"
              />
            ))}
          </Stack>
        )}

        {activeRuns.length > 0 && (
          <Stack direction="row" spacing={0.75} flexWrap="wrap" sx={{ mb: 1 }}>
            {activeRuns.map(run => (
              <Chip
                key={run.id}
                label={`${run.run_type}: ${run.status}`}
                color={run.status === 'running' ? 'info' : 'default'}
                size="small"
                variant="outlined"
              />
            ))}
          </Stack>
        )}

        {bundle ? (
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              {bundle.summary || 'Evidence bundle'}{bundle.candidate_hash ? ` - ${shortHash(bundle.candidate_hash)}` : ''}
            </Typography>
            {bundle.checks?.length ? (
              <Stack spacing={0.75} sx={{ mt: 1 }}>
                {bundle.checks.map(check => (
                  <Box key={check.id} sx={{ borderLeft: 3, borderLeftColor: `${STATUS_COLOR[check.status]}.main`, pl: 1 }}>
                    <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap">
                      <Chip
                        label={check.check_type}
                        size="small"
                        color={STATUS_COLOR[check.status] ?? 'default'}
                        sx={{ height: 18, fontSize: '0.65rem' }}
                      />
                      <Typography variant="caption" color="text.secondary">
                        {check.tool_name || 'manual'} - {check.status}
                      </Typography>
                    </Stack>
                    {check.output && (
                      <Box component="pre" sx={{ mt: 0.5, mb: 0, fontSize: '0.65rem', whiteSpace: 'pre-wrap', maxHeight: 140, overflow: 'auto' }}>
                        {check.output}
                      </Box>
                    )}
                    {llmFindings(check.metadata).length > 0 && (
                      <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                        {llmFindings(check.metadata).map((finding, index) => (
                          <Box key={`${finding.claim}-${index}`}>
                            <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
                              <Chip
                                label={finding.severity}
                                size="small"
                                color={finding.blocking ? 'error' : 'warning'}
                                variant="outlined"
                                sx={{ height: 18, fontSize: '0.62rem' }}
                              />
                              {finding.path && (
                                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                                  {finding.path}{finding.line ? `:${finding.line}` : ''}
                                </Typography>
                              )}
                            </Stack>
                            <Typography variant="caption" sx={{ display: 'block', fontSize: '0.68rem' }}>
                              {finding.claim}
                            </Typography>
                            {finding.suggested_fix && (
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem' }}>
                                {finding.suggested_fix}
                              </Typography>
                            )}
                          </Box>
                        ))}
                      </Stack>
                    )}
                    {checkArtifacts(check).length > 0 && (
                      <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
                        {checkArtifacts(check).map((artifact, index) => {
                          const ref = artifact.url || artifact.path || '';
                          const label = artifact.label || artifact.kind || 'artifact';
                          return isExternalArtifact(ref) ? (
                            <Typography
                              key={`${ref}-${index}`}
                              component="a"
                              variant="caption"
                              href={ref}
                              target="_blank"
                              rel="noreferrer"
                              sx={{ fontSize: '0.65rem' }}
                            >
                              {label}
                            </Typography>
                          ) : (
                            <Chip
                              key={`${ref}-${index}`}
                              label={`${label}: ${ref}`}
                              size="small"
                              variant="outlined"
                              sx={{ height: 18, fontSize: '0.62rem' }}
                            />
                          );
                        })}
                      </Stack>
                    )}
                  </Box>
                ))}
              </Stack>
            ) : (
              <Typography variant="caption" color="text.secondary">No checks recorded.</Typography>
            )}
          </Box>
        ) : (
          <Typography variant="caption" color="text.secondary">
            No evidence bundle has been collected for this target.
          </Typography>
        )}
      </Collapse>
    </Paper>
  );
}
