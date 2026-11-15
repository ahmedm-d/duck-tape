export type PullRequest = {number:number;title:string;author:string;head:string;base:string;sha:string;draft:boolean;updated:string};
export type CI = {state:'passed'|'failed'|'pending'|'unknown'|'none'|'skipped';label:string;detail:string};

export function githubRepository(value:string):string|null {
  try {
    const url=new URL(value);
    if(url.protocol!=='https:'||url.hostname!=='github.com'||url.port||url.username||url.password||url.search||url.hash)return null;
    const parts=url.pathname.replace(/\/$/,'').replace(/\.git$/,'').split('/').filter(Boolean);
    return parts.length===2&&parts.every(part=>/^[\w.-]+$/.test(part)&&!['.','..'].includes(part))?parts.join('/'):null;
  } catch {return null}
}

async function request(path:string,signal:AbortSignal) {
  // shortcut: public reads use GitHub's anonymous quota; add backend authentication for private repositories or higher limits.
  const response=await fetch('https://api.github.com/repos/'+path,{signal:AbortSignal.any([signal,AbortSignal.timeout(15000)]),credentials:'omit',referrerPolicy:'no-referrer',headers:{Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}});
  if(!response.ok)throw new Error(response.status===403||response.status===429?'GitHub request limited or forbidden. Retry later.':response.status===404?'Project unavailable. This view supports public GitHub projects.':`GitHub request failed (${response.status}).`);
  return {data:await response.json(),more:!!response.headers.get('Link')?.includes('rel="next"')};
}

export async function pullRequests(repository:string,page:number,signal:AbortSignal):Promise<{rows:PullRequest[];more:boolean}> {
  const {data,more}=await request(`${repository}/pulls?state=open&sort=updated&direction=desc&per_page=5&page=${page}`,signal);
  if(!Array.isArray(data)||data.length>5)throw new Error('GitHub returned an invalid pull request list.');
  const rows=data.map(row=>{
    if(!Number.isSafeInteger(row.number)||row.number<1||typeof row.title!=='string'||typeof row.user?.login!=='string'||typeof row.head?.ref!=='string'||typeof row.base?.ref!=='string'||!/^([a-f0-9]{40}|[a-f0-9]{64})$/i.test(row.head?.sha||'')||typeof row.updated_at!=='string')throw new Error('GitHub returned incomplete PR metadata.');
    return {number:row.number,title:row.title,author:row.user.login,head:row.head.ref,base:row.base.ref,sha:row.head.sha,draft:!!row.draft,updated:row.updated_at};
  });
  return {rows,more};
}

export async function commitCI(repository:string,sha:string,signal:AbortSignal):Promise<CI> {
  const [checks,status]=await Promise.all([
    request(`${repository}/commits/${sha}/check-runs?filter=latest&per_page=100`,signal),
    request(`${repository}/commits/${sha}/status?per_page=100`,signal),
  ]);
  if(!Array.isArray(checks.data.check_runs)||!Array.isArray(status.data.statuses)||typeof checks.data.total_count!=='number'||typeof status.data.total_count!=='number')throw new Error('Incomplete CI response.');
  const runs=checks.data.check_runs,contexts=status.data.statuses;
  if(status.data.sha!==sha||runs.some((run:any)=>run.head_sha!==sha))throw new Error('CI response does not match this head commit.');
  const detail=`${runs.length} checks, ${contexts.length} statuses on ${sha.slice(0,7)}. Required checks and mergeability are not evaluated.`;
  if(runs.some((run:any)=>['failure','timed_out','cancelled','action_required','stale'].includes(run.conclusion))||contexts.some((item:any)=>['failure','error'].includes(item.state)))return {state:'failed',label:'Failed',detail};
  if(checks.more||status.more||checks.data.total_count>runs.length||status.data.total_count>contexts.length)return {state:'unknown',label:'Partial results',detail};
  if(!runs.length&&!contexts.length)return {state:'none',label:'No checks',detail};
  if(runs.some((run:any)=>['queued','in_progress','waiting','pending','requested'].includes(run.status))||contexts.some((item:any)=>item.state==='pending'))return {state:'pending',label:'Running',detail};
  if(runs.some((run:any)=>run.status!=='completed'||!['success','neutral','skipped'].includes(run.conclusion))||contexts.some((item:any)=>item.state!=='success'))return {state:'unknown',label:'Unknown',detail};
  if(!contexts.length&&runs.every((run:any)=>['neutral','skipped'].includes(run.conclusion)))return {state:'skipped',label:'Skipped',detail};
  return {state:'passed',label:'Passed',detail};
}
