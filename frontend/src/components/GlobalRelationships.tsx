import {useState} from 'react';

export type CatalogueGraph = {
  coverage: 'unknown';
  truncated: boolean;
  limitations: string[];
  nodes: {id: string; type: 'package' | 'entry' | 'source'; label: string; source_url?: string}[];
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
        return <li key={edge.id}>
          <strong>{labels.get(edge.source)?.label || edge.canonical_id}</strong>
          <span aria-hidden="true"> → </span><span>{edge.type.replaceAll('_', ' ')}</span><span aria-hidden="true"> → </span>
          {target?.source_url?.startsWith('https://') ? <a href={target.source_url} target="_blank" rel="noopener noreferrer">{target.label}</a> : <strong>{target?.label}</strong>}
          <p>{edge.canonical_id} · Revision {edge.material_revision} · Applicability {edge.applicability}</p>
        </li>;
      })}</ul>
      {graph.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}
    </>}
  </section>;
}
