import {useId, useState, type FormEvent} from 'react';
import './GlobalCatalogue.css';
import GlobalRelationships, {type CatalogueGraph} from './GlobalRelationships';

export type GlobalEntry = {
  canonical_id: string;
  aliases: string[];
  kind: 'vulnerability' | 'improvement' | 'framework_update' | 'research_update';
  title: string;
  summary: string;
  source_url: string;
  source_quality: 'fixture' | 'unreviewed' | 'reviewed';
  published_at: string;
  retrieved_at: string;
  withdrawn_at: string | null;
  source_content_hash: string;
  material_revision: number;
  freshness: 'fresh' | 'stale' | 'unknown';
  source_status?: 'failed';
  source_modified_at?: string | null;
  version_status: 'unsupported' | 'exact_versions' | 'unknown';
  affected: {
    ecosystem: string;
    package: string;
    versions: string[];
    fixed_versions: string[];
    ranges: {type: string; events: {introduced?: string | null; fixed?: string | null; last_affected?: string | null; limit?: string | null}[]}[];
  }[];
  conditions: string[];
  evidence: string[];
  limitations: string[];
};

export type CatalogueQuery = {ecosystem: string; package: string; limit: number; provider: 'local' | 'atlas'};
export type CatalogueResult = {
  ecosystem: string;
  package: string;
  coverage: 'unknown';
  entries: GlobalEntry[];
  limitations: string[];
  truncated: boolean;
  source_status?: {
    operation: 'ingest' | 'atlas' | 'databricks';
    identity: string;
    state: 'failed' | 'succeeded' | 'published' | 'curated';
    detail: string;
    attempted_at: string;
  }[];
};

