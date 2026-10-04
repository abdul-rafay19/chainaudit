'use client';
import {useQuery} from '@tanstack/react-query';import {useState} from 'react';import {api,MOCK} from '@/lib/api';import {Full} from '@/lib/data';import {FindingDrawer} from '@/components/drawer';import {TrustStrip} from '@/components/trust-strip';import {STATUS} from '@/components/trace';
export default function Review(){
  const q=useQuery<Full[]>({queryKey:['queue'],queryFn:()=>api<Full[]>('/findings?needs_review=true'),enabled:!MOCK});const [open,setOpen]=useState<Full|null>(null);
  return(<><TrustStrip/><main className="mx-auto max-w-3xl px-6 py-10"><h1 className="text-2xl font-semibold tracking-tight">Review queue</h1><p className="mb-5 text-sm text-mute">Findings waiting for a human decision.</p>
    {MOCK?<p className="rounded-lg border border-dashed border-line p-6 text-sm text-mute">The queue reads from the live backend. In offline mode, open a replay and click a finding to review it.</p>
    :q.isLoading?<p className="text-sm text-mute">Loading…</p>:q.isError?<p role="alert" className="text-sm text-bad">Could not load the queue. Check that the backend is running.</p>
    :!q.data?.length?<p className="rounded-lg border border-dashed border-line p-6 text-sm text-mute">Nothing to review. Upload evidence to create findings.</p>
    :<ul className="divide-y divide-line rounded-xl border border-line bg-card">{q.data.map(f=><li key={f.finding_id}><button onClick={()=>setOpen(f)} className="flex w-full justify-between px-4 py-3 text-left text-sm hover:bg-paper"><span><span className="font-mono text-xs text-mute">{f.rule_id}</span> {f.rule_title}</span><span>{STATUS[f.compliance_status]?.t}</span></button></li>)}</ul>}
  </main>{open&&<FindingDrawer f={open} mock={false} onClose={()=>{setOpen(null);q.refetch()}}/>}</>)}
