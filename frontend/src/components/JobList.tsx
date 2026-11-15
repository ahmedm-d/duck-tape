import {ChevronRight,GitBranch,ArrowUp,ArrowDown,Minus,Search,LayoutGrid,List,Clock,CheckCircle2} from 'lucide-react';

export type JobSummary = {id:string;repo_id:string;title:string;status:string;source?:string;type?:string;priority?:string;risk?:string;description?:string;affected_areas?:string[];created_at?:string};
export type JobSort = 'newest'|'oldest'|'priority'|'status';
export const label=(value?:string)=>value?value[0].toUpperCase()+value.slice(1):'Unknown';
export function arrival(value?:string){const time=value?Date.parse(value):NaN;return Number.isFinite(time)?time:null}
export function Priority({value}:{value?:string}) {
  const Icon=value==='high'?ArrowUp:value==='low'?ArrowDown:Minus;
  return <span className={'tag priority priority-'+(['high','medium','low'].includes(value||'')?value:'unknown')}><Icon size={13} aria-hidden="true"/>Priority: {label(value)}</span>;
}
export function JobStatus({value}:{value:string}) {
 const tone=['Ready for review','Completed'].includes(value)?'good':value==='Failed'?'bad':['Needs input','Awaiting approval'].includes(value)?'warm':'';
 const meanings:Record<string,string>={'Awaiting approval':'Approve the plan before the agent prepares changes.','Ready for review':'Changes are prepared; review the diff and checks.','Needs input':'The agent needs a decision to continue.'};
 const Icon=tone==='good'?CheckCircle2:Clock;
 return <span className={'status job-status '+tone} title={meanings[value]||value}><Icon size={14} aria-hidden="true"/>{value}</span>;
}
export function ViewSwitch({value,onChange}:{value:'list'|'tiles';onChange:(value:'list'|'tiles')=>void}) {
 return <div className="view-switch" role="group" aria-label="Job view"><button aria-label="List view" title="List view" aria-pressed={value==='list'} onClick={()=>onChange('list')}><List size={17}/></button><button aria-label="Tile view" title="Tile view" aria-pressed={value==='tiles'} onClick={()=>onChange('tiles')}><LayoutGrid size={17}/></button></div>;
}
export function sortJobs(jobs:JobSummary[],sort:JobSort) {
  return [...jobs].sort((a,b)=>{
    if(sort==='priority'){
      const order:Record<string,number>={high:0,medium:1,low:2};
      const difference=(order[a.priority||'']??3)-(order[b.priority||'']??3);
      if(difference)return difference;
    }
    if(sort==='status'){const difference=a.status.localeCompare(b.status);if(difference)return difference}
    const left=arrival(a.created_at),right=arrival(b.created_at);
    if(left===null||right===null)return left===right?a.id.localeCompare(b.id):left===null?1:-1;
    return (sort==='oldest'?left-right:right-left)||a.id.localeCompare(b.id);
  });
}
export default function JobList({jobs,view='list',sort='newest',projectLabel,onOpen,onInvestigation}:{jobs:JobSummary[];view?:'tiles'|'list';sort?:JobSort;projectLabel?:(id:string)=>string;onOpen:(job:JobSummary)=>void;onInvestigation:(job:JobSummary)=>void}) {
  if(!jobs.length)return <div className="empty"><h3>Nothing in this queue</h3><p>Try another filter or create a request in a project.</p></div>;
  return <div className={'job-list job-'+view}>{sortJobs(jobs,sort).map(job=>{
    const time=arrival(job.created_at),areas=job.affected_areas||[];
    return <article className="job-card" key={job.id}><button className="job-row" onClick={()=>onOpen(job)}>
      <span className="job-glyph"><GitBranch size={18}/></span>
      <span className="job-title">
        {projectLabel&&<small className="job-project">{projectLabel(job.repo_id)}</small>}
        <strong>{job.title}</strong>
        <span className="job-description">{job.description|| (areas.length?areas.slice(0,2).join(', ')+(areas.length>2?` +${areas.length-2} more`:''):'File scope not recorded')}</span>
        <span className="job-labels"><Priority value={job.priority}/><span className="tag job-source">Source: {label(job.source)}</span><span className="tag job-type">Type: {label(job.type)}</span></span>
        <span className="job-metadata"><span>Risk: {label(job.risk||'unassessed')}</span>{areas.length>0&&<span>{areas.length} affected {areas.length===1?'file':'files'}</span>}{time!==null?<time dateTime={job.created_at} title={new Date(time).toLocaleString()}>Received {new Date(time).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</time>:<span>Arrival time unavailable</span>}</span>
      </span>
      <JobStatus value={job.status}/><ChevronRight size={16}/>
    </button><div className="job-card-footer"><button className="text-button" onClick={()=>onInvestigation(job)}><Search size={14}/>Investigation</button><span>Priority, risk &amp; source</span></div></article>;
  })}</div>;
}
