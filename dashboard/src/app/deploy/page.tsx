'use client';

import { useState } from 'react';

import { PageHeader } from '@/components/layout/PageHeader';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { CreateAgentWizard } from '@/components/agent/wizard/CreateAgentWizard';
import { YamlDeploy, STARTER } from './YamlDeploy';

export default function DeployPage() {
  const [tab, setTab] = useState('wizard');
  const [yamlText, setYamlText] = useState(STARTER);

  return (
    <div>
      <PageHeader
        title="Deploy"
        description="Build an agent with the guided wizard, or paste a manifest. Mirrors forgeos deploy."
        back={{ href: '/', label: 'Agents' }}
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="wizard">Wizard</TabsTrigger>
          <TabsTrigger value="yaml">YAML</TabsTrigger>
        </TabsList>

        <TabsContent value="wizard">
          <CreateAgentWizard
            onOpenYaml={(y) => {
              setYamlText(y);
              setTab('yaml');
            }}
          />
        </TabsContent>

        <TabsContent value="yaml">
          <YamlDeploy text={yamlText} onTextChange={setYamlText} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
