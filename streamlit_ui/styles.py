"""Feuille de style de l'interface Streamlit (aucune logique métier)."""

from __future__ import annotations

import streamlit as st

STYLE = """
<style>
  html, body, [class*="st-"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  .block-container { padding-top: 2rem; padding-bottom: 3.5rem; max-width: 1180px; }
  [data-testid="stSidebar"] { border-right: 1px solid rgba(148, 163, 184, .14); }
  [data-testid="stSidebar"] .block-container { padding-top: 1.4rem; }

  .nx-brand { display: flex; align-items: center; gap: .65rem; margin-bottom: .2rem; }
  .nx-logo {
    display: inline-flex; align-items: center; justify-content: center;
    width: 40px; height: 40px; border-radius: 13px; flex: 0 0 40px;
    background: linear-gradient(135deg, #22d3ee, #6366f1);
    color: #04121f; font-weight: 900; font-size: 1.3rem;
    box-shadow: 0 8px 24px rgba(34, 211, 238, .25);
  }
  .nx-brand-title { font-size: 1.35rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.05; }
  .nx-brand-title span { color: #22d3ee; }
  .nx-brand small {
    display: block; color: #7c8ba1; font-size: .62rem; font-weight: 700;
    letter-spacing: .16em; margin-top: .2rem;
  }

  .nx-hero {
    padding: 1.35rem 1.55rem; border: 1px solid rgba(148, 163, 184, .16);
    border-radius: 20px; margin-bottom: 1.25rem;
    background:
      radial-gradient(1100px 260px at 0% 0%, rgba(34, 211, 238, .13), transparent 60%),
      radial-gradient(900px 260px at 100% 0%, rgba(99, 102, 241, .12), transparent 60%),
      rgba(17, 28, 51, .55);
  }
  .nx-kicker {
    color: #22d3ee; font-size: .7rem; font-weight: 800;
    letter-spacing: .18em; text-transform: uppercase;
  }
  .nx-hero h1 { font-size: 1.85rem; margin: .3rem 0 .25rem; letter-spacing: -.035em; }
  .nx-hero p { margin: 0; color: #9fb0c8; font-size: .92rem; max-width: 860px; }

  .nx-pill {
    display: inline-block; padding: .2rem .62rem; border-radius: 999px;
    font-size: .7rem; font-weight: 700; letter-spacing: .02em; white-space: nowrap;
  }
  .nx-accept { background: rgba(74, 222, 128, .12); color: #4ade80; border: 1px solid rgba(74, 222, 128, .35); }
  .nx-verify { background: rgba(251, 191, 36, .12); color: #fbbf24; border: 1px solid rgba(251, 191, 36, .35); }
  .nx-block { background: rgba(248, 113, 113, .12); color: #f87171; border: 1px solid rgba(248, 113, 113, .35); }
  .nx-info { background: rgba(34, 211, 238, .10); color: #67e8f9; border: 1px solid rgba(34, 211, 238, .30); }
  .nx-neutral { background: rgba(148, 163, 184, .12); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, .25); }

  .nx-card {
    border: 1px solid rgba(148, 163, 184, .16); border-radius: 16px;
    padding: .9rem 1.05rem; background: rgba(17, 28, 51, .5); margin-bottom: .55rem;
  }
  .nx-muted { color: #8fa2bb; font-size: .8rem; }
  .nx-big { font-size: 1.7rem; font-weight: 800; letter-spacing: -.03em; }
  .nx-code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 1.8rem; font-weight: 800; letter-spacing: .32em; color: #67e8f9;
  }

  div[data-testid="stMetric"] {
    background: rgba(17, 28, 51, .55); border: 1px solid rgba(148, 163, 184, .16);
    border-radius: 16px; padding: .75rem 1rem;
  }
  div[data-testid="stMetricLabel"] p {
    color: #9fb0c8; font-size: .74rem; text-transform: uppercase;
    letter-spacing: .07em; font-weight: 700;
  }
  div[data-testid="stMetricValue"] { font-weight: 800; letter-spacing: -.02em; font-size: 1.5rem; }
  div[data-testid="stMetricDelta"] { font-size: .72rem; }

  .nx-kpi-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr));
    gap: .75rem; margin: .15rem 0 1.1rem;
  }
  .nx-kpi {
    border: 1px solid rgba(148, 163, 184, .16); border-radius: 16px;
    padding: .8rem 1rem; background: rgba(17, 28, 51, .55); min-width: 0;
  }
  .nx-kpi-label {
    color: #9fb0c8; font-size: .67rem; text-transform: uppercase;
    letter-spacing: .07em; font-weight: 700; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .nx-kpi-value {
    font-size: 1.45rem; font-weight: 800; letter-spacing: -.03em;
    margin-top: .18rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .nx-kpi-delta { font-size: .72rem; margin-top: .12rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .nx-up { color: #4ade80; }
  .nx-down { color: #f87171; }
  .nx-flat { color: #8fa2bb; }

  .nx-cat-row { display: flex; justify-content: space-between; gap: 1rem; font-size: .86rem; margin-top: .55rem; }
  .nx-cat-track { height: 7px; border-radius: 999px; background: rgba(148, 163, 184, .14); overflow: hidden; margin-top: .28rem; }
  .nx-cat-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #22d3ee, #6366f1); }

  .stTabs [data-baseweb="tab-list"] { gap: 6px; flex-wrap: wrap; }
  .stTabs [data-baseweb="tab"] { padding: .35rem .9rem; border-radius: 10px; }
  .stButton button { border-radius: 10px; font-weight: 600; }
</style>
"""


def apply_style() -> None:
    """Injecte la feuille de style dans la page courante."""
    st.markdown(STYLE, unsafe_allow_html=True)


__all__ = ["STYLE", "apply_style"]
