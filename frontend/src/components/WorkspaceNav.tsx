import {useState} from 'react';
import {BookOpen,ChevronDown,FolderGit2,Inbox,LayoutGrid,Sparkles} from 'lucide-react';

type Props = {
  page: string;
  repoId: string;
  repositories: {id:string;label:string}[];
  jobCount: number;
  investigationCount: number;
  onNavigate: (page:'Dashboard'|'Projects'|'All Jobs'|'All Investigations')=>void;
  onRepository: (id:string)=>void;
};

export default function WorkspaceNav({page,repoId,repositories,jobCount,investigationCount,onNavigate,onRepository}:Props) {
  const [expanded,setExpanded]=useState(false);
  return <nav className="workspace-nav" aria-label="Workspace">
    <button aria-label="Dashboard" title="Dashboard" aria-current={page==='Dashboard'?'page':undefined} className={page==='Dashboard'?'selected':''} onClick={()=>{setExpanded(false);onNavigate('Dashboard')}}>
      <LayoutGrid size={19}/><span className="nav-label">Dashboard</span>
    </button>
    <div className={'projects-nav-row '+(page==='Projects'?'selected':'')}><button aria-label="Projects" title="Projects" aria-current={page==='Projects'?'page':undefined} onClick={()=>{setExpanded(false);onNavigate('Projects')}}><BookOpen size={19}/><span className="nav-label">Projects</span><span className="repository-count">{repositories.length}</span></button><button className="projects-expand" aria-label="Toggle projects" title="Show project shortcuts" aria-expanded={expanded} aria-controls="workspace-repositories" onClick={()=>setExpanded(!expanded)}><ChevronDown className={expanded?'repository-chevron expanded':'repository-chevron'} size={15}/></button></div>
    {expanded&&<div id="workspace-repositories" className="repository-links">
      {repositories.length?repositories.map(repo=><button key={repo.id} title={repo.label} aria-current={repo.id===repoId&&page!=='Settings'?'page':undefined} onClick={()=>{setExpanded(false);onRepository(repo.id)}}><FolderGit2 size={16}/><span>{repo.label}</span></button>):<p>No projects connected.</p>}
    </div>}
    <button aria-label="All Jobs" title="All Jobs" aria-current={page==='All Jobs'?'page':undefined} className={page==='All Jobs'?'selected':''} onClick={()=>{setExpanded(false);onNavigate('All Jobs')}}>
      <Inbox size={19}/><span className="nav-label">All Jobs</span><span className="workspace-count">{jobCount}</span>
    </button>
    <button aria-label="All Investigations" title="All Investigations" aria-current={page==='All Investigations'?'page':undefined} className={page==='All Investigations'?'selected':''} onClick={()=>{setExpanded(false);onNavigate('All Investigations')}}>
      <Sparkles size={19}/><span className="nav-label">All Investigations</span><span className="workspace-count">{investigationCount}</span>
    </button>
  </nav>;
}
