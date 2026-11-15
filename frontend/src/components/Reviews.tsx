import {useEffect,useState,type ReactNode} from 'react';
import {GitBranch,GitCommitHorizontal,GitPullRequest,RefreshCw} from 'lucide-react';
import {commitCI,githubRepository,pullRequests,type CI,type PullRequest} from '../github';
import {demoPulls,demoCI} from '../demo';
import AppliedChanges from './AppliedChanges';
import ModelReviews from './ModelReviews';
import type {Row} from '../api';

function CheckStatus({repository,sha,demo}:{repository:string;sha:string;demo?:CI}) {
  const [result,setResult]=useState<CI|null>(demo||null),[attempt,setAttempt]=useState(0);
  useEffect(()=>{
    if(demo){setResult(demo);return}
    const controller=new AbortController();setResult(null);
    commitCI(repository,sha,controller.signal).then(value=>{if(!controller.signal.aborted)setResult(value)}).catch(error=>{if(!controller.signal.aborted)setResult({state:'unknown',label:'Unavailable',detail:error.message})});
    return ()=>controller.abort();
  },[repository,sha,demo,attempt]);
  return <div className="pr-ci"><span className={'ci-badge '+(result?.state||'pending')} title={result?.detail||'Fetching CI for this head commit'}><i/>{result?.label||'Loading'}</span>{result?.label==='Unavailable'&&<button className="text-button" aria-label={'Retry CI '+sha.slice(0,7)} onClick={()=>setAttempt(value=>value+1)}>Retry</button>}</div>;
}

export default function Reviews({projectId,origin,demo,prepared,jobs,onOpen}:{projectId:string;origin:string;demo:boolean;prepared:ReactNode;jobs:Row[];onOpen:(job:Row)=>void}) {
  const [reviewPR,setReviewPR]=useState<{number:number;sha:string}|undefined>();
  const imported=githubRepository(origin);
  const storageKey='duck-tape:github:'+projectId;
  const [repository,setRepository]=useState(imported||githubRepository(localStorage.getItem(storageKey)||'')||'');
  const [input,setInput]=useState(''),[error,setError]=useState(''),[rows,setRows]=useState<PullRequest[]>([]),[loading,setLoading]=useState(false),[more,setMore]=useState(false),[page,setPage]=useState(1),[refresh,setRefresh]=useState(0),[updated,setUpdated]=useState('');
  const [tab,setTab]=useState(demo||repository?'Pull requests':'Agent changes');
  useEffect(()=>{
    if(demo||!repository||tab!=='Pull requests')return;
    const controller=new AbortController();setLoading(true);setError('');setRows([]);setMore(false);setUpdated('');
    pullRequests(repository,page,controller.signal).then(value=>{if(!controller.signal.aborted){setRows(value.rows);setMore(value.more);setUpdated(new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}}).catch(error=>{if(!controller.signal.aborted)setError(error.message)}).finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return ()=>controller.abort();
  },[demo,repository,tab,page,refresh]);
  const visible=demo?demoPulls:rows;
  return <>
    <div className="heading-line"><h1>Reviews</h1>{tab==='Pull requests'&&<button disabled={demo||!repository||loading} onClick={()=>setRefresh(value=>value+1)}><RefreshCw size={15}/>Refresh</button>}</div>
    <div className="tabs">{['Pull requests','Agent changes','Applied changes',...(!demo?['Model reviews']:[])].map(value=><button key={value} aria-current={tab===value?'page':undefined} className={tab===value?'active':''} onClick={()=>setTab(value)}>{value}</button>)}</div>
    {tab==='Model reviews'?<ModelReviews projectId={projectId} pr={reviewPR}/>:tab==='Applied changes'?<AppliedChanges projectId={projectId} jobs={jobs} onOpen={onOpen}/>:tab==='Agent changes'?<section className="panel no-pad">{prepared}</section>:<>
      {demo?<p className="pr-source"><span className="tag">Demo data</span> northstar/commerce-api</p>:repository?<div className="pr-source"><a href={'https://github.com/'+repository} target="_blank" rel="noreferrer">{repository}</a><span>GitHub · {updated?'Fetched '+updated:'Read-only'}</span>{!imported&&<button className="text-button" onClick={()=>{setRepository('');setRows([]);setError('');setInput('');localStorage.removeItem(storageKey)}}>Change project</button>}</div>:<form className="pr-connect panel" onSubmit={event=>{event.preventDefault();const selected=githubRepository(input);if(!selected){setError('Enter a public GitHub project URL: https://github.com/owner/repo');return}localStorage.setItem(storageKey,'https://github.com/'+selected);setPage(1);setError('');setRepository(selected)}}><label htmlFor="pr-repository">GitHub project</label><div className="input-row"><input id="pr-repository" type="url" required placeholder="https://github.com/owner/repo" value={input} onChange={event=>setInput(event.target.value)}/><button className="primary">Load pull requests</button></div><p>Public projects. Read-only; no token required.</p></form>}
      {error&&<div role="alert" className="alert error">{error}{repository&&<button onClick={()=>setRefresh(value=>value+1)}>Retry</button>}</div>}
      {loading?<p role="status">Loading pull requests…</p>:(demo||repository)&&!error&&<div className="pr-list">
        <div className="pr-columns" aria-hidden="true"><span>Pull request</span><span>Branch</span><span>Commit</span><span>Head CI</span></div>
        {visible.map((pr,index)=><article className="pr-row" key={pr.number} aria-label={'Pull request '+pr.number}>
          <div className="pr-title"><GitPullRequest size={18} className={pr.draft?'muted':'pr-open'}/><div>{demo?<strong>{pr.title}</strong>:<a href={`https://github.com/${repository}/pull/${pr.number}`} target="_blank" rel="noreferrer">{pr.title}</a>}<small>#{pr.number} · {pr.author}{pr.draft&&<span className="tag">Draft</span>}</small></div></div>
          <div className="pr-branch" title={pr.head+' → '+pr.base}><GitBranch size={14}/><span><code>{pr.head}</code><small>→ {pr.base}</small></span></div>
          <div className="pr-commit"><GitCommitHorizontal size={14}/>{demo?<code>{pr.sha.slice(0,7)}</code>:<a title={pr.sha} href={`https://github.com/${repository}/commit/${pr.sha}`} target="_blank" rel="noreferrer"><code>{pr.sha.slice(0,7)}</code></a>}</div>
          {!demo&&imported&&<button onClick={()=>{setReviewPR(pr);setTab('Model reviews')}}>AI review #{pr.number}</button>}
          <CheckStatus key={pr.sha+refresh} repository={repository} sha={pr.sha} demo={demo?demoCI[index]:undefined}/>
        </article>)}
        {!visible.length&&<div className="empty"><h3>No open pull requests</h3></div>}
      </div>}
      {!demo&&repository&&!error&&!loading&&<div className="pr-pagination"><button disabled={page===1} onClick={()=>setPage(value=>value-1)}>Previous</button><span>Page {page}</span><button disabled={!more} onClick={()=>setPage(value=>value+1)}>Next</button></div>}
    </>}
  </>;
}
