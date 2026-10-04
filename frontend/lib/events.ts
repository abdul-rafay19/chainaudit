/* Pure reducer: events -> UI state. No timers, no fake progress. Swap loose types for lib/api-types.ts after `npm run types`. */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type Ev={seq:number;type:string;agent:string;message:string;finding_id?:string|null;document_id?:string|null;data:Record<string,any>;replay?:boolean};
export type NodeState='queued'|'working'|'done'|'conflict'|'escalated';
export type Doc={id:string;name:string;state:NodeState;docType?:string};
export type Finding={id:string;ruleId:string;title:string;status:string;reason:string;audit:'pending'|'verified'|'review';escalation?:string|null;before?:string};
export type Loop={phase:'conflict'|'rereading'|'fixed';field:string;doc:string;from?:any;to?:any;mode?:string;page?:number|null;attempt?:number}|null;
export type State={status:'idle'|'running'|'done'|'failed';last:number;meta:{provider?:string;mock?:boolean;ruleSet?:string;replay?:boolean;fault?:boolean};
 orch:NodeState;compliance:NodeState;auditor:NodeState;docs:Record<string,Doc>;findings:Record<string,Finding>;loop:Loop;log:Ev[];summary?:Record<string,any>};
export const initial:State={status:'idle',last:0,meta:{},orch:'queued',compliance:'queued',auditor:'queued',docs:{},findings:{},loop:null,log:[]};
export function reduce(s:State,e:Ev):State{
  if(e.seq<=s.last)return s; // de-dupe by seq
  const n:State={...s,last:e.seq,log:[...s.log,e],docs:{...s.docs},findings:{...s.findings}};
  const d=e.data??{};const byName=(nm:string)=>Object.values(n.docs).find(x=>x.name===nm);
  const setDoc=(id:string|undefined|null,st:NodeState,extra:Partial<Doc>={})=>{const x=id?n.docs[id]:undefined;if(x)n.docs[x.id]={...x,state:st,...extra}};
  switch(e.type){
    case'WORKFLOW_CREATED':n.status='running';n.orch='working';n.meta={provider:d.provider,mock:d.mock,ruleSet:d.rule_set,replay:!!e.replay};break;
    case'DOCUMENT_RECEIVED':if(e.document_id)n.docs[e.document_id]={id:e.document_id,name:d.document,state:'queued'};break;
    case'EXTRACTION_STARTED':setDoc(e.document_id,'working');break;
    case'EXTRACTION_COMPLETED':setDoc(e.document_id,'done',{docType:d.doc_type});if(d.fault_injected)n.meta={...n.meta,fault:true};break;
    case'EXTRACTION_FAILED':setDoc(e.document_id,'escalated');break;
    case'COMPLIANCE_CHECK_STARTED':
      if(d.reevaluation){for(const c of d.changes??[]){const f=n.findings[c.finding_id];if(f)n.findings[f.id]={...f,before:f.status,status:c.after?.compliance_status??f.status,reason:c.after?.reason??f.reason}}}
      else n.compliance='working';break;
    case'FINDING_CREATED':if(e.finding_id)n.findings[e.finding_id]={id:e.finding_id,ruleId:d.rule_id,title:d.rule_title,status:d.compliance_status,reason:d.reason,audit:'pending',escalation:d.escalation_reason};n.compliance='done';break;
    case'AUDIT_STARTED':n.auditor='working';break;
    case'AUDIT_CONFLICT_FOUND':
      if(d.case==='A'){n.auditor='conflict';n.loop={phase:'conflict',field:d.field,doc:d.document,from:d.previous_value,attempt:d.attempts_used};const x=byName(d.document);if(x)setDoc(x.id,'conflict')}
      else n.auditor='escalated';break;
    case'REEXTRACTION_REQUESTED':{n.loop={phase:'rereading',field:d.field,doc:d.document,from:n.loop?.from,mode:d.mode,page:d.page_hint,attempt:d.attempt};const x=byName(d.document);if(x)setDoc(x.id,'working');break}
    case'REEXTRACTION_COMPLETED':{n.loop={phase:'fixed',field:d.field,doc:d.document,from:d.old_value,to:d.new_value,page:d.page};n.auditor='working';const x=byName(d.document);if(x)setDoc(x.id,'done');break}
    case'HUMAN_REVIEW_REQUIRED':if(e.finding_id&&n.findings[e.finding_id])n.findings[e.finding_id]={...n.findings[e.finding_id],audit:'review',escalation:d.escalation_reason};break;
    case'AUDIT_COMPLETED':n.auditor=n.auditor==='escalated'?'escalated':'done';n.summary=d;for(const f of Object.values(n.findings))if(f.audit==='pending')n.findings[f.id]={...f,audit:'verified'};break;
    case'WORKFLOW_COMPLETED':n.status='done';n.orch='done';break;
    case'WORKFLOW_FAILED':n.status='failed';n.orch='escalated';break;
  }
  return n}