export default function GlobalCatalogue({query, graphQuery}: {query: (params: CatalogueQuery) => Promise<CatalogueResult>; graphQuery?: (params: CatalogueQuery) => Promise<CatalogueGraph>}) {
  const id = useId();
  const [ecosystem, setEcosystem] = useState('');
  const [packageName, setPackageName] = useState('');
  const [provider, setProvider] = useState<'local' | 'atlas'>('local');
  const [result, setResult] = useState<CatalogueResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastQuery, setLastQuery] = useState<CatalogueQuery | null>(null);

  async function search(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const params = {ecosystem: ecosystem.trim(), package: packageName.trim(), limit: 25, provider};
      setResult(await query(params));
      setLastQuery(params);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Catalogue unavailable');
    } finally {
      setLoading(false);
    }
  }

  return <section className="global-catalogue" aria-labelledby={`${id}-heading`}>
    <header>
      <div className="eyebrow">Duck Tape · Public knowledge</div>
      <h1 id={`${id}-heading`}>Global catalogue</h1>
      <p>Inspect public vulnerabilities, improvements and updates by exact ecosystem and package. Repository applicability is assessed separately using local evidence.</p>
    </header>
    <form className="panel global-catalogue-search" onSubmit={search}>
      <label htmlFor={`${id}-provider`}>Catalogue store
        <select id={`${id}-provider`} value={provider} disabled={loading} onChange={event => setProvider(event.target.value as 'local' | 'atlas')}>
          <option value="local">Local catalogue</option>
          <option value="atlas">Atlas public catalogue</option>
        </select>
      </label>
      <label htmlFor={`${id}-ecosystem`}>Ecosystem
        <input id={`${id}-ecosystem`} placeholder="e.g. PyPI or npm" value={ecosystem} maxLength={64} required disabled={loading} onChange={event => setEcosystem(event.target.value)}/>
      </label>
      <label htmlFor={`${id}-package`}>Package
        <input id={`${id}-package`} placeholder="Exact package name" value={packageName} maxLength={256} required disabled={loading} onChange={event => setPackageName(event.target.value)}/>
      </label>
      <button className="primary" disabled={loading || !ecosystem.trim() || !packageName.trim()}>{loading ? 'Searching…' : 'Search catalogue'}</button>
    </form>
    {provider === 'atlas' && <p>Search explicitly queries the configured public Atlas catalogue. Changing the selection does not connect.</p>}
    <p role="status" aria-live="polite">{loading ? 'Searching public catalogue…' : result ? `${result.entries.length} entries for ${result.ecosystem} / ${result.package}.` : 'Searches return at most 25 entries.'}</p>
    {error && <p role="alert" className="alert error">{error}</p>}
    {result && <>
      <aside className="panel" aria-label="Catalogue coverage">
        <h2>Coverage unknown</h2>
        <p>A matching record is public evidence, not a repository finding or permission to prepare a patch.</p>
        {result.limitations.map((item, index) => <p key={index}>{item}</p>)}
        {result.truncated && <p>Results truncated at 25 entries. This is not the complete set of matching records.</p>}
      </aside>
      {graphQuery && lastQuery && <GlobalRelationships key={JSON.stringify(lastQuery)} query={() => graphQuery(lastQuery)}/>}
      <section className="panel" aria-label="Source and publication status">
        <h2>Source and publication status</h2>
        <p>Latest catalogue operations. Failed refreshes can leave previous records available; record freshness alone does not confirm a successful refresh.</p>
        {!result.source_status?.length && <p>No operation status supplied. Live source and publication health are unknown.</p>}
        {result.source_status?.map((operation, index) => <div className="global-catalogue-operation" key={index}>
          <strong>{operation.operation} · {operation.state}</strong>
          <p>{operation.identity}</p>
          <p>{operation.detail} · <time dateTime={operation.attempted_at}>{operation.attempted_at}</time></p>
        </div>)}
      </section>
      {result.entries.length === 0 && <p>No catalogue entries found. This is not a clean scan.</p>}
      <div className="global-catalogue-entries">{result.entries.map(entry => <article className="panel" key={entry.canonical_id}>
        <div className="global-catalogue-badges">
          <span className="tag">{entry.kind.replaceAll('_', ' ')}</span>
          <span className="tag">{entry.freshness === 'stale' ? 'Stale' : entry.freshness === 'fresh' ? 'Fresh' : 'Freshness unknown'}</span>
          {entry.withdrawn_at && <span className="tag">Withdrawn</span>}
          <span className="tag">{entry.version_status === 'unsupported' ? 'Version rules unsupported' : entry.version_status === 'exact_versions' ? 'Explicit versions supplied' : 'Version applicability unknown'}</span>
          <span className="tag">Source: {entry.source_quality}</span>
        </div>
        <h2>{entry.title}</h2>
        <p className="global-catalogue-summary">{entry.summary}</p>
        <dl>
          <dt>Identity</dt><dd>{entry.canonical_id} · Revision {entry.material_revision}</dd>
          <dt>Source</dt><dd>{/^https:\/\//i.test(entry.source_url) ? <a href={entry.source_url} target="_blank" rel="noopener noreferrer">Original source</a> : 'Source URL unavailable'}</dd>
          <dt>Published</dt><dd><time dateTime={entry.published_at}>{entry.published_at}</time></dd>
          <dt>Last retrieved</dt><dd><time dateTime={entry.retrieved_at}>{entry.retrieved_at}</time></dd>
          {entry.withdrawn_at && <><dt>Withdrawn on</dt><dd><time dateTime={entry.withdrawn_at}>{entry.withdrawn_at}</time></dd></>}
        </dl>
        {(entry.withdrawn_at || entry.freshness !== 'fresh' || entry.source_status === 'failed') && <p>Revalidate this source before considering a new proposal.</p>}
        <details>
          <summary>Evidence and version rules</summary>
          <h3>Affected packages</h3>
          {entry.affected.map((affected, index) => <div key={index}>
            <p><strong>{affected.ecosystem} / {affected.package}</strong></p>
            <p>Explicit versions: {affected.versions.join(', ') || 'Not supplied'}</p>
            <p>Known fixed versions: {affected.fixed_versions.join(', ') || 'Not supplied'}</p>
            {affected.ranges.length > 0 && <pre>{JSON.stringify(affected.ranges, null, 2)}</pre>}
          </div>)}
          <h3>Source evidence</h3><ul>{entry.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
          {entry.conditions.length > 0 && <><h3>Conditions</h3><ul>{entry.conditions.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
          {entry.limitations.length > 0 && <><h3>Limitations</h3><ul>{entry.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
          <p>Aliases: {entry.aliases.join(', ') || 'None supplied'}</p>
          <p>Source hash: <code>{entry.source_content_hash}</code></p>
        </details>
      </article>)}</div>
    </>}
  </section>;
}
