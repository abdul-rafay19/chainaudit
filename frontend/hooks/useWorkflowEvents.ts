'use client';
import {useEffect,useReducer} from 'react';import {API} from '@/lib/api';import {Ev,initial,reduce} from '@/lib/events';
const TYPES=['WORKFLOW_CREATED','DOCUMENT_RECEIVED','EXTRACTION_STARTED','EXTRACTION_COMPLETED','EXTRACTION_FAILED','COMPLIANCE_CHECK_STARTED','FINDING_CREATED','AUDIT_STARTED','AUDIT_CHECK_RESULT','AUDIT_CONFLICT_FOUND','REEXTRACTION_REQUESTED','REEXTRACTION_COMPLETED','HUMAN_REVIEW_REQUIRED','CORRECTIVE_ACTION_DRAFTED','REVIEW_DECISION_RECORDED','RULES_RERUN_STARTED','RULES_RERUN_COMPLETED','DISPATCH_RECORDED','AUDIT_COMPLETED','WORKFLOW_COMPLETED','WORKFLOW_FAILED'];
/** Live: EventSource (native Last-Event-ID resume) into the reducer, de-duped by seq. Mock (id "mock-<name>"): plays a recording through the same reducer. */
export function useWorkflowEvents(id:string){
  const [state,dispatch]=useReducer(reduce,initial);
  useEffect(()=>{
    if(id.startsWith('mock-')){let off=false;const timers:number[]=[];
      fetch(`/replays/${id.slice(5)}.json`).then(r=>r.json()).then((d:{events:Omit<Ev,'seq'>[]})=>{
        d.events.forEach((e,i)=>timers.push(window.setTimeout(()=>{if(!off)dispatch({...e,seq:i+1,replay:true} as Ev)},i*350)))});
      return()=>{off=true;timers.forEach(clearTimeout)}}
    const es=new EventSource(`${API}/api/workflows/${id}/events`);
    TYPES.forEach(t=>es.addEventListener(t,m=>dispatch(JSON.parse((m as MessageEvent).data))));
    return()=>es.close()},[id]);
  return state}
