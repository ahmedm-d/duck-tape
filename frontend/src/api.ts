import {previewMode,demoResponse} from './demo';
export type Row=Record<string,any>;
// Set by AuthGate when Supabase sign-in is enabled; returns the current (auto-refreshed) access token.
let sessionToken:(()=>Promise<string|undefined>)|null=null;
export function setSessionToken(getter:(()=>Promise<string|undefined>)|null){sessionToken=getter}
export const UNAUTHORIZED_EVENT='duck-tape:unauthorized';
async function authorization():Promise<Record<string,string>>{const token=sessionToken?await sessionToken():undefined;return token?{Authorization:'Bearer '+token}:{}}
function signedOut(res:Response){if(res.status===401&&sessionToken)dispatchEvent(new Event(UNAUTHORIZED_EVENT))}
export async function api(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 if(previewMode)return demoResponse(path,method);
 const res=await fetch('/api'+path,{method,signal,headers:{...(body instanceof FormData?{}:{'Content-Type':'application/json'}),...await authorization()},body:body===undefined?undefined:body instanceof FormData?body:JSON.stringify(body)});
 signedOut(res);
 if(!res.ok){let message;try{const error=await res.json();message=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail||error)}catch{message=res.statusText}throw new Error(message)}
 return res.status===204?null:res.json();
}
// Download links can't send an Authorization header, so with sign-in enabled fetch the file and save it locally.
export async function downloadLink(event:{preventDefault():void;currentTarget:HTMLAnchorElement}){
 if(!sessionToken||previewMode)return;
 event.preventDefault();
 const href=event.currentTarget.href,res=await fetch(href,{headers:await authorization()});signedOut(res);
 if(!res.ok){alert('Download failed: '+res.statusText);return}
 const name=/filename="?([^";]+)"?/i.exec(res.headers.get('Content-Disposition')||'')?.[1]||href.split('/').pop()?.split('?')[0]||'download';
 const url=URL.createObjectURL(await res.blob()),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
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
