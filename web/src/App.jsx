import { useEffect, useState } from 'react'
import content from './content.js'

const GITHUB_ICON = (
  <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
  </svg>
)

function useTheme() {
  const [theme, setTheme] = useState(() => {
    const set = document.documentElement.dataset.theme
    if (set) return set
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('theme', theme)
    } catch (e) {}
  }, [theme])
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))]
}

function CopyBlock({ code }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (e) {}
  }
  return (
    <div className="code">
      <button className="copy" onClick={copy} aria-label="Copy commands">
        {copied ? 'Copied' : 'Copy'}
      </button>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  )
}

export default function App() {
  const [theme, toggle] = useTheme()
  const c = content

  return (
    <>
      <header className="nav">
        <div className="wrap nav-inner">
          <a className="brand" href="#top">
            <span className="logo">{c.name.charAt(0)}</span>
            {c.name}
          </a>
          <nav className="links">
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
            <a href="#start">Run it</a>
          </nav>
          <div className="nav-actions">
            <button className="icon-btn" onClick={toggle} aria-label="Toggle dark mode" title="Toggle theme">
              {theme === 'dark' ? '☀' : '☾'}
            </button>
            <a className="btn ghost" href={c.repo} target="_blank" rel="noreferrer">
              {GITHUB_ICON} GitHub
            </a>
          </div>
        </div>
      </header>

      <main id="top">
        <section className="hero wrap">
          <p className="eyebrow">{c.eyebrow}</p>
          <h1>{c.tagline}</h1>
          <p className="lead">{c.description}</p>
          <div className="cta">
            <a className="btn primary" href={c.repo} target="_blank" rel="noreferrer">
              View on GitHub
            </a>
            <a className="btn ghost" href="#start">
              Run it locally
            </a>
          </div>
          <div className="chips">
            {c.stack.map((s) => (
              <span key={s} className="chip">
                {s}
              </span>
            ))}
          </div>
          {c.notice && <p className="notice">{c.notice}</p>}
        </section>

        <section id="how" className="wrap section">
          <h2>How it works</h2>
          <ol className="steps">
            {c.steps.map((s, i) => (
              <li key={s.title} className="card step">
                <span className="num">{i + 1}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        {c.stats && (
          <section className="wrap section">
            <h2>{c.stats.title}</h2>
            <div className="stats">
              {c.stats.items.map((s) => (
                <div key={s.label} className="card stat">
                  <div className="stat-value">{s.value}</div>
                  <div className="stat-label">{s.label}</div>
                </div>
              ))}
            </div>
            {c.stats.note && <p className="muted small">{c.stats.note}</p>}
          </section>
        )}

        <section id="features" className="wrap section">
          <h2>Features</h2>
          <div className="grid">
            {c.features.map((f) => (
              <div key={f.title} className="card">
                <h3>{f.title}</h3>
                <p>{f.text}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="start" className="wrap section">
          <h2>Run it locally</h2>
          <p className="muted">{c.startNote}</p>
          <CopyBlock code={c.quickstart} />
        </section>
      </main>

      <footer className="footer">
        <div className="wrap footer-inner">
          <span>
            {c.name} · by{' '}
            <a href="https://github.com/Abhishek2005-Siva" target="_blank" rel="noreferrer">
              Abhishek2005-Siva
            </a>
          </span>
          <a href={c.repo} target="_blank" rel="noreferrer">
            Source on GitHub →
          </a>
        </div>
      </footer>
    </>
  )
}
