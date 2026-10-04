'use client';
import {useState} from 'react';import clsx from 'clsx';import {API,api,ApiError} from '@/lib/api';import {Full,EvidenceT} from '@/lib/data';import {ESC,STATUS} from './trace';
function Img({f,i,mock}:{f:Full;i:number;mock:boolean}){
  const [bad,setBad]=useState(mock);
  if(bad||!f.evidence[i].page)return <p className="rounded border border-dashed border-line p-3 text-xs text-mute">{mock?'Page image is shown when connected to the backend.':'No page image for this evidence. Quote, document and page are shown above.'}</p>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img alt={`Page ${f.evidence[i].page} of ${f.evidence[i].source_document}`} src={`${API}/api/findings/${f.finding_id}/evidence-image?evidence_index=${i}`} onError={()=>setBad(true)} className="pulse-bad w-full rounded border border-line"/>}
function Ev({e,f,i,mock}:{e:EvidenceT;f:Full;i:number;mock:boolean}){
  return(<div className="space-y-2 rounded-lg border border-line p-3"><div className="flex justify-between text-xs"><span className="font-mono">{e.field}</span><span className="text-mute">{e.verified?'Quote found in document':`Not verified (${e.verification})`}</span></div>
    <div className="font-mono text-lg">{e.value===null?'Not found in this document':`${e.value}${e.unit?` ${e.unit}`:''}`}</div>
    {e.quote&&<blockquote className="border-l-2 border-act pl-3 font-mono text-xs">{e.quote}</blockquote>}
    <div className="text-xs text-mute">{e.source_document}{e.page?`, page ${e.page}`:''} · confidence score {e.confidence.toFixed(2)}</div><Img f={f} i={i} mock={mock}/></div>)}
export function FindingDrawer({f,mock,onClose,onDecision}:{f:Full;mock:boolean;onClose:()=>void;onDecision?:(d:string)=>void}){
  const st=STATUS[f.compliance_status];const [lang,setLang]=useState<'en'|'roman_ur'>('en');
  const [decision,setDecision]=useState(f.review_decision);const [txt,setTxt]=useState<{en:string;roman_ur:string}>({en:f.corrective_action?.en??'',roman_ur:f.corrective_action?.roman_ur??''});
  const [err,setErr]=useState('');const [msg,setMsg]=useState('');const [editing,setEditing]=useState(false);
  const approved=decision==='approved'||decision==='edited';
  async function decide(d:string){setErr('');setMsg('');const prev=decision;setDecision(d);// optimistic, reconciled below
    try{if(!mock)await api(`/reviews/${f.finding_id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:d,reviewer:'demo-reviewer',...(d==='edited'?{edited_text:txt[lang]}:{})})});onDecision?.(d);setEditing(false)}
    catch(e){setDecision(prev);setErr(e instanceof ApiError?`${e.code}: ${e.message}`:'Could not record the decision.')}}
  async function send(){setErr('');setMsg('');if(!approved)return setErr('Approve or edit this finding before sending.');
    try{if(!mock)await api(`/findings/${f.finding_id}/dispatch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:'demo_whatsapp',language:lang})});setMsg('Saved to the demo outbox. Nothing was sent to a real person.')}
    catch(e){setErr(e instanceof ApiError?`${e.code}: ${e.message}`:'Dispatch failed.')}}
  const conflict=f.escalation_reason==='CONFLICTING_VALUES'&&f.evidence.length>1;
  return(<aside role="dialog" aria-label={f.rule_title} className="fixed inset-y-0 right-0 z-20 w-full max-w-2xl overflow-y-auto border-l border-line bg-paper p-6 shadow-2xl">
    <div className="flex items-start justify-between"><div><div className="font-mono text-xs text-mute">{f.rule_id}</div><h2 className="text-xl font-semibold">{f.rule_title}</h2></div><button onClick={onClose} className="rounded border border-line px-2 py-1 text-sm">Close</button></div>
    <span className={clsx('mt-3 inline-block rounded-full border px-2.5 py-0.5 text-xs',st?.c)}>{st?.t??f.compliance_status}</span>
    <p className="mt-3 text-sm">{f.reason}</p>
    {f.escalation_reason&&<p className="mt-3 rounded bg-warn/10 px-3 py-2 text-sm text-warn">{ESC[f.escalation_reason]??f.escalation_reason}</p>}
    {f.auditor_flags.length>0&&<div className="mt-3 flex flex-wrap gap-1.5">{f.auditor_flags.map(x=><span key={x} className="rounded-full border border-aud/40 bg-aud/10 px-2 py-0.5 font-mono text-xs text-aud">{x}</span>)}</div>}
    <h3 className="mb-2 mt-6 text-sm font-medium text-mute">Evidence</h3>
    {conflict&&<p className="mb-2 text-sm font-medium">Potential inconsistency requiring human review. The auditor did not try to fix it because both quotes are in the documents.</p>}
    <div className={clsx('grid gap-3',f.evidence.length>1&&'sm:grid-cols-2')}>{f.evidence.length?f.evidence.map((e,i)=><Ev key={i} e={e} f={f} i={i} mock={mock}/>):<p className="text-sm text-mute">No evidence attached to this finding.</p>}</div>
    {f.requires_human_review&&<section className="mt-6 space-y-3 border-t border-line pt-5"><h3 className="text-sm font-medium text-mute">Your decision</h3>
      {decision&&<p className="text-sm">Decision recorded: <b>{decision.replace('_',' ')}</b></p>}
      <div className="flex flex-wrap gap-2">{[['approved','Approve'],['rejected','Reject'],['more_evidence','Request more evidence']].map(([k,l])=><button key={k} disabled={!!decision} onClick={()=>decide(k)} className="rounded-md border border-line bg-card px-3 py-1.5 text-sm hover:border-act disabled:opacity-50">{l}</button>)}
        <button disabled={!!decision||!f.corrective_action} onClick={()=>setEditing(true)} className="rounded-md border border-line bg-card px-3 py-1.5 text-sm hover:border-act disabled:opacity-50">Edit draft</button></div>
      {f.corrective_action&&<div><div className="mb-1 flex gap-1">{(['en','roman_ur'] as const).map(k=><button key={k} onClick={()=>setLang(k)} className={clsx('rounded px-2 py-1 text-xs',lang===k?'bg-act text-white':'border border-line')}>{k==='en'?'English':'Roman Urdu'}</button>)}</div>
        <textarea readOnly={!editing} value={txt[lang]} onChange={e=>setTxt({...txt,[lang]:e.target.value})} rows={6} className="w-full rounded-md border border-line bg-card p-3 text-sm"/>
        <p className="mt-1 text-xs text-mute">{f.corrective_action.label}</p>
        <div className="mt-2 flex gap-2">{editing&&<button onClick={()=>decide('edited')} className="rounded-md bg-act px-3 py-1.5 text-sm text-white">Save edited draft</button>}
          <button onClick={send} aria-disabled={!approved} className={clsx('rounded-md px-3 py-1.5 text-sm',approved?'bg-ink text-paper':'cursor-not-allowed border border-line text-mute')}>Send to demo outbox</button></div></div>}
      {err&&<p role="alert" className="text-sm text-bad">{err}</p>}{msg&&<p role="status" className="text-sm text-ok">{msg}</p>}</section>}
  </aside>)}
