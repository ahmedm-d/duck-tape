import {useState} from 'react';
import DiffViewer from './DiffViewer';

type Run = {id:string;candidate_diff?:string;candidate_files?:any[];candidate_patch_hash?:string;base_snapshot?:string;status?:string};

export default function CandidatePreview({runs}:{runs:Run[]}){
 const [split,setSplit]=useState(false);
 const [selected,setSelected]=useState('');
 const candidates=runs.filter(run=>run.candidate_diff);
 const run=candidates.find(item=>item.id===selected)||candidates.at(-1);
 if(!run)return null;
 const download=()=>{
  const url=URL.createObjectURL(new Blob([run.candidate_diff!],{type:'text/x-diff'}));
  const link=document.createElement('a');link.href=url;link.download='duck-tape-unverified-'+run.id.replace(/[^a-zA-Z0-9_-]/g,'').slice(0,16)+'.patch';
  link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 return <>
  <section className="panel"><h2>Unverified candidate</h2><p>This captured patch has not completed verification and review. It cannot be approved or applied through Duck Tape. Export preserves the exact diff for manual inspection.</p><p className="muted">Base {run.base_snapshot} · Patch {run.candidate_patch_hash||'See captured run'}</p></section>
  <div className="toolbar"><select aria-label="Candidate run" value={run.id} onChange={event=>setSelected(event.target.value)}>{candidates.map(item=><option key={item.id} value={item.id}>Run {item.id.slice(0,8)} · {item.status}</option>)}</select><div className="spacer"/><button onClick={()=>setSplit(!split)}>{split?'Unified view':'Split view'}</button><button onClick={download}>Export unverified patch</button></div>
  <DiffViewer files={run.candidate_files||[]} diff={run.candidate_diff!} split={split}/>
 </>;
}
