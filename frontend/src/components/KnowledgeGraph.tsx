import {useMemo,useRef,useState} from 'react';
import {ArrowUpRight,Minus,Plus,RotateCcw,Search} from 'lucide-react';

type SourceFile = {path:string;status?:string;symbols?:{name:string}[]};
type Edge = {source:string;target:string;kind:string;confidence?:string;line?:number};
type Props = {files:SourceFile[];edges:Edge[];snapshot:string;gaps:string[];onSource:(path:string,line?:number)=>void};
const colours = ['var(--accent)','#91b8ee','#c4a5ee','#dcb778','#86c5ba','#ce99ae'];

function layout(paths:string[],edges:Edge[]) {
  const nodes=paths.map((path,i)=>({path,x:450+Math.cos(i*2.39996)*Math.sqrt(i+1)*22,y:280+Math.sin(i*2.39996)*Math.sqrt(i+1)*18}));
  const lookup=new Map(nodes.map((node,i)=>[node.path,i]));
  const links=edges.map(edge=>[lookup.get(edge.source)!,lookup.get(edge.target)!]);
  // shortcut: cap the visible graph at 250 files; move layout to a worker for larger simultaneous views.
  for(let step=0;step<90;step++) {
    const forces=nodes.map(()=>({x:0,y:0}));
    for(let a=0;a<nodes.length;a++)for(let b=a+1;b<nodes.length;b++) {
      const dx=nodes[a].x-nodes[b].x,dy=nodes[a].y-nodes[b].y,d2=Math.max(100,dx*dx+dy*dy),force=250/d2;
      forces[a].x+=dx*force;forces[a].y+=dy*force;forces[b].x-=dx*force;forces[b].y-=dy*force;
    }
    for(const [a,b] of links) {
      const dx=nodes[b].x-nodes[a].x,dy=nodes[b].y-nodes[a].y,d=Math.max(1,Math.hypot(dx,dy)),force=(d-95)*.015;
      forces[a].x+=dx/d*force;forces[a].y+=dy/d*force;forces[b].x-=dx/d*force;forces[b].y-=dy/d*force;
    }
    nodes.forEach((node,i)=>{node.x=Math.max(45,Math.min(855,node.x+Math.max(-9,Math.min(9,forces[i].x+(450-node.x)*.012))));node.y=Math.max(40,Math.min(520,node.y+Math.max(-9,Math.min(9,forces[i].y+(280-node.y)*.012))))});
  }
  return nodes;
}

