import {useState} from 'react';
import type {GlobalEntry} from './GlobalCatalogue';

export type CatalogueGraph = {
  coverage: 'unknown';
  truncated: boolean;
  limitations: string[];
  nodes: {id: string; type: 'package' | 'entry' | 'source'; label: string; source_url?: string; entry?: GlobalEntry}[];
  edges: {id: string; source: string; target: string; type: 'affects' | 'applies_to' | 'supported_by'; canonical_id: string; material_revision: number; applicability: 'unknown'}[];
};

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
