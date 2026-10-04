/* eslint-disable @typescript-eslint/no-explicit-any */
import {useQuery} from '@tanstack/react-query';import {api} from './api';import {Full} from './data';
export type Audit={status:string;suppliers:string[];batches:string[];docs:{name:string;type:string}[];findings:Full[];ruleSet:string;reviews:any[];actions:number;outbox:any[];events:any[]};
const uniq=(a:any[])=>Array.from(new Set(a.filter(Boolean).map(String)));
function fromReplay(d:any):Audit{const fs:Full[]=(d.findings??[]).filter((f:Full)=>!f.superseded);const ev=(k:string)=>uniq(fs.flatMap(f=>f.evidence.filter(e=>e.field===k).map(e=>e.value as string)));
  return{status:d.workflow.status,suppliers:ev('supplier_id'),batches:ev('batch_id'),docs:(d.documents??[]).map((x:any)=>({name:x.original_name??x.name,type:x.doc_type??'unknown'})),findings:fs,ruleSet:d.workflow.rule_set?.name?`${d.workflow.rule_set.name} v${d.workflow.rule_set.version}`:'Demo Buyer Framework',reviews:[],actions:fs.filter(f=>f.corrective_action).length,outbox:[],events:d.events??[]}}
function fromApi(d:any):Audit{const fs:Full[]=(d.findings??[]).filter((f:Full)=>!f.superseded);
  return{status:d.status,suppliers:d.supplier_ids??[],batches:d.batch_ids??[],docs:(d.documents??[]).map((x:any)=>({name:x.original_name,type:x.doc_type})),findings:fs,ruleSet:d.rule_set?.name?`${d.rule_set.name} v${d.rule_set.version}`:'Demo Buyer Framework',reviews:d.reviews??[],actions:(d.corrective_actions??[]).length,outbox:d.outbox??[],events:d.events??[]}}
export function useAudit(id:string){return useQuery<Audit>({queryKey:['audit',id],queryFn:()=>id.startsWith('mock-')?fetch(`/replays/${id.slice(5)}.json`).then(r=>r.json()).then(fromReplay):api<any>(`/audit/${id}`).then(fromApi)})}
