import {previewMode,demoResponse} from './demo';
export type Row=Record<string,any>;
export async function api(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 if(previewMode)return demoResponse(path,method);
 const res=await fetch('/api'+path,{method,signal,headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:body instanceof FormData?body:JSON.stringify(body)});
 if(!res.ok){let message;try{const error=await res.json();message=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail||error)}catch{message=res.statusText}throw new Error(message)}
 return res.status===204?null:res.json();
}
export async function operationResult(value:Row,onProgress:(operation:Row)=>void,signal?:AbortSignal):Promise<any>{
 if(!value?.operation_id&&!(value?.id&&value?.kind&&['queued','running','complete','failed','cancelled'].includes(value.status)))return value;
 let current=value;
 for(;;){
  if(signal?.aborted)throw new DOMException('Request cancelled','AbortError');
  onProgress(current);
  if(current.status==='complete')return current.result;
  if(current.status==='failed'||current.status==='cancelled')throw new Error('Operation '+current.status+'. Review its progress below.');
  await new Promise(resolve=>setTimeout(resolve,500));
  current=await api('/operations/'+encodeURIComponent(current.operation_id||current.id),'GET',undefined,signal);
 }
}
