import {useId, useState} from 'react';
import type {GlobalEntry} from './GlobalCatalogue';
import './GlobalRelationships.css';

export type CatalogueGraph = {
  coverage: 'unknown';
  truncated: boolean;
  limitations: string[];
  nodes: {id: string; type: 'package' | 'entry' | 'source'; label: string; source_url?: string; entry?: GlobalEntry}[];
  edges: {id: string; source: string; target: string; type: 'affects' | 'applies_to' | 'supported_by'; canonical_id: string; material_revision: number; applicability: 'unknown'}[];
};

const nodeKinds = {package: 'Package', entry: 'Finding', source: 'Source'};

function RelationshipMap({graph}: {graph: CatalogueGraph}) {
  const markerId = useId();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const kinds = ['package', 'entry', 'source'] as const;
  const groups = kinds.map(kind => graph.nodes.filter(node => node.type === kind));
  const height = Math.max(1, ...groups.map(group => group.length)) * 112 + 68;
  const positions = new Map(groups.flatMap((group, column) => group.map((node, row) =>
    [node.id, {x: 20 + column * 310, y: 56 + row * 112}] as const)));
  const selected = graph.nodes.find(node => node.id === selectedId);
  const related = graph.edges.filter(edge => edge.source === selectedId || edge.target === selectedId);
  return <>
    <p>Select a node to inspect its evidence. Arrows follow the public source claims.</p>
    <div className="global-graph-scroll" role="region" aria-label="Interactive public graph" tabIndex={0}>
      <div className="global-graph-canvas" style={{height}}>
        {kinds.map((kind, column) => <span key={kind} className="global-graph-column" style={{left: 20 + column * 310}}>{nodeKinds[kind]}</span>)}
        <svg width="880" height={height} aria-hidden="true">
          <defs><marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>
          {graph.edges.map(edge => {
            const from = positions.get(edge.source), to = positions.get(edge.target);
            if (!from || !to) return null;
            const forward = to.x > from.x;
            const x1 = from.x + (forward ? 220 : 0), x2 = to.x + (forward ? 0 : 220);
            const y1 = from.y + 38, y2 = to.y + 38, middle = (x1 + x2) / 2;
            return <g key={edge.id} className={selectedId && !related.includes(edge) ? 'global-graph-dim' : ''}>
              <path data-edge={edge.id} d={`M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`} fill="none" stroke="currentColor" strokeWidth="1.5" markerEnd={`url(#${markerId})`}/>
              <text x={middle} y={(y1 + y2) / 2 - 10} textAnchor="middle">{edge.type.replaceAll('_', ' ')}</text>
            </g>;
          })}
        </svg>
        {graph.nodes.map(node => {
          const position = positions.get(node.id)!;
          return <button key={node.id} className="global-graph-node" style={{left: position.x, top: position.y}}
            aria-label={`${nodeKinds[node.type]}: ${node.label}`} aria-pressed={selectedId === node.id} onClick={() => setSelectedId(node.id)} title={node.label}>
            <span>{node.entry?.canonical_id || nodeKinds[node.type]}</span><strong>{node.label}</strong>
          </button>;
        })}
      </div>
    </div>
    {selected && <section className="global-graph-detail" aria-label="Selected graph node" aria-live="polite">
      <h3>{selected.label}</h3>
      <p>{nodeKinds[selected.type]} · {related.length} relationships · Applicability unknown</p>
      {selected.entry && <>
        <p>{selected.entry.summary}</p>
        <p>{selected.entry.canonical_id} · Revision {selected.entry.material_revision} · Freshness: {selected.entry.freshness} · Source: {selected.entry.source_quality}</p>
        {selected.entry.withdrawn_at && <p>Withdrawn: {selected.entry.withdrawn_at}</p>}
        {selected.entry.source_status === 'failed' && <p>Source refresh failed. Revalidate before considering a proposal.</p>}
        <p>Version rules: {selected.entry.version_status}. Repository applicability must be assessed separately.</p>
      </>}
      {(selected.source_url || selected.entry?.source_url)?.startsWith('https://') && <a href={selected.source_url || selected.entry?.source_url} target="_blank" rel="noopener noreferrer">Original source</a>}
    </section>}
  </>;
}

export default function GlobalRelationships({query}: {query: () => Promise<CatalogueGraph>}) {
  const [graph, setGraph] = useState<CatalogueGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    setLoading(true); setError(''); setGraph(null);
    try { setGraph(await query()); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Relationships unavailable'); }
    finally { setLoading(false); }
  }
  const labels = new Map(graph?.nodes.map(node => [node.id, node]));
  return <section className="panel" aria-label="Public relationship graph">
    <h2>Public relationship graph</h2>
    <p>Explore package, finding and original-source relationships. These public claims do not confirm repository or version applicability.</p>
    <button onClick={load} disabled={loading}>{loading ? 'Loading relationships…' : 'View relationships'}</button>
    {error && <p role="alert">{error}</p>}
    {graph && <>
      <p role="status">{graph.nodes.length} nodes · {graph.edges.length} relationships · Applicability unknown</p>
      {graph.truncated && <p>Graph truncated to the bounded query results.</p>}
      {graph.edges.length === 0 && <p>No relationships found. Coverage remains unknown.</p>}
      {graph.nodes.length > 0 && <RelationshipMap graph={graph}/>}
      <ul className="global-catalogue-relationships">{graph.edges.map(edge => {
        const target = labels.get(edge.target);
        const entry = labels.get(edge.source)?.entry;
        return <li key={edge.id}>
          <strong>{labels.get(edge.source)?.label || edge.canonical_id}</strong>
          <span aria-hidden="true"> → </span><span>{edge.type.replaceAll('_', ' ')}</span><span aria-hidden="true"> → </span>
          {target?.source_url?.startsWith('https://') ? <a href={target.source_url} target="_blank" rel="noopener noreferrer">{target.label}</a> : <strong>{target?.label}</strong>}
          <p>{edge.canonical_id} · Revision {edge.material_revision} · Applicability {edge.applicability}</p>
          <div className="global-catalogue-badges">
            <span className="tag">{entry?.freshness === 'fresh' ? 'Fresh' : entry?.freshness === 'stale' ? 'Stale' : 'Freshness unknown'}</span>
            {entry?.withdrawn_at && <span className="tag">Withdrawn</span>}
            {entry?.source_status === 'failed' && <span className="tag">Source refresh failed</span>}
            <span className="tag">{entry?.version_status === 'unsupported' ? 'Version rules unsupported' : entry?.version_status === 'exact_versions' ? 'Explicit versions supplied' : 'Version applicability unknown'}</span>
          </div>
          {(!entry || entry.freshness !== 'fresh' || entry.withdrawn_at || entry.source_status === 'failed') && <p>Revalidate this source before considering a new proposal.</p>}
        </li>;
      })}</ul>
      {graph.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}
    </>}
  </section>;
}
