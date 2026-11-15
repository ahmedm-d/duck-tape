import {
  ArrowDown, ArrowUpRight, BookOpen, FileCheck2,
  ShieldCheck, Ticket, Zap,
} from 'lucide-react';
import {WorkspacePreview} from './WorkspacePreview';
import './landing.css';

const logo = new URL('./assets/logo.png', import.meta.url).href;
const heroStill = new URL('./assets/hero-still.png', import.meta.url).href;

const stages = [
  {name: 'Understand', detail: 'Retrieve source evidence and approved repository knowledge.'},
  {name: 'Prepare', detail: 'Approve preparation, then plan and edit in isolation.'},
  {name: 'Check', detail: 'Run tests and an independent review. Revise when needed.'},
  {name: 'Review', detail: 'Review and approve the exact revision, then apply to a managed branch.'},
];

function JobFlow() {
  return <div className="landing-flow-diagram">
    <svg className="landing-flow-ribbons" viewBox="0 0 1100 260" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="landing-warm"><stop stopColor="#a75662"/><stop offset="1" stopColor="#e9b8a9"/></linearGradient>
        <linearGradient id="landing-cool" gradientUnits="userSpaceOnUse" x1="80" x2="365"><stop stopColor="#689f9e"/><stop offset="1" stopColor="#c4ddd2"/></linearGradient>
        <linearGradient id="landing-violet"><stop stopColor="#443481"/><stop offset=".5" stopColor="#9b6bcd"/><stop offset="1" stopColor="#86b2b5"/></linearGradient>
        <linearGradient id="landing-path" gradientUnits="userSpaceOnUse" x1="365" x2="1000"><stop stopColor="#e3a995"/><stop offset=".55" stopColor="#7362c7"/><stop offset="1" stopColor="#b494ac"/></linearGradient>
      </defs>
      <use href="#landing-ribbons" className="landing-ribbon-glow"/>
      <g id="landing-ribbons" fill="none" strokeWidth="21" strokeLinecap="round">
        <path d="M80 55C225 55 195 130 365 130" stroke="url(#landing-warm)"/>
        <path d="M80 130H365" stroke="url(#landing-cool)"/>
        <path d="M80 205C220 205 205 130 365 130" stroke="url(#landing-violet)"/>
        <path d="M365 130H1000" stroke="url(#landing-path)"/>
        <path d="M575 130C645 0 715 0 785 130" stroke="url(#landing-violet)"/>
        <path d="M785 130C860 245 930 245 1000 130" stroke="url(#landing-warm)"/>
      </g>
    </svg>
    <ul className="landing-flow-sources" aria-label="Job sources">
      <li><Ticket size={17} aria-hidden="true"/>Tickets</li>
      <li><ShieldCheck size={17} aria-hidden="true"/>Security</li>
      <li><BookOpen size={17} aria-hidden="true"/>Research</li>
    </ul>
    <ol className="landing-flow" aria-label="A full Duck Tape job">
      {stages.map(({name, detail}) => <li key={name}>
        <h3>{name}</h3>
        <span className="landing-visually-hidden">{detail}</span>
      </li>)}
    </ol>
  </div>;
}

const benchmarks = [
  {name: 'Security review', base: 7, compared: 200, total: 200, method: 'Preference pairs'},
  {name: 'Code review', base: 4, compared: 165, total: 168, method: 'Preference pairs'},
  {name: 'Repository retrieval', base: 30, compared: 190, total: 200, method: 'Local retrieval'},
  {name: 'Test generation', base: 84, compared: 166, total: 200, method: 'Prompted model'},
  {name: 'Code implementation', base: 100, compared: 180, total: 200, method: 'Prompted model'},
  {name: 'Frontend / UI generation', base: 135, compared: 200, total: 200, method: 'Prompted model'},
  {name: 'Task routing', base: 102, compared: 162, total: 200, method: 'Prompted model'},
  {name: 'Ticket parsing', base: 103, compared: 159, total: 200, method: 'Prompted model'},
  {name: 'Log classification', base: 146, compared: 196, total: 200, method: 'Prompted model'},
  {name: 'Database migrations', base: 160, compared: 196, total: 200, method: 'Prompted model'},
  {name: 'Commit / PR writing', base: 172, compared: 200, total: 200, method: 'Prompted model'},
  {name: 'Infrastructure as code', base: 197, compared: 196, total: 200, method: 'Prompted model'},
];

