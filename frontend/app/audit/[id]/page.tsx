'use client';
import {use} from 'react';import {useAudit} from '@/lib/audit';import {TrustStrip} from '@/components/trust-strip';import {STATUS} from '@/components/trace';
function Step({title,children}:{title:string;children:React.ReactNode}){return(<li className="relative rounded-lg border border-line bg-card p-3 lg:min-w-0"><h3 className="mb-1.5 text-xs font-medium text-mute">{title}</h3><div className="space-y-1 text-sm">{children}</div></li>)}
const Mono=({t}:{t:string})=><div className="truncate font-mono text-xs">{t}</div>;
export default function AuditPage({params}:{params:Promise<{id:string}>}){
  const {id}=use(params);const q=useAudit(id);const a=q.data;
  return(<><TrustStrip/><main className="mx-auto max-w-6xl px-6 py-8">
    <div className="mb-1 flex items-center justify-between"><h1 className="text-2xl font-semibold tracking-tight">Audit record</h1><a href={`/workflows/${id}`} className="text-sm text-act">Back to run</a></div>
    <p className="mb-6 text-sm text-mute">Every decision below traces back to a quote on a page of a supplier document.</p>
    {q.isLoading&&<p className="text-sm text-mute">Loading…</p>}{q.isError&&<p role="alert" className="text-sm text-bad">Could not load this record. Check that the backend is running.</p>}
    {a&&<><ol aria-label="Provenance chain" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Step title="Supplier">{a.suppliers.length?a.suppliers.map(x=><Mono key={x} t={x}/>):<span className="text-mute">Not found</span>}</Step>
      <Step title="Batch">{a.batches.length?a.batches.map(x=><Mono key={x} t={x}/>):<span className="text-mute">Not found</span>}{a.batches.length>1&&<p className="text-xs text-warn">More than one batch ID across documents</p>}</Step>
      <Step title="Documents">{a.docs.map(d=><div key={d.name}><Mono t={d.name}/><span className="text-xs text-mute">{d.type.replace('_',' ')}</span></div>)}</Step>
      <Step title="Evidence">{a.findings.reduce((n,f)=>n+f.evidence.length,0)} values, each with page and quote</Step>
      <Step title="Rules">{a.ruleSet}<span className="block text-xs text-mute">Decided by code, not by a model</span></Step>
      <Step title="Findings">{a.findings.map(f=><div key={f.finding_id} className="flex justify-between gap-2"><span className="font-mono text-xs">{f.rule_id}</span><span className="text-xs">{STATUS[f.compliance_status]?.t??f.compliance_status}</span></div>)}</Step>
      <Step title="Reviewer decisions">{a.findings.some(f=>f.review_decision)?a.findings.filter(f=>f.review_decision).map(f=><div key={f.finding_id} className="text-xs"><span className="font-mono">{f.rule_id}</span>: {f.review_decision}</div>):<span className="text-mute">No decisions yet</span>}</Step>
      <Step title="Corrective actions">{a.actions} AI drafts<span className="block text-xs text-mute">AI draft. Review before sending.</span>{a.outbox.length>0&&<span className="block text-xs">{a.outbox.length} in demo outbox</span>}</Step></ol>
      <h2 className="mb-3 mt-10 text-sm font-medium text-mute">Event timeline ({a.events.length})</h2>
      <ol className="divide-y divide-line rounded-lg border border-line bg-card text-sm">{a.events.map((e:any,i:number)=><li key={e.seq??i} className="flex gap-3 px-3 py-2"><span className="w-8 shrink-0 font-mono text-xs text-mute">{e.seq??i+1}</span><span className="w-44 shrink-0 font-mono text-xs">{e.type}</span><span className="text-mute">{e.message}</span></li>)}</ol></>}
  </main></>)}
