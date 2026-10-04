export const API=process.env.NEXT_PUBLIC_API_URL??'http://localhost:8000';
export const MOCK=process.env.NEXT_PUBLIC_MOCK==='1';
export class ApiError extends Error{constructor(public code:string,message:string,public status:number,public details?:unknown){super(message)}}
export async function api<T>(path:string,init?:RequestInit):Promise<T>{
  const r=await fetch(`${API}/api${path}`,init);
  if(!r.ok){const b=await r.json().catch(()=>null);throw new ApiError(b?.error?.code??'UNKNOWN',b?.error?.message??r.statusText,r.status,b?.error?.details)}
  return r.json()}
export const REPLAYS=['failed_supplier','clean_supplier','auditor_loop_fault_injection','decoy_trap_fault_injection'];
export const REPLAY_INFO:Record<string,string>={failed_supplier:'Failed supplier: a limit breach and conflicting batch IDs',clean_supplier:'Clean supplier: every check passes',auditor_loop_fault_injection:'Auditor loop: wrong value caught and re-read (fault injection)',decoy_trap_fault_injection:'Decoy trap: a look-alike ID is caught (fault injection)'};
