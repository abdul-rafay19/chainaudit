'use client';
import {useState} from 'react';import {api,ApiError} from '@/lib/api';import {Full} from '@/lib/data';
type Changed={rule_id:string;before:any;after:any};
const name=(x:any)=>typeof x==='string'?x:x?.compliance_status;
export function RulesPanel({wfId,mock,rules,findings,onChanged}:{wfId:string;mock:boolean;rules:any[];findings:Full[];onChanged:(c:Changed[])=>void}){
  const chem=rules.find(r=>r.id==='CHEM_MAX');const base=chem?.limit??15;const [limit,setLimit]=useState<number>(base);
  const [res,setRes]=useState<{changed:Changed[];llm_calls:number;ms?:number;local:boolean}|null>(null);const [err,setErr]=useState('');const [busy,setBusy]=useState(false);
  if(!chem)return null;
  async function apply(l:number){setErr('');setBusy(true);
    try{if(mock){const f=findings.find(x=>x.rule_id==='CHEM_MAX');const v=Number(f?.evidence.find(e=>e.field==='chemical_ppm')?.value);
        const after=v<=l?'PASSING_CONFIGURED_CHECK':'REQUIREMENT_NOT_SATISFIED';const changed=f&&f.compliance_status!==after?[{rule_id:'CHEM_MAX',before:f.compliance_status,after}]:[];
        setRes({changed,llm_calls:0,local:true});onChanged(changed)}
      else{const r=await api<{changed:Changed[];llm_calls:number;duration_ms:number}>(`/workflows/${wfId}/rerun-rules`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({overrides:{CHEM_MAX:{limit:l}}})});setRes({changed:r.changed,llm_calls:r.llm_calls,ms:r.duration_ms,local:false});onChanged(r.changed)}}
    catch(e){setErr(e instanceof ApiError?`${e.code}: ${e.message}`:'Rerun failed.')}finally{setBusy(false)}}
  return(<section className="rounded-lg border border-line bg-card p-4"><h2 className="font-medium">Rules</h2><p className="text-xs text-mute">Change a limit and re-check the evidence already read.</p>
    <div className="mt-3 flex flex-wrap items-end gap-3"><label className="text-sm">Maximum chemical concentration ({chem.unit})<input type="number" value={limit} onChange={e=>setLimit(Number(e.target.value))} className="mt-1 block w-28 rounded border border-line bg-paper px-2 py-1 font-mono"/></label>
      <button disabled={busy} onClick={()=>apply(limit)} className="rounded-md bg-act px-3 py-1.5 text-sm text-white disabled:opacity-60">{busy?'Re-checking…':'Re-check'}</button>
      <button disabled={busy} onClick={()=>{setLimit(base);apply(base)}} className="rounded-md border border-line px-3 py-1.5 text-sm">Reset to {base}</button></div>
    {err&&<p role="alert" className="mt-2 text-sm text-bad">{err}</p>}
    {res&&<div role="status" className="mt-3 rounded bg-ok/10 px-3 py-2 text-sm">{res.changed.length?res.changed.map(c=><div key={c.rule_id}><span className="font-mono">{c.rule_id}</span>: {name(c.before)} to {name(c.after)}</div>):'No verdict changed.'}
      <div className="mt-1 text-xs text-mute">Re-evaluated cached evidence. No extraction re-run. {res.llm_calls} model calls{res.ms!=null?` · ${res.ms} ms`:''}{res.local?' · computed locally in offline mode':''}</div></div>}</section>)}