function Benchmarks() {
  return <>
    <div className="landing-stat-grid">
      <article className="landing-stat-card">
        <div className="landing-card-label"><span>RUBRIC SCORE</span><span>ALL METHODS</span></div>
        <div className="landing-card-icon"><FileCheck2 size={25} strokeWidth={1.5} aria-hidden="true"/></div>
        <h3>Checks passed.</h3>
        <div className="landing-stat-value">93.3<span>%</span></div>
        <p className="landing-stat-detail">2,210 / 2,368 rubric checks passed.</p>
        <p className="landing-stat-detail">52.4% baseline. Pooled across evaluation methods.</p>
      </article>
      <article className="landing-stat-card">
        <div className="landing-card-label"><span>EVALUATION SIZE</span><span>50 PER SPECIALIST</span></div>
        <div className="landing-card-icon"><BookOpen size={25} strokeWidth={1.5} aria-hidden="true"/></div>
        <h3>Examples evaluated.</h3>
        <div className="landing-stat-value">600</div>
        <p className="landing-stat-detail">50 held-out examples for each benchmark.</p>
      </article>
      <article className="landing-stat-card">
        <div className="landing-card-label"><span>SPECIALIST COVERAGE</span><span>FULL REPORTED SET</span></div>
        <div className="landing-card-icon"><Zap size={25} strokeWidth={1.5} aria-hidden="true"/></div>
        <h3>Benchmarks covered.</h3>
        <div className="landing-stat-value">12</div>
        <p className="landing-stat-detail">Generation, preference scoring and retrieval.</p>
      </article>
    </div>

    <div className="landing-benchmark-table" role="region" aria-label="Specialist benchmark results" tabIndex={0}>
      <table aria-describedby="benchmark-methods">
        <caption className="landing-visually-hidden">Specialist rubric results, ordered by score improvement</caption>
        <thead><tr><th scope="col">Specialist</th><th scope="col">Evaluation</th><th scope="col">Baseline</th><th scope="col">Compared result</th><th scope="col">Gain</th></tr></thead>
        <tbody>{benchmarks.map(result => {
          const gain = (result.compared - result.base) / result.total * 100;
          return <tr key={result.name}>
            <th scope="row">{result.name}</th>
            <td><span className="landing-benchmark-method">{result.method}</span></td>
            <td><strong>{(result.base / result.total * 100).toFixed(1)}%</strong><small>{result.base}/{result.total} checks</small></td>
            <td><strong>{(result.compared / result.total * 100).toFixed(1)}%</strong><small>{result.compared}/{result.total} checks</small></td>
            <td className={gain < 0 ? 'landing-benchmark-gain is-regression' : 'landing-benchmark-gain'}>{gain > 0 ? '+' : ''}{gain.toFixed(1)} pp</td>
          </tr>;
        })}</tbody>
      </table>
    </div>

    <div className="landing-benchmark-methods" id="benchmark-methods">
      <h3>What the scores measure.</h3>
      <p>Each percentage measures rubric checks across 50 examples. Gains are in percentage points. The pooled score combines the three evaluation methods below.</p>
      <dl>
        <div><dt>Prompted model</dt><dd>Zero-shot gemma-4-31b-it compared with the same model with a task contract and few-shot examples. Checks assess output structure, grounding and specialist rules.</dd></div>
        <div><dt>Preference pairs</dt><dd>Offline scoring of rejected and chosen dataset responses for security and code review.</dd></div>
        <div><dt>Local retrieval</dt><dd>Basic vector retrieval compared with a local hybrid and graph pipeline. Top-five recall rose from 60% to 92%; caller and test inclusion reached 88%.</dd></div>
      </dl>
      <p className="landing-benchmark-alert">Diff syntax validity fell from 80% to 62% for test generation. Infrastructure as code declined by 0.5 percentage points.</p>
      <p className="landing-benchmark-source">Source: team-supplied evaluation report, 15 November 2026. Independent reproduction is pending. Claude backend performance is evaluated separately.</p>
    </div>
  </>;
}

export function Landing() {
  return <div className="landing" id="top">
    <div className="landing-atmosphere" aria-hidden="true"/>
    <a className="landing-skip" href="#workspace">Skip to content</a>
    <div className="landing-opening">
      <header className="landing-header">
        <a className="landing-logo" href="/" aria-label="Duck Tape home"><img src={logo} alt="Duck Tape" width="44" height="44"/></a>
        <nav className="landing-nav" aria-label="Main navigation">
          <a href="#workspace">Workspace</a>
          <a href="#workflow">Workflow</a>
          <a href="#benchmarks">Benchmarks</a>
        </nav>
      </header>
      <section className="landing-hero" aria-labelledby="landing-headline">
        <img className="landing-hero-art" src={heroStill} alt="" width="1672" height="941" fetchPriority="high"/>
        <div className="landing-hero-content">
          <div className="landing-hero-copy">
            <h1 id="landing-headline"><span>Duck Tape</span> holds your codebase together.</h1>
            <div className="landing-hero-actions">
              <a className="landing-button" href="/login">Log in</a>
              <a className="landing-button landing-button-create" href="/signup">Start creating <ArrowUpRight size={16} aria-hidden="true"/></a>
            </div>
          </div>
        </div>
        <a className="landing-scroll" href="#workspace" aria-label="Explore the workspace">
          <ArrowDown size={19} aria-hidden="true"/>
        </a>
      </section>
    </div>

    <main className="landing-main">
      <section className="landing-workspace landing-section" id="workspace" aria-labelledby="workspace-heading" tabIndex={-1}>
        <div className="landing-section-heading">
          <div><span className="landing-kicker">01 / THE WORKSPACE</span><h2 id="workspace-heading">Everything in view.</h2></div>
          <a className="landing-text-link" href="/dashboard">Open workspace <ArrowUpRight size={18} aria-hidden="true"/></a>
        </div>
        <WorkspacePreview/>
      </section>

      <section className="landing-workflow landing-section" id="workflow" aria-labelledby="workflow-heading">
        <span className="landing-kicker">02 / THE WORKFLOW</span>
        <h2 id="workflow-heading">A job. Start to finish.</h2>
        <JobFlow/>
      </section>

      <section className="landing-benchmarks landing-section" id="benchmarks" aria-labelledby="benchmarks-heading">
        <div className="landing-section-heading">
          <div><span className="landing-kicker">03 / THE NUMBERS</span><h2 id="benchmarks-heading">Show the work.</h2></div>
          <p className="landing-benchmark-note">Team-reported results · 50 examples per benchmark.</p>
        </div>
        <Benchmarks/>
      </section>
    </main>

    <footer className="landing-footer">
      <a className="landing-brand" href="/"><img src={logo} alt="" width="25" height="25"/>Duck Tape</a>
      <span>© {new Date().getFullYear()} Duck Tape</span>
      <nav aria-label="Footer navigation"><a href="/dashboard">Workspace <ArrowUpRight size={14} aria-hidden="true"/></a><a href="#top">Back to top ↑</a></nav>
    </footer>
  </div>;
}
