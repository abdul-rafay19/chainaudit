import {State} from '@/lib/events';
export function TrustStrip({s}:{s?:State}){
  const chip='rounded-full border border-line bg-card px-2.5 py-0.5 text-xs text-mute';
  return(<header className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-line bg-paper/90 px-6 py-3 backdrop-blur">
    <span className={chip}>Synthetic Demo Data</span><span className={chip}>{s?.meta.ruleSet??'Demo Buyer Framework v1.0'}</span>
    <span className={chip}>Model: {s?.meta.provider??'unknown'}{s?.meta.mock?' (offline mock)':''}</span>
    {s?.meta.replay&&<span className="rounded-full border border-act/40 bg-act/10 px-2.5 py-0.5 text-xs text-act">Replay</span>}
    {s?.meta.fault&&<span className="rounded-full border border-warn/50 bg-warn/10 px-2.5 py-0.5 text-xs text-warn">Fault injection (test mode)</span>}</header>)}
