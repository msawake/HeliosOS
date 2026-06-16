import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowRight, Code, RocketLaunch } from '@phosphor-icons/react';

import { api, type Env, type ToolDef } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { initialWizardState, type WizardState } from '@/lib/wizard/types';
import { manifestToYaml } from '@/lib/wizard/buildManifest';
import { STEP_KEYS, validateStep } from '@/lib/wizard/validate';
import { Stepper } from './Stepper';
import {
  StepBasics, StepLlm, StepTools, StepSecrets, StepEnvironment, StepGovernance, StepReview,
} from './steps';

const TITLES = ['Basics', 'LLM', 'Tools', 'Secrets', 'Environment', 'Governance', 'Review'];

/** Guided multi-step agent creation. Builds an agentos/v1 manifest and deploys
 *  it via the same path the YAML editor uses. `onOpenYaml` hands the generated
 *  manifest to the sibling YAML tab. */
export function CreateAgentWizard({ onOpenYaml }: { onOpenYaml: (yaml: string) => void }) {
  const router = useRouter();
  const [s, setS] = useState<WizardState>(initialWizardState);
  const [step, setStep] = useState(0);
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [toolsLoading, setToolsLoading] = useState(true);
  const [envs, setEnvs] = useState<Env[]>([]);
  const [envsLoading, setEnvsLoading] = useState(true);
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  const patch = useCallback((p: Partial<WizardState>) => setS((prev) => ({ ...prev, ...p })), []);

  useEffect(() => {
    api.listTools().then((t) => setTools(t ?? [])).catch(() => setTools([])).finally(() => setToolsLoading(false));
    api.listEnvs().then((e) => setEnvs(e ?? [])).catch(() => setEnvs([])).finally(() => setEnvsLoading(false));
  }, []);

  const result = useMemo(() => validateStep(STEP_KEYS[step], s), [step, s]);
  const isLast = step === TITLES.length - 1;

  const next = () => {
    if (result.ok && !isLast) setStep((i) => i + 1);
  };
  const back = () => setStep((i) => Math.max(0, i - 1));

  const deploy = async () => {
    setDeploying(true);
    setDeployError(null);
    try {
      const yaml = manifestToYaml(s);
      const res = await api.deployYaml(yaml);
      const agentId = res.agent_id;
      // Best-effort post-deploy environment attach (won't undo the deploy).
      if (s.envMode === 'attach' && s.envDefId) {
        try {
          await api.attachEnv(agentId, s.envDefId);
        } catch (e) {
          // Surface but still navigate — the agent exists.
          console.warn('attachEnv failed:', e);
        }
      }
      router.push(`/agents/${encodeURIComponent(agentId)}`);
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : 'Deploy failed');
      setDeploying(false);
    }
  };

  const props = { s, patch, errors: result.errors };

  return (
    <Card>
      <CardContent className="space-y-5 pt-5">
        <Stepper steps={TITLES} current={step} onJump={setStep} />

        <div className="min-h-[20rem]">
          {step === 0 && <StepBasics {...props} />}
          {step === 1 && <StepLlm {...props} />}
          {step === 2 && <StepTools {...props} tools={tools} loading={toolsLoading} />}
          {step === 3 && <StepSecrets {...props} />}
          {step === 4 && <StepEnvironment {...props} envs={envs} loading={envsLoading} />}
          {step === 5 && <StepGovernance {...props} />}
          {step === 6 && <StepReview {...props} />}
        </div>

        {deployError ? (
          <div role="alert" className="rounded-md border border-danger/20 bg-danger-wash px-4 py-3 text-[13px] text-danger">
            {deployError}
          </div>
        ) : null}

        <div className="flex items-center justify-between border-t border-edge-subtle pt-4">
          <Button variant="ghost" onClick={back} disabled={step === 0}>
            <ArrowLeft className="h-4 w-4" aria-hidden /> Back
          </Button>
          <div className="flex items-center gap-2">
            {isLast ? (
              <>
                <Button variant="secondary" onClick={() => onOpenYaml(manifestToYaml(s))}>
                  <Code className="h-4 w-4" aria-hidden /> Open in YAML editor
                </Button>
                <Button onClick={deploy} disabled={deploying}>
                  <RocketLaunch className="h-4 w-4" aria-hidden />
                  {deploying ? 'Deploying…' : 'Deploy'}
                </Button>
              </>
            ) : (
              <Button onClick={next} disabled={!result.ok}>
                Next <ArrowRight className="h-4 w-4" aria-hidden />
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
