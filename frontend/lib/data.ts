/* eslint-disable @typescript-eslint/no-explicit-any */
import {useQuery} from '@tanstack/react-query';import {api} from './api';
export type EvidenceT={field:string;value:string|number|null;unit?:string|null;source_document:string;document_id:string;page:number|null;quote:string|null;confidence:number;verification:string;verified:boolean;bbox?:number[]|null};
export type Full={finding_id:string;workflow_id?:string;rule_id:string;rule_title:string;compliance_status:string;reason:string;escalation_reason:string|null;evidence:EvidenceT[];auditor_flags:string[];audit_status:string;requires_human_review:boolean;review_decision:string|null;corrective_action:{en:string;roman_ur:string;status:string;label:string}|null;superseded:boolean;audit_notes:string[]};
export type WF={workflow:{id:string;status:string;rule_set:any};findings:Full[]};
export const isMock=(id:string)=>id.startsWith('mock-');
export function useWorkflow(id:string,tick:string){
  return useQuery<WF>({queryKey:['wf',id,tick],queryFn:()=>isMock(id)?fetch(`/replays/${id.slice(5)}.json`).then(r=>r.json()):api<any>(`/workflows/${id}`).then(normalize)})}
/** Live API is flat ({workflow_id,status,findings,rule_set}); replay files nest it under `workflow`. Normalise to one shape. */
function normalize(d:any):WF{return d.workflow?d:{workflow:{id:d.workflow_id,status:d.status,rule_set:d.rule_set},findings:d.findings??[]}}
