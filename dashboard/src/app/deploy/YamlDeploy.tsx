import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import yaml from 'js-yaml';
import { CheckCircle, RocketLaunch } from '@phosphor-icons/react';

import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { CodeEditor } from '@/components/ui/code-editor';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

/** js-yaml attaches a 1-based-friendly `mark` (0-based line) to parse errors. */
function yamlErrorLine(e: unknown): number | null {
  const line = (e as { mark?: { line?: number } } | null)?.mark?.line;
  return typeof line === 'number' ? line + 1 : null;
}

export const STARTER = `kind: Agent
metadata:
  name: my-agent
  department: engineering
spec:
  stack: forgeos
  execution_type: event_driven
  ownership: shared
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools: []
  goal: Describe what this agent should accomplish.
  system_prompt: |
    You are a helpful Helios OS agent.
`;

interface Preview {
  name?: string;
  stack?: string;
  execution_type?: string;
  ownership?: string;
  toolCount?: number;
  promptChars?: number;
}

function buildPreview(doc: unknown): Preview {
  const d = (doc ?? {}) as Record<string, any>;
  const spec = (d.spec ?? d) as Record<string, any>;
  const tools = spec.tools ?? spec.capabilities?.tools?.allowed;
  const prompt = spec.system_prompt?.content ?? spec.system_prompt;
  return {
    name: d.metadata?.name ?? d.name,
    stack: spec.stack ?? spec.runtime?.framework,
    execution_type: spec.execution_type ?? spec.lifecycle?.type,
    ownership: spec.ownership,
    toolCount: Array.isArray(tools) ? tools.length : undefined,
    promptChars: typeof prompt === 'string' ? prompt.length : undefined,
  };
}

/** Raw YAML manifest editor — paste, validate, deploy. `text`/`onTextChange`
 *  are lifted to the page so the wizard can hand off a generated manifest. */
export function YamlDeploy({ text, onTextChange }: { text: string; onTextChange: (v: string) => void }) {
  const router = useRouter();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [errorLine, setErrorLine] = useState<number | null>(null);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);

  // Validate as you type (debounced): surface the parse error + failing line
  // without waiting for the Validate button.
  useEffect(() => {
    const id = setTimeout(() => {
      if (!text.trim()) {
        setParseError(null);
        setErrorLine(null);
        return;
      }
      try {
        yaml.load(text);
        setParseError(null);
        setErrorLine(null);
      } catch (e) {
        setParseError(e instanceof Error ? e.message : 'Invalid YAML');
        setErrorLine(yamlErrorLine(e));
      }
    }, 300);
    return () => clearTimeout(id);
  }, [text]);

  const validate = () => {
    setDeployError(null);
    try {
      const doc = yaml.load(text);
      setPreview(buildPreview(doc));
      setParseError(null);
      setErrorLine(null);
    } catch (e) {
      setPreview(null);
      setParseError(e instanceof Error ? e.message : 'Invalid YAML');
      setErrorLine(yamlErrorLine(e));
    }
  };

  const deploy = async () => {
    setDeployError(null);
    try {
      yaml.load(text);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : 'Invalid YAML');
      return;
    }
    setDeploying(true);
    try {
      const res = await api.deployYaml(text);
      router.push(`/agents/${encodeURIComponent(res.agent_id)}`);
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : 'Deploy failed');
      setDeploying(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_20rem]">
      <Card>
        <CardHeader>
          <CardTitle>Manifest</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <CodeEditor
            value={text}
            onChange={(v) => {
              onTextChange(v);
              setPreview(null);
            }}
            errorLine={errorLine}
            ariaLabel="Agent manifest YAML"
          />
          {parseError ? (
            <div role="alert" className="rounded-md border border-danger/20 bg-danger-wash px-4 py-3 text-[13px] text-danger">
              {parseError}
            </div>
          ) : null}
          {deployError ? (
            <div role="alert" className="rounded-md border border-danger/20 bg-danger-wash px-4 py-3 text-[13px] text-danger">
              {deployError}
            </div>
          ) : null}
          <div className="flex items-center gap-3">
            <Button variant="secondary" onClick={validate}>
              <CheckCircle className="h-4 w-4" aria-hidden />
              Validate
            </Button>
            <Button onClick={deploy} disabled={deploying}>
              <RocketLaunch className="h-4 w-4" aria-hidden />
              {deploying ? 'Deploying…' : 'Deploy'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="h-fit">
        <CardHeader>
          <CardTitle>Preview</CardTitle>
        </CardHeader>
        <CardContent>
          {preview ? (
            <dl className="space-y-2 text-[13px]">
              {[
                ['Name', preview.name],
                ['Stack', preview.stack],
                ['Type', preview.execution_type],
                ['Ownership', preview.ownership],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between gap-3 border-b border-edge-subtle pb-2">
                  <dt className="text-tertiary">{label}</dt>
                  <dd className="font-mono text-xs text-primary">{value || '—'}</dd>
                </div>
              ))}
              <div className="flex items-center gap-2 pt-1">
                <Badge variant="outline">{preview.toolCount ?? 0} tools</Badge>
                <Badge variant="outline">{preview.promptChars ?? 0} prompt chars</Badge>
              </div>
            </dl>
          ) : (
            <p className="text-[13px] text-tertiary">
              Validate to preview the parsed manifest before deploying.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
