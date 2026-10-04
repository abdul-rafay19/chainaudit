'use client';
import Link from 'next/link';import {usePathname} from 'next/navigation';import clsx from 'clsx';
const NAV=[['/workflows','Overview'],['/start','New audit'],['/review','Review queue']];
export function Shell({children}:{children:React.ReactNode}){
  const p=usePathname();const on=(h:string)=>p===h||(h==='/workflows'&&(p.startsWith('/workflows/')||p.startsWith('/audit/')));
  return(<div className="min-h-screen md:flex">
    <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-card p-4 md:sticky md:top-0 md:flex md:h-screen">
      <Link href="/" className="mb-6 flex items-center gap-2 text-base font-semibold tracking-tight"><span className="grid h-6 w-6 place-items-center rounded bg-ink text-xs text-paper">C</span>ChainAudit</Link>
      <nav aria-label="Main" className="space-y-1">{NAV.map(([h,l])=><Link key={h} href={h} aria-current={on(h)?'page':undefined} className={clsx('block rounded-md px-3 py-2 text-sm transition-colors',on(h)?'bg-paper font-medium text-ink':'text-mute hover:bg-paper hover:text-ink')}>{l}</Link>)}</nav>
      <p className="mt-auto text-xs text-mute">Synthetic demo data. Demo buyer framework, not any real buyer&apos;s rules.</p></aside>
    <div className="min-w-0 flex-1"><nav aria-label="Main" className="flex gap-4 border-b border-line bg-card px-4 py-3 text-sm md:hidden"><Link href="/" className="font-semibold">ChainAudit</Link>{NAV.map(([h,l])=><Link key={h} href={h} className={on(h)?'font-medium':'text-mute'}>{l}</Link>)}</nav>{children}</div></div>)}
