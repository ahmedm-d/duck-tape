import type {Row} from '../api';
import DashboardCharts from './DashboardCharts';
import {type JobSummary} from './JobList';
import type {ProjectPage} from './ProjectNav';

export default function ProjectOverview({project,jobs,events,onNavigate,onStatus,onRequest}:{project:Row;jobs:JobSummary[];events:Row[];onNavigate:(page:ProjectPage)=>void;onStatus:(status:string,period:string)=>void;onRequest:()=>void}) {
 const files=(project.index?.files||[]).filter((file:Row)=>file.status==='indexed'),attention=jobs.filter(job=>['Proposed','Awaiting approval','Needs input','Failed','Ready for review'].includes(job.status));
 return <><div className="heading-line"><div><h1>Overview</h1><p>{project.name}</p></div><button className="primary" onClick={onRequest}>New request</button></div>
  <div className="overview-facts"><button onClick={()=>onNavigate('Jobs')}><strong>{jobs.length}</strong><span>Jobs</span></button><button onClick={()=>onStatus('attention','all')}><strong>{attention.length}</strong><span>Need attention</span></button><button onClick={()=>onNavigate('Repository')}><strong>{files.length}</strong><span>Indexed files</span></button><button onClick={()=>onNavigate('Repository')}><strong>{files.filter((file:Row)=>/(^|\/)(__tests__|tests?)\/|[._](test|spec)\./i.test(file.path)).length}</strong><span>Test files</span></button></div>
  <DashboardCharts jobs={jobs} onStatus={onStatus}/>
  <div className="two-col"><section className="panel"><div className="section-head"><h2>Codebase</h2><button className="text-button" onClick={()=>onNavigate('Repository')}>Explore code</button></div><p className="overview-summary">{project.index?.overview||'No codebase summary recorded.'}</p><dl className="overview-details"><div><dt>Branch</dt><dd>{project.branch||project.origin?.branch||'Not recorded'}</dd></div><div><dt>Commit</dt><dd><code>{(project.commit||project.origin?.commit)?.slice(0,12)||'Not recorded'}</code></dd></div><div><dt>Snapshot</dt><dd><code>{project.snapshot?.slice(0,12)||'Not indexed'}</code></dd></div></dl>{(project.index?.gaps||[]).length>0&&<details><summary>{project.index.gaps.length} coverage gaps</summary><ul>{project.index.gaps.map((gap:string,index:number)=><li key={index}>{gap}</li>)}</ul></details>}</section>
  <section className="panel"><div className="section-head"><h2>Recent activity</h2><button className="text-button" onClick={()=>onNavigate('Activity')}>View activity</button></div>{events.length?events.slice(0,5).map(event=><div className="overview-event" key={event.id}><span>{event.message}</span><time>{event.created_at?new Date(event.created_at).toLocaleString():''}</time></div>):<p>No activity recorded yet.</p>}</section></div>
 </>;
}
