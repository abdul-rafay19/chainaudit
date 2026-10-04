'use client';
import clsx from 'clsx';import {Loop,NodeState,State,Finding} from '@/lib/events';
const dot:Record<NodeState,string>={queued:'bg-line',working:'bg-aud',done:'bg-ok',conflict:'bg-bad',escalated:'bg-warn'};
const label:Record<NodeState,string>={queued:'Queued',working:'Working',done:'Complete',conflict:'Conflict',escalated:'Needs review'};
export const STATUS:Record<string,{t:string;c:string}>={PASSING_CONFIGURED_CHECK:{t:'Passing',c:'text-ok border-ok/40 bg-ok/10'},REQUIREMENT_NOT_SATISFIED:{t:'Requirement not satisfied',c:'text-bad border-bad/40 bg-bad/10'},HUMAN_REVIEW_REQUIRED:{t:'Human review',c:'text-warn border-warn/50 bg-warn/10'}};
export const ESC:Record<string,string>={LOW_EXTRACTION_CONFIDENCE:'We could not read this value reliably',CONFLICTING_VALUES:'Documents disagree with each other',MISSING_REQUIRED_FIELD:'A required value is missing',UNSUPPORTED_CONCLUSION:'The auditor could not confirm the conclusion from the quote',EXTRACTION_FAILED:'We could not read this file',REEXTRACTION_LIMIT:'Re-reading did not resolve it',UNVERIFIABLE_SOURCE:'This source cannot be checked against document text (scan or suspicious content)',UNKNOWN_DOCUMENT_TYPE:'We could not tell what kind of document this is'};
function Node({name,tone,state,sub}:{name:string;tone:string;state:NodeState;sub?:string}){
  return(<div className={clsx('relative overflow-hidden rounded-lg border bg-card px-3 py-2.5 transition-colors duration-300',state==='conflict'?'border-bad pulse-bad':'border-line',state==='working'&&'working')}>
    <div className="flex items-center gap-2"><span className={clsx('h-2 w-2 rounded-full transition-colors',dot[state])}/><span className="text-sm font-medium" style={{color:tone}}>{name}</span></div>
    <div className="mt-0.5 flex justify-between gap-2 text-xs text-mute"><span className="truncate font-mono">{sub??''}</span><span>{label[state]}</span></div></div>)}
export function AgentTrace({s}:{s:State}){
  const docs=Object.values(s.docs);
  return(<section aria-label="Agent trace" className="space-y-3">
    <Node name="Orchestrator" tone="#64748B" state={s.orch} sub={s.meta.ruleSet}/>
    <div className="grid gap-2 sm:grid-cols-3">{docs.length?docs.map(d=><Node key={d.id} name="Extractor" tone="#0F8B8D" state={d.state} sub={d.name}/>):<Node name="Extractor" tone="#0F8B8D" state="queued" sub="waiting for documents"/>}</div>
    <div className="grid gap-2 sm:grid-cols-2"><Node name="Compliance (rules engine)" tone="#4F46E5" state={s.compliance} sub="deterministic code"/><Node name="Auditor" tone="#7C3AED" state={s.auditor} sub="checks every quote against the PDF"/></div>
    <LoopBanner l={s.loop}/></section>)}
function LoopBanner({l}:{l:Loop}){
  if(!l)return <div className="h-[72px]" aria-hidden/>; // reserved space: no layout shift
  const msg=l.phase==='conflict'?'Auditor challenged a value: the quote was not found in the document.':l.phase==='rereading'?(l.mode==='document'?'Extractor is re-reading the whole document.':`Extractor is re-reading page ${l.page??''}.`):'Value corrected. Rules re-evaluated against the new evidence.';
  return(<div role="status" className={clsx('flex h-[72px] items-center gap-4 rounded-lg border px-4 text-sm',l.phase==='fixed'?'border-ok/50 bg-ok/10':'border-bad/50 bg-bad/10')}>
    <div className="flex-1"><div className="font-medium">{msg}</div><div className="font-mono text-xs text-mute">{l.field} · {l.doc}</div></div>
    {l.from!=null&&<span className={clsx('rounded px-2 py-1 font-mono text-xs transition-all',l.phase==='fixed'?'bg-line text-mute line-through':'bg-bad/15 text-bad')}>{String(l.from)}</span>}
    {l.phase==='fixed'&&<span className="rounded bg-ok/15 px-2 py-1 font-mono text-xs text-ok">{String(l.to)}</span>}</div>)}
export function FindingCard({f}:{f:Finding}){
  const st=STATUS[f.status]??{t:f.status,c:'border-line'};
  return(<article className="rounded-lg border border-line bg-card p-4">
    <div className="flex items-start justify-between gap-3"><div><div className="font-mono text-xs text-mute">{f.ruleId}</div><h3 className="font-medium">{f.title}</h3></div>
      <span className={clsx('shrink-0 rounded-full border px-2.5 py-0.5 text-xs',st.c)}>{st.t}</span></div>
    {f.before&&f.before!==f.status&&<div className="mt-1 text-xs text-mute line-through">{STATUS[f.before]?.t??f.before}</div>}
    <p className="mt-2 text-sm text-mute">{f.reason}</p>
    {f.escalation&&<p className="mt-2 rounded bg-warn/10 px-2 py-1 text-xs text-warn">{ESC[f.escalation]??f.escalation}</p>}
    <div className="mt-3 text-xs text-mute">{f.audit==='verified'?'Auditor verified the evidence':f.audit==='review'?'Waiting for a human decision':'Auditor checking…'}</div></article>)}
