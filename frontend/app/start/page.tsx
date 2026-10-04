'use client';
import {useRouter} from 'next/navigation';import {useEffect,useState} from 'react';import {api,API,MOCK,REPLAYS,REPLAY_INFO,ApiError} from '@/lib/api';import {TrustStrip} from '@/components/trust-strip';
export default function Start(){
  const r=useRouter();const [err,setErr]=useState('');const [busy,setBusy]=useState(false);const [live,setLive]=useState<'checking'|'ready'|'waking'>('checking');
  // Wake a sleeping free-tier backend as soon as someone opens this page.
  useEffect(()=>{if(MOCK)return;let off=false;const ping=(n:number)=>fetch(`${API}/api/health`).then(x=>{if(!x.ok)throw 0;if(!off)setLive('ready')}).catch(()=>{if(off)return;setLive('waking');if(n<12)setTimeout(()=>ping(n+1),5000)});ping(0);return()=>{off=true}},[]);
  async function upload(files:FileList|null){if(!files?.length)return;if(MOCK)return setErr('Uploads need the live backend. Recorded runs work without it.');setBusy(true);setErr('');
    const fd=new FormData();Array.from(files).forEach(f=>fd.append('files',f));
    try{const x=await api<{workflow_id:string}>('/evidence/upload',{method:'POST',body:fd});r.push(`/workflows/${x.workflow_id}`)}
    catch(e){setErr(e instanceof ApiError?`${e.code}: ${e.message}`:'Could not reach the live backend. Free hosting sleeps when idle and takes about a minute to wake. Try again shortly, or watch a recorded run below.')}finally{setBusy(false)}}
  return(<><TrustStrip/><main className="mx-auto max-w-3xl px-6 py-12">
    <h1 className="text-3xl font-semibold tracking-tight">Start an audit</h1>
    <p className="mt-2 max-w-xl text-mute">Drop supplier PDFs. Agents read them, a rules engine decides, and an auditor checks every quote against the page it came from.</p>
    {!MOCK&&<p role="status" className="mt-4 text-xs text-mute">Live backend: {live==='ready'?'ready':live==='waking'?'waking up, this can take about a minute…':'checking…'}{live==='waking'&&<> · <a href={`${API}/api/health`} target="_blank" rel="noreferrer" className="text-act underline">Open the backend once to wake it</a></>}</p>}
    <label className="mt-4 flex h-44 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-line bg-card text-sm text-mute transition-colors hover:border-act focus-within:border-act" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();upload(e.dataTransfer.files)}}>
      <span className="font-medium text-ink">{busy?'Uploading…':'Drop PDF, PNG or JPG files here'}</span><span>or click to choose files</span>
      <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg" className="sr-only" onChange={e=>upload(e.target.files)}/></label>
    {err&&<p role="alert" className="mt-3 text-sm text-bad">{err}</p>}
    <h2 className="mb-3 mt-12 text-sm font-medium text-mute">Or watch a recorded run (works anytime)</h2>
    <ul className="divide-y divide-line rounded-xl border border-line bg-card">{REPLAYS.map(n=><li key={n}><button onClick={()=>r.push(`/workflows/mock-${n}`)} className="flex w-full items-center justify-between px-4 py-3 text-left text-sm hover:bg-paper"><span>{REPLAY_INFO[n]}</span><span className="text-act">Start replay</span></button></li>)}</ul>
  </main></>)}
