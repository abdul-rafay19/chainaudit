import './globals.css';import type {Metadata} from 'next';import {Inter_Tight,JetBrains_Mono} from 'next/font/google';
import {Providers} from '@/components/providers';
const sans=Inter_Tight({subsets:['latin'],variable:'--font-sans'});const mono=JetBrains_Mono({subsets:['latin'],variable:'--font-mono'});
export const metadata:Metadata={title:'ChainAudit Control Center'};
export default function Root({children}:{children:React.ReactNode}){return(<html lang="en" className={`${sans.variable} ${mono.variable}`}><body><Providers>{children}</Providers></body></html>)}
