import {useEffect,useState} from 'react';
import {api,type Row} from '../api';

export default function ModelReviews({projectId,pr}:{projectId:string;pr?:{number:number;sha:string}}){
 const [rows,setRows]=useState<Row[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let active=true;const controller=new AbortController();
  async function load(){try{const result=await api('/repositories/'+projectId+'/model-reviews','GET',undefined,controller.signal);if(active){setRows(result);setError('')}}catch(e){if(active)setError(String(e))}}
  load();const timer=setInterval(load,3000);return()=>{active=false;controller.abort();clearInterval(timer)};
 },[projectId]);
 async function run(path:string,body?:unknown){setBusy(true);setError('');try{await api(path,'POST',body);setRows(await api('/repositories/'+projectId+'/model-reviews'))}catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="panel"><h2>Model reviews</h2>
  <p>Read-only, cited model assessments. Findings are hypotheses; these reviews do not execute tests, approve PRs or establish security completeness.</p>
  <div className="actions"><button disabled={busy} onClick={()=>run('/repositories/'+projectId+'/security-review')}>Review source security</button>
   {pr&&<button disabled={busy} onClick={()=>run('/repositories/'+projectId+'/pr-review',{number:pr.number,head:pr.sha})}>Review PR #{pr.number}</button>}</div>
  {error&&<p role="alert">{error}</p>}
  {!rows.length&&<p>No model reviews yet. Select a PR in Pull requests or review this repository’s source security.</p>}
  {[...rows].reverse().map(row=><article className="note" key={row.id}>
   <h3>{row.payload.kind==='pr'?'PR #'+row.payload.number:'Source security'} · {row.status}</h3>
   <p>Snapshot <code>{row.payload.snapshot_id}</code></p>
   {row.error&&<p role="alert">{row.error}</p>}
   {['queued','running'].includes(row.status)&&<button disabled={busy} onClick={()=>run('/operations/'+row.id+'/cancel')}>Cancel review</button>}
   {['failed','cancelled'].includes(row.status)&&<button disabled={busy} onClick={()=>run('/operations/'+row.id+'/retry')}>Retry review</button>}
   {row.result&&<><p className="prose">{row.result.summary}</p>
    <p>Models: {[...new Set(row.result.provider.map((p:Row)=>p.model))].join(', ')} · No tests executed</p>
    {row.result.pr&&<p><a href={row.result.pr.url} target="_blank" rel="noreferrer">GitHub PR</a> · Reviewed head <code>{row.result.pr.head}</code></p>}
    {!row.result.findings.length&&<p>No concerns identified in the supplied evidence. This is not proof of correctness or safety.</p>}
    {row.result.findings.map((finding:Row,i:number)=><details key={i}><summary>{finding.severity} · hypothesis · {finding.message}</summary>
     <p>Verify: {finding.verification}</p><p>{finding.reference.path}:{finding.reference.start_line}–{finding.reference.end_line}</p>
     {finding.evidence.source_url&&<a href={finding.evidence.source_url+'#L'+finding.reference.start_line} target="_blank" rel="noreferrer">View exact source</a>}
     <pre>{finding.evidence.content.split('\n').slice(finding.reference.start_line-finding.evidence.start_line,finding.reference.end_line-finding.evidence.start_line+1).join('\n')}</pre>
    </details>)}
    <details><summary>Coverage and limitations</summary><ul>{row.result.limitations.map((text:string,i:number)=><li key={i}>{text}</li>)}</ul><p>Sources inspected: {row.result.context.items.map((item:Row)=>item.path).join(', ')}</p></details>
   </>}
  </article>)}
 </section>;
}
