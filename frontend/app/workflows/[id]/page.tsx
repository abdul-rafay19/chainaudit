'use client';
import {use,useState} from 'react';import {useQueryClient} from '@tanstack/react-query';import {useWorkflowEvents} from '@/hooks/useWorkflowEvents';import {AgentTrace,FindingCard} from '@/components/trace';import {TrustStrip} from '@/components/trust-strip';
import {FindingDrawer} from '@/components/drawer';import {RulesPanel} from '@/components/rules';import {isMock,useWorkflow,Full} from '@/lib/data';
/* eslint-disable @typescript-eslint/no-explicit-any */
export default function Run({params}:{params:Promise<{id:string}>}){
  const {id}=use(params);const s=useWorkflowEvents(id);const qc=useQueryClient();const mock=isMock(id);
  const settled=!!s.summary||s.status==='done'||s.status==='failed';const wf=useWorkflow(id,settled?'final':'live');
  const [open,setOpen]=useState<string|null>(null);const [ghost,setGhost]=useState<Record<string,string>>({});const [local,setLocal]=useState<Record<string,string>>({});
  const full:Full[]=(settled&&wf.data?wf.data.findings.filter(f=>!f.superseded):[]).map(f=>local[f.rule_id]?{...f,compliance_status:local[f.rule_id]}:f);
  const live=Object.values(s.findings);const rules=wf.data?.workflow?.rule_set?.content?.rules??[];const sel=full.find(f=>f.finding_id===open);
  const nm=(x:any)=>typeof x==='string'?x:x?.compliance_status;
  function changed(c:{rule_id:string;before:any;after:any}[]){setGhost(Object.fromEntries(c.map(x=>[x.rule_id,nm(x.before)])));
    if(mock)setLocal(l=>({...l,...Object.fromEntries(c.map(x=>[x.rule_id,nm(x.after)]))}));else qc.invalidateQueries({queryKey:['wf',id]})}
  return(<><TrustStrip s={s}/><main className="mx-auto grid max-w-6xl gap-8 px-6 py-8 lg:grid-cols-[1.1fr_1fr]">
    <div><h1 className="mb-1 text-2xl font-semibold tracking-tight">{settled?'Audit ready for review':'Audit in progress'}</h1>
      <p className="mb-5 text-sm text-mute" aria-live="polite">{s.status==='failed'?'This run failed.':settled?'Open a finding to see its evidence and decide.':s.status==='running'?'Agents are working…':'Connecting…'}</p>
      <AgentTrace s={s}/>{settled&&<a href={`/audit/${id}`} className="mt-4 inline-block text-sm text-act">Open audit record</a>}{settled&&rules.length>0&&<div className="mt-6"><RulesPanel wfId={id} mock={mock} rules={rules} findings={full} onChanged={changed}/></div>}</div>
    <div><h2 className="mb-3 text-sm font-medium text-mute">Findings</h2>
      <div className="space-y-3">{settled&&full.length?full.map(f=><button key={f.finding_id} onClick={()=>setOpen(f.finding_id)} className="block w-full text-left"><FindingCard f={{id:f.finding_id,ruleId:f.rule_id,title:f.rule_title,status:f.compliance_status,reason:f.reason,audit:f.requires_human_review&&!f.review_decision?'review':'verified',escalation:f.escalation_reason,before:ghost[f.rule_id]}}/></button>)
        :live.length?live.map(f=><FindingCard key={f.id} f={f}/>):<p className="rounded-lg border border-dashed border-line p-6 text-sm text-mute">Findings appear here as the rules engine finishes each check.</p>}</div>
      <details className="mt-6 text-xs text-mute"><summary className="cursor-pointer">Event log ({s.log.length})</summary><ol className="mt-2 max-h-64 space-y-1 overflow-auto font-mono">{s.log.map(e=><li key={e.seq}>{e.seq} {e.type} {e.message}</li>)}</ol></details></div>
  </main>{sel&&<FindingDrawer key={sel.finding_id} f={sel} mock={mock} onClose={()=>setOpen(null)} onDecision={()=>{if(!mock)qc.invalidateQueries({queryKey:['wf',id]})}}/>}</>)}
