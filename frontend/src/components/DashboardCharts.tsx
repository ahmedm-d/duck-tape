import {useState} from 'react';
import {arrival,type JobSummary} from './JobList';

const statusColours:Record<string,string>={'Proposed':'#8b949e','Awaiting approval':'#d29922','Queued':'#79c0ff','Investigating':'#58a6ff','Needs input':'#db6d28','Implementing':'#a5a3ff','Verifying':'#d2a8ff','Ready for review':'#3fb950','Failed':'#f85149','Cancelled':'#6e7681','Rejected':'#9da7b3','Completed':'#56d4c0'};
export function receivedWithin(job:JobSummary,period:string) {
  if(period==='all')return true;
  const start=new Date();start.setHours(0,0,0,0);start.setDate(start.getDate()-Number(period)+1);
  const time=arrival(job.created_at);return time!==null&&time>=+start&&time<=Date.now();
}
export default function DashboardCharts({jobs,onStatus}:{jobs:JobSummary[];onStatus:(status:string,period:string)=>void}) {
  const [period,setPeriod]=useState('all');
  const visible=jobs.filter(job=>receivedWithin(job,period));
  const counts=new Map<string,number>();visible.forEach(job=>counts.set(job.status,(counts.get(job.status)||0)+1));
  let offset=0;
  const today=new Date();today.setHours(0,0,0,0);
  const days=Array.from({length:7},(_,index)=>{const start=new Date(today);start.setDate(start.getDate()-6+index);const end=new Date(start);end.setDate(end.getDate()+1);return {start,count:jobs.filter(job=>{const time=arrival(job.created_at);return time!==null&&time>=+start&&time<+end&&time<=Date.now()}).length}});
  const maximum=Math.max(1,...days.map(day=>day.count)),received=days.reduce((sum,day)=>sum+day.count,0),unknown=jobs.filter(job=>arrival(job.created_at)===null).length;
  return <div className="dashboard-charts">
    <section className="panel dashboard-chart" aria-label="Jobs by status"><div className="section-head"><h2>Jobs by status</h2><select aria-label="Status timeline" value={period} onChange={event=>setPeriod(event.target.value)}><option value="all">All time</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></div>
      <p className="chart-period">{visible.length} {visible.length===1?'job':'jobs'} · Current status{period!=='all'&&' of jobs received in this period'}</p>{visible.length?<div className="status-chart"><svg viewBox="0 0 120 120" role="img" aria-label={`${visible.length} jobs by current status; counts in the legend`}><circle cx="60" cy="60" r="44" fill="none" stroke="var(--border)" strokeWidth="13"/>{[...counts].map(([status,count])=>{const length=count/visible.length*100,start=offset;offset+=length;return <circle key={status} cx="60" cy="60" r="44" fill="none" stroke={statusColours[status]||'#8b949e'} strokeWidth="13" pathLength="100" strokeDasharray={`${length} ${100-length}`} strokeDashoffset={-start} transform="rotate(-90 60 60)"><title>{status}: {count}</title></circle>})}<text x="60" y="60" textAnchor="middle" className="chart-total">{visible.length}</text><text x="60" y="76" textAnchor="middle" className="chart-caption">jobs</text></svg><div className="chart-legend">{[...counts].map(([status,count])=><button key={status} aria-label={`${status}: ${count}`} onClick={()=>onStatus(status,period)}><i style={{background:statusColours[status]||'#8b949e'}}/><span>{status}</span><strong>{count}</strong></button>)}</div></div>:<p className="chart-empty">No jobs in this period</p>}
    </section>
    <section className="panel dashboard-chart" aria-label="New jobs · last 7 days"><div className="section-head"><h2>New jobs <small>Last 7 days</small></h2><span className="muted">{received} received</span></div><div className="arrival-chart" role="img" aria-label={days.map(day=>`${day.start.toLocaleDateString([],{month:'short',day:'numeric'})}: ${day.count} jobs`).join('; ')}>{days.map(day=><div key={+day.start} className="arrival-day"><div className="arrival-track"><span>{day.count}</span><i style={{height:(day.count/maximum*90)+'px'}}/></div><small>{day.start.toLocaleDateString([],{month:'short',day:'numeric'})}</small></div>)}</div><p className="chart-footnote">Local time{unknown>0&&` · ${unknown} ${unknown===1?'job':'jobs'} without an arrival time`}</p></section>
  </div>;
}
