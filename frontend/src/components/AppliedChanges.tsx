import {useEffect,useState} from 'react';
import {api,type Row} from '../api';
import DiffViewer from './DiffViewer';

export default function AppliedChanges({projectId,jobs,onOpen}:{projectId:string;jobs:Row[];onOpen:(job:Row)=>void}) {
  const applied=jobs.filter(job=>job.repo_id===projectId&&job.status==='Completed')
    .sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')));
  const [selected,setSelected]=useState(''),[detail,setDetail]=useState<Row|null>(null);
  const [error,setError]=useState(''),[attempt,setAttempt]=useState(0),[split,setSplit]=useState(false);
  const id=applied.some(job=>job.id===selected)?selected:applied[0]?.id;
  useEffect(()=>{
    setDetail(null);setError('');
    if(!id)return;
    const controller=new AbortController();
    api('/jobs/'+encodeURIComponent(id),'GET',undefined,controller.signal).then(job=>{
      if(controller.signal.aborted)return;
      if(job.repo_id!==projectId||job.status!=='Completed')throw new Error('This applied change is no longer available in this project.');
      setDetail(job);
    }).catch(error=>{if(!controller.signal.aborted)setError(error.message)});
    return()=>controller.abort();
  },[id,projectId,attempt]);
  const current=detail&&detail.id===id&&detail.repo_id===projectId?detail:null;
  const revision=current?.revisions?.find((item:Row)=>item.id===current.approval?.revision_id);
  return <section aria-label="Applied change history">
    <h2>Applied changes</h2>
    <p>All completed changes in this project. These patches were applied to managed local branches; GitHub publication and merging are separate.</p>
    {!applied.length?<div className="empty"><h3>No applied changes yet</h3><p>Changes appear here after successful application, not just approval.</p></div>:<>
      <div className="panel"><label>Applied change<select aria-label="Applied change" value={id} onChange={event=>setSelected(event.target.value)}>
        {applied.map(job=><option key={job.id} value={job.id}>{job.title}</option>)}
      </select></label><p>{applied.length} completed {applied.length===1?'change':'changes'}</p></div>
      {error?<div role="alert">{error}<button onClick={()=>setAttempt(value=>value+1)}>Retry loading change</button></div>:!current?<p role="status">Loading applied revision...</p>:<>
        <div className="section-head"><h3>{current.title}</h3><button onClick={()=>onOpen(current)}>Open job and checks</button></div>
        {!revision?<p role="alert">The approved revision is unavailable. Open the job to inspect its evidence.</p>:<>
          <div className="toolbar"><span className="tag">Applied revision {revision.number}</span><code>{current.managed_target_branch||'steward/integration'}</code>
            <button onClick={()=>setSplit(value=>!value)}>{split?'Unified view':'Split view'}</button>
            <a className="button" href={`/api/jobs/${encodeURIComponent(current.id)}/patch?revision_id=${encodeURIComponent(revision.id)}`}>Export applied patch</a>
          </div>
          <p>{typeof revision.explanation==='string'?revision.explanation:'See the job for its recorded explanation.'}</p>
          <DiffViewer key={revision.id} files={revision.files||[]} diff={revision.diff||''} split={split}/>
        </>}
      </>}
    </>}
  </section>;
}

