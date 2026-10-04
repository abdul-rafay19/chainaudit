'use client';import {QueryClient,QueryClientProvider} from '@tanstack/react-query';import {useState} from 'react';
export function Providers({children}:{children:React.ReactNode}){const [c]=useState(()=>new QueryClient());return <QueryClientProvider client={c}>{children}</QueryClientProvider>}
