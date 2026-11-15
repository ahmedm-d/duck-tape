import {Activity,ArrowLeft,BookOpen,CheckCheck,Code2,FolderGit2,LayoutDashboard,Inbox,Sparkles} from 'lucide-react';

export type ProjectPage = 'Overview'|'Jobs'|'Investigations'|'Research'|'Reviews'|'Repository'|'Knowledge'|'Activity';
type Props = {
  page: string;
  projectId: string;
  projects: {id:string;label:string}[];
  jobs: {status:string;source:string}[];
  onWorkspace: ()=>void;
  onProject: (id:string)=>void;
  onNavigate: (page:ProjectPage)=>void;
};

export default function ProjectNav({page,projectId,projects,jobs,onWorkspace,onProject,onNavigate}:Props) {
  const investigations=jobs.filter(job=>job.status==='Investigating').length;
  return <nav className="workspace-nav project-nav" aria-label="Project">
    <button title="Back to workspace" aria-label="Back to workspace" onClick={onWorkspace}><ArrowLeft size={18}/><span className="nav-label">All projects</span></button>
    <label className="project-switcher" title="Switch project"><FolderGit2 size={18}/><select aria-label="Switch project" value={projectId} onChange={event=>onProject(event.target.value)}>{projects.map(project=><option key={project.id} value={project.id}>{project.label}</option>)}</select></label>
    <button title="Overview" aria-label="Project overview" aria-current={page==='Overview'?'page':undefined} className={page==='Overview'?'selected':''} onClick={()=>onNavigate('Overview')}><LayoutDashboard size={19}/><span className="nav-label">Overview</span></button>
    <button title="Jobs" aria-label="Project jobs" aria-current={page==='Jobs'?'page':undefined} className={page==='Jobs'?'selected':''} onClick={()=>onNavigate('Jobs')}><Inbox size={19}/><span className="nav-label">Jobs</span><span className="workspace-count">{jobs.length}</span></button>
    <button title="Investigations" aria-label="Project investigations" aria-current={['Investigations','Research'].includes(page)?'page':undefined} className={['Investigations','Research'].includes(page)?'selected':''} onClick={()=>onNavigate('Investigations')}><Sparkles size={19}/><span className="nav-label">Investigations</span><span className="workspace-count">{investigations}</span></button>
    <button title="Reviews" aria-label="Project reviews" aria-current={page==='Reviews'?'page':undefined} className={page==='Reviews'?'selected':''} onClick={()=>onNavigate('Reviews')}><CheckCheck size={19}/><span className="nav-label">Reviews</span></button>
    {([{page:'Repository',label:'Codebase',Icon:Code2},{page:'Knowledge',label:'Knowledge',Icon:BookOpen},{page:'Activity',label:'Activity',Icon:Activity}] as const).map(item=><button key={item.page} title={item.label} aria-label={'Project '+item.label.toLowerCase()} aria-current={page===item.page?'page':undefined} className={page===item.page?'selected':''} onClick={()=>onNavigate(item.page)}><item.Icon size={19}/><span className="nav-label">{item.label}</span></button>)}
  </nav>;
}