export default function KnowledgeGraph({files,edges,snapshot,gaps,onSource}:Props) {
  const [query,setQuery]=useState(''),[kind,setKind]=useState(''),[selected,setSelected]=useState('');
  const [view,setView]=useState({x:0,y:0,scale:1});
  const drag=useRef<{x:number;y:number;originX:number;originY:number}|null>(null);
  const graph=useMemo(()=>{
    const paths=[...new Set(files.filter(file=>file.status==='indexed').map(file=>file.path))].sort();
    const available=new Set(paths);
    const relationships=edges.filter(edge=>available.has(edge.source)&&available.has(edge.target)&&(!kind||edge.kind===kind));
    const matches=paths.filter(path=>path.toLowerCase().includes(query.toLowerCase()));
    const matched=new Set(matches);
    const nearby=new Set(relationships.filter(edge=>matched.has(edge.source)||matched.has(edge.target)).flatMap(edge=>[edge.source,edge.target]));
    const candidates=query?[...matches,...paths.filter(path=>nearby.has(path)&&!matched.has(path))]:paths;
    const visible=candidates.slice(0,250),visibleSet=new Set(visible);
    const links=relationships.filter(edge=>visibleSet.has(edge.source)&&visibleSet.has(edge.target));
    const unique=[...new Map(links.map(edge=>[[edge.source,edge.target].sort().join('\0'),edge])).values()];
    return {nodes:layout(visible,unique),links,matches:new Set(matches),total:paths.length,omitted:Math.max(0,candidates.length-250)};
  },[files,edges,query,kind]);
  const positions=new Map(graph.nodes.map(node=>[node.path,node]));
  const active=positions.has(selected)?selected:'';
  const connections=graph.links.filter(edge=>edge.source===active||edge.target===active);
  const neighbours=new Set(connections.flatMap(edge=>[edge.source,edge.target]));
  const groups=[...new Set(graph.nodes.map(node=>node.path.includes('/')?node.path.split('/')[0]:'root'))];
  const colour=(path:string)=>colours[groups.indexOf(path.includes('/')?path.split('/')[0]:'root')%colours.length];
  const zoom=(amount:number)=>setView(value=>({...value,scale:Math.max(.5,Math.min(3,value.scale+amount))}));
  return <section className="knowledge-map" aria-label="Codebase knowledge map">
    <div className="graph-toolbar"><label className="search"><Search size={15}/><input aria-label="Find graph file" placeholder="Find a file or folder…" value={query} onChange={event=>{setQuery(event.target.value);setView({x:0,y:0,scale:1})}}/></label><select aria-label="Graph relationship" value={kind} onChange={event=>setKind(event.target.value)}><option value="">All relationships</option>{[...new Set(edges.map(edge=>edge.kind))].sort().map(value=><option key={value}>{value}</option>)}</select><span>{graph.nodes.length} files · {graph.links.length} relationships</span></div>
    <div className="graph-body"><div className="graph-canvas">
      {graph.nodes.length?<svg viewBox="0 0 900 560" aria-label="Project file graph" onPointerDown={event=>{if((event.target as Element).closest('[role=button]'))return;const rect=event.currentTarget.getBoundingClientRect();drag.current={x:event.clientX,y:event.clientY,originX:view.x,originY:view.y};event.currentTarget.setPointerCapture(event.pointerId);event.currentTarget.dataset.ratio=String(900/rect.width)}} onPointerMove={event=>{if(drag.current){const ratio=Number(event.currentTarget.dataset.ratio);setView(value=>({...value,x:drag.current!.originX+(event.clientX-drag.current!.x)*ratio,y:drag.current!.originY+(event.clientY-drag.current!.y)*ratio}))}}} onPointerUp={()=>{drag.current=null}} onPointerCancel={()=>{drag.current=null}}>
        <g transform={`translate(${450+view.x} ${280+view.y}) scale(${view.scale}) translate(-450 -280)`}>
          {graph.links.map((edge,i)=>{const a=positions.get(edge.source)!,b=positions.get(edge.target)!;return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={active&&(edge.source===active||edge.target===active)?'graph-edge highlighted':'graph-edge'} opacity={active&&!neighbours.has(edge.source)?.15:1}><title>{edge.source} → {edge.target}: {edge.kind} ({edge.confidence||'confidence not recorded'})</title></line>})}
          {graph.nodes.map(node=><g key={node.path} role="button" tabIndex={0} aria-label={'Inspect '+node.path} aria-pressed={active===node.path} className="graph-node" transform={`translate(${node.x} ${node.y})`} opacity={active&&!neighbours.has(node.path)?.22:query&&!graph.matches.has(node.path)?.4:1} onClick={()=>setSelected(node.path)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setSelected(node.path)}}}>
            <title>{node.path}</title><circle r="15" fill="transparent"/><circle r={active===node.path?8:5} fill={colour(node.path)} className="graph-dot"/>{(active===node.path||graph.nodes.length<30||!!query&&graph.matches.has(node.path))&&<text x="12" y="4">{node.path.split('/').at(-1)}</text>}
          </g>)}
        </g>
      </svg>:<div className="empty"><h3>{query?'No matching files':'No indexed files yet'}</h3><p>{query?'Try another file or folder name.':'Refresh the codebase index to build its map.'}</p></div>}
      <div className="graph-controls"><button aria-label="Zoom out" onClick={()=>zoom(-.25)}><Minus size={15}/></button><span>{Math.round(view.scale*100)}%</span><button aria-label="Zoom in" onClick={()=>zoom(.25)}><Plus size={15}/></button><button aria-label="Reset graph view" onClick={()=>setView({x:0,y:0,scale:1})}><RotateCcw size={15}/></button></div>
      <div className="graph-legend">{groups.slice(0,6).map((group,i)=><span key={group}><i style={{background:colours[i]}}/>{group}</span>)}{groups.length>6&&<span>+{groups.length-6} folders</span>}</div>
    </div><aside className="graph-inspector" aria-label="Graph file details">
      <div className="eyebrow">{active?'SELECTED FILE':'FILE DETAILS'}</div>
      {active?<><h2>{active.split('/').at(-1)}</h2><code>{active}</code><button className="graph-open" onClick={()=>onSource(active)}>Open source<ArrowUpRight size={14}/></button><h3>{connections.length} relationships</h3><div className="graph-connections">{connections.map((edge,i)=><button key={i} onClick={()=>setSelected(edge.source===active?edge.target:edge.source)}><small>{edge.source===active?'→':'←'} {edge.kind}</small><span>{edge.source===active?edge.target:edge.source}</span><small>{edge.confidence||'Confidence not recorded'}</small></button>)}{!connections.length&&<p>No resolved relationships for this filter.</p>}</div></>:<><h2>Select a file</h2><p>Inspect dependencies and source.</p><p className="graph-hint">Drag to pan · + / − to zoom</p></>}
      <details className="graph-provenance"><summary>About this map</summary><p>Local source index. Graphify-inspired layout; folder colours.</p><p>Snapshot <code>{snapshot?.slice(0,12)||'unavailable'}</code>. Static relationships may be incomplete.</p>{gaps.map((gap,i)=><p key={i}>{gap}</p>)}</details>
    </aside></div>
    {graph.omitted>0&&<p className="graph-limit" role="status">Showing 250 of {graph.total} indexed files. Search a folder or file to explore the rest.</p>}
  </section>;
}
