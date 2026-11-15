import type {Row} from '../api';
import {Priority,label} from './JobList';

export default function Investigation({job,onSource}:{job:Row;onSource:(path:string,start?:number,end?:number,snapshot?:string)=>void}) {
 const run=[...(job.runs||[])].reverse().find((run:Row)=>run.investigation);
 const investigation=job.investigation||run?.investigation;
 const priorityMeaning:Record<string,string>={high:'Urgent work with significant user or operational impact.',medium:'Important work that can follow urgent fixes.',low:'Lower urgency; schedule alongside routine maintenance.'};
 const riskMeaning:Record<string,string>={high:'Broad or sensitive change; review the impact and verification carefully.',medium:'Some behavioural impact; targeted checks and review are needed.',low:'A narrow change with a smaller expected impact.'};
 return <section className="investigation-detail" aria-label="Job investigation"><div className="section-head"><h2>Investigation</h2><span className="tag">{investigation?'Recorded assessment':'Assessment pending'}</span></div>
  <div className="two-col"><section className="panel"><h3>Why this priority?</h3><Priority value={job.priority}/><p className="assessment-meaning">{priorityMeaning[job.priority]||'Priority has not been assigned.'}</p><p>{job.priority_reason||'No priority rationale recorded.'}</p></section><section className="panel"><h3>Change risk: {label(job.risk||'unassessed')}</h3><p className="assessment-meaning">{riskMeaning[job.risk]||'Risk has not been assessed.'}</p><p>{job.risk_reason||'No risk rationale recorded.'}</p></section></div>
  <section className="panel"><h3>Findings</h3><p className="prose">{investigation?.summary||'No agent investigation has been recorded yet.'}</p>{job.rationale&&<><h3>Intake rationale</h3><p>{job.rationale}</p></>}{(investigation?.missing||[]).length>0&&<><h3>Evidence gaps</h3><ul>{investigation.missing.map((item:string,index:number)=><li key={index}>{item}</li>)}</ul></>}
   <h3>Source evidence</h3>{(investigation?.evidence||[]).map((item:Row,index:number)=><button className="evidence" key={index} onClick={()=>onSource(item.path,item.start_line,item.end_line,job.investigation?job.base_snapshot:run?.base_snapshot||job.base_snapshot)}>{item.path}:{item.start_line}{item.end_line!==item.start_line?'–'+item.end_line:''}</button>)}{!investigation?.evidence?.length&&<p>No source citations recorded.</p>}
   {typeof job.source_url==='string'&&/^https?:\/\//i.test(job.source_url)&&<a href={job.source_url} target="_blank" rel="noreferrer">Original source</a>}
   <details><summary>Original request</summary><p className="prose">{job.description||job.title}</p></details>
  </section>
 </section>;
}
