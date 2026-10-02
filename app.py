import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = ["pdf", "png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"]

st.set_page_config(
    page_title="Document Intelligence",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# MODERN UI THEME
# -----------------------------------------------------------------------------

st.markdown(
    """
    <style>
    :root {
        --di-primary: #2563eb;
        --di-primary-dark: #1d4ed8;
        --di-text: #172033;
        --di-muted: #667085;
        --di-border: #e5e7eb;
        --di-surface: #ffffff;
        --di-soft: #f7f9fc;
        --di-green: #14804a;
        --di-amber: #b54708;
    }

    .stApp {
        background: #f6f8fc;
    }

    [data-testid="stHeader"] {
        background: rgba(246,248,252,.88);
    }

    [data-testid="stSidebar"] {
        background: #101828;
        border-right: 1px solid #1d2939;
    }

    [data-testid="stSidebar"] * {
        color: #eaf0f8 !important;
    }

    [data-testid="stSidebar"] .stButton button {
        background: #1d2939;
        border: 1px solid #344054;
        color: #ffffff !important;
    }

    .di-brand {
        display: flex;
        align-items: center;
        gap: 14px;
        margin: 4px 0 28px 0;
    }

    .di-logo {
        width: 44px;
        height: 44px;
        border-radius: 13px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: linear-gradient(135deg, #3b82f6, #1d4ed8);
        color: white;
        font-size: 22px;
        box-shadow: 0 8px 22px rgba(37,99,235,.28);
    }

    .di-brand-title {
        font-size: 19px;
        font-weight: 750;
        line-height: 1.1;
        color: #ffffff;
    }

    .di-brand-sub {
        font-size: 12px;
        color: #98a2b3;
        margin-top: 4px;
    }

    .di-hero {
        background: linear-gradient(135deg, #0f172a 0%, #172554 54%, #1d4ed8 100%);
        border-radius: 22px;
        padding: 34px 38px;
        color: white;
        margin-bottom: 22px;
        box-shadow: 0 18px 45px rgba(15,23,42,.14);
    }

    .di-hero h1 {
        margin: 0;
        font-size: 38px;
        letter-spacing: -1.2px;
        color: white;
    }

    .di-hero p {
        margin: 10px 0 0 0;
        color: #dbeafe;
        font-size: 16px;
        max-width: 760px;
        line-height: 1.55;
    }

    .di-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        border-radius: 999px;
        padding: 6px 11px;
        font-size: 12px;
        font-weight: 700;
        margin-bottom: 12px;
        background: rgba(255,255,255,.12);
        border: 1px solid rgba(255,255,255,.18);
        color: #eff6ff;
    }

    .di-card {
        background: var(--di-surface);
        border: 1px solid var(--di-border);
        border-radius: 18px;
        padding: 20px;
        box-shadow: 0 5px 20px rgba(16,24,40,.045);
        margin-bottom: 16px;
    }

    .di-card-title {
        color: var(--di-text);
        font-size: 17px;
        font-weight: 750;
        margin-bottom: 5px;
    }

    .di-card-sub {
        color: var(--di-muted);
        font-size: 13px;
        line-height: 1.45;
    }

    .di-upload {
        background: white;
        border: 1.5px dashed #98a2b3;
        border-radius: 18px;
        padding: 28px;
        text-align: center;
        margin: 8px 0 18px 0;
    }

    .di-upload-icon {
        width: 58px;
        height: 58px;
        border-radius: 17px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: #eff6ff;
        font-size: 27px;
        margin-bottom: 10px;
    }

    .di-upload-title {
        font-size: 19px;
        font-weight: 750;
        color: var(--di-text);
    }

    .di-upload-sub {
        color: var(--di-muted);
        font-size: 13px;
        margin-top: 5px;
    }

    .di-step {
        background: white;
        border: 1px solid var(--di-border);
        border-radius: 14px;
        padding: 15px 12px;
        text-align: center;
        min-height: 86px;
    }

    .di-step-number {
        width: 27px;
        height: 27px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: #eff6ff;
        color: var(--di-primary);
        font-size: 12px;
        font-weight: 800;
        margin-bottom: 7px;
    }

    .di-step-label {
        color: var(--di-text);
        font-size: 12px;
        font-weight: 650;
    }

    .di-result-header {
        background: white;
        border: 1px solid var(--di-border);
        border-radius: 20px;
        padding: 24px 26px;
        margin-bottom: 18px;
        box-shadow: 0 5px 20px rgba(16,24,40,.045);
    }

    .di-result-kicker {
        color: var(--di-primary);
        text-transform: uppercase;
        letter-spacing: .08em;
        font-size: 11px;
        font-weight: 800;
        margin-bottom: 5px;
    }

    .di-result-title {
        color: var(--di-text);
        font-size: 29px;
        font-weight: 800;
        letter-spacing: -.7px;
    }

    .di-result-file {
        color: var(--di-muted);
        font-size: 13px;
        margin-top: 5px;
        word-break: break-all;
    }

    .di-status {
        border-radius: 12px;
        padding: 12px 14px;
        font-size: 13px;
        font-weight: 700;
        margin: 8px 0 18px 0;
    }

    .di-status-pass {
        background: #ecfdf3;
        color: #027a48;
        border: 1px solid #abefc6;
    }

    .di-status-review {
        background: #fffaeb;
        color: #b54708;
        border: 1px solid #fedf89;
    }

    .di-status-neutral {
        background: #eff6ff;
        color: #175cd3;
        border: 1px solid #bfdbfe;
    }

    .di-mini-label {
        color: #667085;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: .05em;
        font-weight: 700;
    }

    .di-mini-value {
        color: #172033;
        font-size: 18px;
        font-weight: 800;
        margin-top: 3px;
    }

    .di-section-title {
        color: #172033;
        font-size: 21px;
        font-weight: 800;
        margin: 24px 0 10px 0;
        letter-spacing: -.3px;
    }

    .di-section-sub {
        color: #667085;
        font-size: 13px;
        margin-top: -5px;
        margin-bottom: 12px;
    }

    .di-info-card {
        background: white;
        border: 1px solid var(--di-border);
        border-radius: 15px;
        padding: 16px;
        height: 100%;
    }

    .di-info-label {
        color: #667085;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .04em;
        margin-bottom: 5px;
    }

    .di-info-value {
        color: #172033;
        font-size: 14px;
        font-weight: 650;
        overflow-wrap: anywhere;
    }

    .di-generic-text {
        background: #0b1220;
        color: #e5e7eb;
        border-radius: 15px;
        padding: 20px;
        font-family: Consolas, "Courier New", monospace;
        font-size: 12px;
        line-height: 1.55;
        white-space: pre-wrap;
        overflow-x: auto;
        max-height: 680px;
        overflow-y: auto;
    }
    .di-ocr-readable {
        background: #ffffff;
        color: #172033;
        border: 1px solid #e4e7ec;
        border-radius: 16px;
        padding: 22px 24px;
        font-family: Consolas, "Courier New", monospace;
        font-size: 13px;
        line-height: 1.65;
        white-space: pre-wrap;
        overflow-x: auto;
        max-height: 620px;
        overflow-y: auto;
        box-shadow: 0 5px 18px rgba(16,24,40,.035);
    }
    .di-ocr-readable::selection { background: #dbeafe; }


    .stButton > button {
        border-radius: 10px;
        font-weight: 700;
        min-height: 42px;
    }

    div[data-testid="stMetric"] {
        background: white;
        border: 1px solid var(--di-border);
        border-radius: 15px;
        padding: 14px 16px;
        box-shadow: 0 4px 14px rgba(16,24,40,.035);
    }

    div[data-testid="stMetricLabel"] {
        color: #667085 !important;
    }

    div[data-testid="stMetricValue"] {
        color: #172033 !important;
    }

    .di-hero-grid { display:grid; grid-template-columns:1.45fr .75fr; gap:14px; margin-bottom:18px; }
    .di-trust-card { background:#fff; border:1px solid var(--di-border); border-radius:18px; padding:20px; box-shadow:0 5px 20px rgba(16,24,40,.045); }
    .di-trust-title { font-size:13px; font-weight:800; color:#344054; text-transform:uppercase; letter-spacing:.06em; }
    .di-trust-main { font-size:21px; font-weight:800; color:#172033; margin-top:6px; }
    .di-trust-sub { color:#667085; font-size:12px; line-height:1.5; margin-top:5px; }
    .di-pill { display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px; font-size:11px; font-weight:800; background:#eff6ff; color:#175cd3; border:1px solid #bfdbfe; }
    .di-source-frame { background:#f8fafc; border:1px solid #e4e7ec; border-radius:16px; padding:10px; }
    .di-callout { border-left:4px solid #2563eb; background:#eff6ff; border-radius:10px; padding:13px 15px; color:#344054; font-size:13px; line-height:1.5; margin:14px 0 0 0; }
    .di-stat-strip { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .di-stat-chip { background:#f8fafc; border:1px solid #e4e7ec; border-radius:9px; padding:7px 10px; font-size:11px; color:#475467; }
    .di-stat-chip strong { color:#172033; }
    .di-divider { height:1px; background:#eaecf0; margin:22px 0; }

    /* Live processing experience */
    .di-progress-shell { background:#ffffff; border:1px solid #e4e7ec; border-radius:20px; padding:22px; margin:12px 0 18px 0; box-shadow:0 8px 26px rgba(16,24,40,.055); animation:diFadeUp .45s ease both; }
    .di-progress-head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px; }
    .di-progress-title { font-size:18px; font-weight:800; color:#172033; }
    .di-progress-detail { font-size:12px; color:#667085; margin-top:4px; }
    .di-progress-percent { font-size:13px; font-weight:800; color:#2563eb; white-space:nowrap; }
    .di-progress-track { height:8px; background:#eef2f7; border-radius:999px; overflow:hidden; margin-bottom:20px; }
    .di-progress-fill { height:100%; border-radius:999px; background:linear-gradient(90deg,#2563eb,#60a5fa); transition:width .55s ease; position:relative; overflow:hidden; }
    .di-progress-fill:after { content:""; position:absolute; inset:0; background:linear-gradient(90deg,transparent,rgba(255,255,255,.45),transparent); animation:diShimmer 1.7s infinite; }
    .di-stage-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; }
    .di-stage { min-height:76px; padding:11px 9px; border:1px solid #eaecf0; border-radius:13px; background:#f8fafc; transition:all .35s ease; }
    .di-stage.active { border-color:#93c5fd; background:#eff6ff; box-shadow:0 0 0 3px rgba(37,99,235,.07); transform:translateY(-2px); }
    .di-stage.done { border-color:#a7f3d0; background:#f0fdf4; }
    .di-stage-icon { font-size:17px; line-height:1; margin-bottom:7px; }
    .di-stage-label { font-size:11px; font-weight:800; color:#344054; }
    .di-stage-sub { font-size:10px; color:#667085; margin-top:3px; line-height:1.25; }
    .di-live-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#2563eb; margin-right:7px; box-shadow:0 0 0 0 rgba(37,99,235,.45); animation:diPulse 1.5s infinite; }
    .di-complete-banner { background:linear-gradient(135deg,#ecfdf3,#f0fdf4); border:1px solid #a7f3d0; color:#166534; border-radius:14px; padding:13px 15px; font-size:13px; font-weight:750; animation:diFadeUp .4s ease both; }
    .di-file-ready { display:flex; align-items:center; gap:10px; background:#f8fafc; border:1px solid #e4e7ec; border-radius:12px; padding:10px 12px; margin-top:12px; }
    .di-file-ready-icon { width:32px; height:32px; border-radius:9px; display:flex; align-items:center; justify-content:center; background:#eff6ff; }
    @keyframes diFadeUp { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
    @keyframes diPulse { 0% { box-shadow:0 0 0 0 rgba(37,99,235,.4); } 70% { box-shadow:0 0 0 8px rgba(37,99,235,0); } 100% { box-shadow:0 0 0 0 rgba(37,99,235,0); } }
    @keyframes diShimmer { from { transform:translateX(-100%); } to { transform:translateX(100%); } }
    @media (max-width: 900px) { .di-stage-grid { grid-template-columns:repeat(3,1fr); } }
    @media (max-width: 600px) { .di-stage-grid { grid-template-columns:repeat(2,1fr); } .di-progress-head { align-items:flex-start; flex-direction:column; } }
    .di-footer-note { color:#98a2b3; font-size:11px; text-align:center; padding:18px 0 5px 0; }
    .block-container {
        padding-top: 2.2rem;
        padding-bottom: 3rem;
        max-width: 1450px;
    }
    /* Premium application shell */
    .di-topbar { display:flex; align-items:center; justify-content:space-between; gap:18px; margin:0 0 18px; }
    .di-topbar-title { font-size:14px; font-weight:800; color:#667085; letter-spacing:.04em; text-transform:uppercase; }
    .di-topbar-state { display:inline-flex; align-items:center; gap:7px; border:1px solid #d0d5dd; background:#fff; border-radius:999px; padding:6px 10px; font-size:11px; font-weight:750; color:#344054; }
    .di-topbar-state .dot { width:7px; height:7px; border-radius:50%; background:#12b76a; box-shadow:0 0 0 3px #ecfdf3; }
    .di-hero-v2 { position:relative; overflow:hidden; background:linear-gradient(135deg,#0b1220 0%,#111c36 48%,#1d4ed8 100%); border-radius:26px; padding:31px 36px; color:#fff; margin-bottom:18px; box-shadow:0 22px 55px rgba(15,23,42,.18); }
    .di-hero-v2:after { content:""; position:absolute; width:280px; height:280px; right:-80px; top:-120px; border-radius:50%; background:rgba(96,165,250,.15); filter:blur(3px); }
    .di-hero-v2 .eyebrow { position:relative; z-index:1; display:inline-flex; align-items:center; gap:7px; border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.08); padding:6px 10px; border-radius:999px; font-size:10px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
    .di-hero-v2 h1 { position:relative; z-index:1; margin:13px 0 7px; font-size:38px; line-height:1.05; letter-spacing:-1.5px; color:#fff; max-width:780px; }
    .di-hero-v2 p { position:relative; z-index:1; margin:0; color:#dbeafe; font-size:13px; line-height:1.55; max-width:720px; }
    .di-hero-points { position:relative; z-index:1; display:flex; gap:8px; flex-wrap:wrap; margin-top:15px; }
    .di-hero-point { border:1px solid rgba(255,255,255,.13); background:rgba(255,255,255,.07); border-radius:10px; padding:7px 10px; font-size:11px; color:#e5efff; }
    .di-flow-card { background:#fff; border:1px solid #e4e7ec; border-radius:20px; padding:20px; box-shadow:0 8px 26px rgba(16,24,40,.045); }
    .di-flow-head { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; margin-bottom:15px; }
    .di-flow-title { font-size:16px; font-weight:800; color:#172033; }
    .di-flow-sub { font-size:12px; color:#667085; margin-top:4px; }
    .di-flow-badge { background:#f0fdf4; color:#027a48; border:1px solid #abefc6; border-radius:999px; padding:5px 9px; font-size:10px; font-weight:800; white-space:nowrap; }
    .di-flow-line { display:grid; grid-template-columns:repeat(5,1fr); gap:7px; }
    .di-flow-item { position:relative; background:#f8fafc; border:1px solid #eaecf0; border-radius:12px; padding:12px 9px; min-height:74px; }
    .di-flow-item:not(:last-child):after { content:""; position:absolute; top:22px; right:-8px; width:9px; height:1px; background:#d0d5dd; }
    .di-flow-num { width:24px; height:24px; display:flex; align-items:center; justify-content:center; border-radius:8px; background:#eff6ff; color:#175cd3; font-size:10px; font-weight:900; margin-bottom:8px; }
    .di-flow-label { font-size:11px; font-weight:800; color:#344054; }
    .di-flow-copy { font-size:9px; color:#667085; margin-top:3px; }
    .di-upload-v2 { margin-top:18px; border:1.5px dashed #98a2b3; background:linear-gradient(180deg,#fff,#fbfdff); border-radius:20px; padding:28px 24px 22px; text-align:center; transition:all .25s ease; }
    .di-upload-v2:hover { border-color:#60a5fa; box-shadow:0 10px 30px rgba(37,99,235,.07); }
    .di-upload-v2-icon { width:54px; height:54px; display:inline-flex; align-items:center; justify-content:center; border-radius:16px; background:#eff6ff; color:#2563eb; font-size:24px; margin-bottom:10px; }
    .di-upload-v2-title { font-size:18px; font-weight:800; color:#172033; }
    .di-upload-v2-copy { color:#667085; font-size:12px; margin-top:5px; }
    .di-selected { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:12px; padding:12px 14px; background:#f8fafc; border:1px solid #e4e7ec; border-radius:13px; text-align:left; }
    .di-selected-main { display:flex; align-items:center; gap:10px; min-width:0; }
    .di-file-icon { width:34px; height:34px; border-radius:10px; display:flex; align-items:center; justify-content:center; background:#eff6ff; flex:none; }
    .di-selected-name { font-size:12px; font-weight:800; color:#344054; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .di-selected-meta { font-size:10px; color:#667085; margin-top:2px; }
    .di-side-divider { height:1px; background:rgba(255,255,255,.08); margin:12px 2px; }
    .di-side-pipeline { position:relative; padding:4px 2px 3px 4px; }
    .di-side-pipeline:before { content:""; position:absolute; left:10px; top:13px; bottom:13px; width:1px; background:rgba(255,255,255,.12); }
    .di-side-stage { position:relative; display:flex; gap:10px; align-items:flex-start; padding:7px 5px; color:#98a2b3; font-size:10px; }
    .di-side-stage .node { width:13px; height:13px; border-radius:50%; background:#1d2939; border:2px solid #475467; z-index:1; flex:none; }
    .di-side-stage.active { color:#fff; }
    .di-side-stage.active .node { background:#60a5fa; border-color:#bfdbfe; box-shadow:0 0 0 4px rgba(96,165,250,.10); }
    .di-side-stage strong { display:block; font-size:10.5px; }
    .di-side-stage span { display:block; margin-top:2px; color:#667085; font-size:9px; }
    .di-side-stage.active span { color:#98a2b3; }
    /* Single, product-style uploader */
    [data-testid="stFileUploader"] { background:#fff; border:1.5px dashed #98a2b3; border-radius:20px; padding:18px; box-shadow:0 6px 20px rgba(16,24,40,.035); }
    [data-testid="stFileUploader"] section { border:0 !important; padding:8px !important; background:transparent !important; }
    [data-testid="stFileUploader"] small { color:#667085 !important; }
    .di-source-compact { max-width:590px; }

    @media (max-width: 900px) { .di-hero-v2 h1 { font-size:32px; } .di-flow-line { grid-template-columns:repeat(3,1fr); } .di-flow-item:not(:last-child):after { display:none; } }
    @media (max-width: 600px) { .di-hero-v2 { padding:28px 22px; } .di-hero-v2 h1 { font-size:28px; } .di-flow-line { grid-template-columns:repeat(2,1fr); } }
    /* Product workspace sidebar */
    [data-testid="stSidebar"] { width:270px !important; min-width:270px !important; background:linear-gradient(180deg,#09111f 0%,#0c1627 52%,#08111e 100%) !important; border-right:1px solid rgba(255,255,255,.07) !important; }
    [data-testid="stSidebar"] > div:first-child { padding:24px 18px 18px 18px !important; }
    [data-testid="stSidebar"] .block-container { padding-top:.2rem !important; }
    .di-product-brand { display:flex; align-items:center; gap:12px; padding:5px 6px 18px 6px; }
    .di-product-logo { width:42px; height:42px; border-radius:13px; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#2f80ff,#155eef); color:#fff; font-size:21px; box-shadow:0 8px 22px rgba(21,94,239,.25); }
    .di-product-name { color:#fff; font-size:15px; font-weight:760; line-height:1.15; letter-spacing:-.2px; }
    .di-product-tagline { margin-top:4px; color:#93a4bd; font-size:10px; letter-spacing:.1px; }
    .di-product-rule { height:1px; background:rgba(255,255,255,.08); margin:0 4px 18px; }
    .di-side-section-title { color:#aebed2; font-size:9px; font-weight:850; letter-spacing:1.5px; margin:22px 4px 8px; }
    .di-current-doc { padding:10px 12px; margin:10px 0 3px; border-radius:12px; background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.07); }
    .di-current-label { color:#6f86a4; font-size:8px; font-weight:800; letter-spacing:1.2px; }
    .di-current-name { color:#eaf1fb; font-size:11px; font-weight:650; margin-top:5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .di-empty-recent { color:#71839e; font-size:10px; line-height:1.5; padding:9px 8px; }
    .di-side-spacer { min-height:20px; }
    .di-engine { display:flex; align-items:center; gap:10px; padding:10px 11px; border:1px solid rgba(255,255,255,.08); border-radius:12px; background:rgba(255,255,255,.035); }
    .di-engine-dot { width:8px; height:8px; border-radius:50%; background:#27d17f; box-shadow:0 0 0 4px rgba(39,209,127,.10); flex:0 0 auto; }
    .di-engine-title { color:#cbd7e7; font-size:10px; font-weight:700; }
    .di-engine-status { color:#71839e; font-size:9px; margin-top:2px; }
    .di-side-footer { color:#566a85; font-size:8.5px; line-height:1.45; margin:10px 7px 0; }
    [data-testid="stSidebar"] .stButton > button { min-height:34px !important; height:30px !important; border-radius:8px !important; background:#172033 !important; border:1px solid #2b3a50 !important; color:#e9f0f9 !important; font-size:11px !important; font-weight:650 !important; text-align:left !important; padding:3px 10px !important; margin:0 0 3px !important; transition:all .18s ease !important; }
    [data-testid="stSidebar"] .stButton > button:hover { border-color:#4d78a8 !important; background:#1c2a40 !important; transform:translateX(2px); }
    [data-testid="stSidebar"] .stButton > button:focus { box-shadow:0 0 0 2px rgba(47,128,255,.18) !important; }
    [data-testid="stSidebar"] [data-testid="stBaseButton-primary"] { min-height:44px !important; height:44px !important; border-radius:10px !important; background:linear-gradient(135deg,#2563eb,#1d4ed8) !important; border:1px solid rgba(255,255,255,.12) !important; font-size:12px !important; text-align:center !important; margin-bottom:7px !important; box-shadow:0 8px 18px rgba(37,99,235,.18) !important; }
    [data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover { background:linear-gradient(135deg,#3478f6,#2563eb) !important; transform:translateY(-1px) !important; }
    .di-recent-heading { display:flex !important; align-items:center !important; justify-content:space-between !important; margin:17px 4px 8px !important; padding-bottom:9px !important; border-bottom:1px solid rgba(255,255,255,.10) !important; }
    .di-recent-title { color:#ffffff !important; font-size:11px !important; font-weight:850 !important; letter-spacing:1.45px !important; }
    .di-recent-count { min-width:22px !important; height:20px !important; padding:0 6px !important; display:inline-flex !important; align-items:center !important; justify-content:center !important; border-radius:999px !important; color:#c8d6e8 !important; background:rgba(255,255,255,.08) !important; border:1px solid rgba(255,255,255,.10) !important; font-size:9px !important; font-weight:800 !important; }
    .di-recent-scroll { max-height:275px !important; overflow-y:auto !important; overflow-x:hidden !important; padding:0 2px 2px 0 !important; scrollbar-width:thin; scrollbar-color:#33445c transparent; }
    .di-recent-scroll::-webkit-scrollbar { width:4px; }
    .di-recent-scroll::-webkit-scrollbar-thumb { background:#33445c; border-radius:8px; }
        /* Refined product sidebar */
    [data-testid="stSidebar"] { width:232px !important; min-width:232px !important; background:linear-gradient(180deg,#0a1220 0%,#0d1728 55%,#0b1322 100%); border-right:1px solid rgba(255,255,255,.07); }
    [data-testid="stSidebar"] > div:first-child { padding:18px 13px 16px 13px; }
    [data-testid="stSidebar"] .block-container { padding-top:.4rem; }
    .di-side-brand { display:flex; align-items:center; gap:10px; padding:4px 4px 16px; margin-bottom:12px; border-bottom:1px solid rgba(255,255,255,.08); }
    .di-side-logo { width:36px; height:36px; border-radius:10px; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#4f8cff,#2563eb); box-shadow:0 7px 18px rgba(37,99,235,.25); font-size:18px; flex:none; }
    .di-side-name { color:#fff; font-size:15px; line-height:1.12; font-weight:800; letter-spacing:-.2px; }
    .di-side-sub { color:#98a2b3; font-size:9.5px; margin-top:4px; }
    .di-side-kicker { color:#667085; font-size:8.5px; text-transform:uppercase; letter-spacing:.13em; font-weight:800; margin:13px 4px 6px; }
    .di-side-panel { border:1px solid rgba(255,255,255,.075); background:rgba(255,255,255,.028); border-radius:12px; padding:6px; margin-bottom:9px; }
    .di-side-row { display:flex; align-items:center; gap:8px; padding:8px 7px; border-radius:8px; color:#cbd5e1; font-size:10.5px; line-height:1.3; transition:background .2s ease, transform .2s ease; }
    .di-side-row.active { background:linear-gradient(90deg,rgba(59,130,246,.18),rgba(59,130,246,.07)); color:#fff; box-shadow:inset 2px 0 0 #60a5fa; }
    .di-side-dot { width:7px; height:7px; border-radius:50%; background:#475467; flex:none; }
    .di-side-dot.active { background:#60a5fa; box-shadow:0 0 0 4px rgba(96,165,250,.10); }
    .di-side-arrow { color:#667085; margin-left:auto; font-size:12px; }
    .di-side-status { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:11px; padding:9px 9px; border-radius:10px; background:rgba(255,255,255,.025); border:1px solid rgba(255,255,255,.07); }
    .di-side-status-label { color:#98a2b3; font-size:10px; }
    .di-side-status-value { color:#a7f3d0; font-size:10px; font-weight:800; }
    .di-side-hint { color:#667085; font-size:10px; line-height:1.45; margin:9px 5px 0; }
    [data-testid="stSidebar"] .stButton > button { min-height:36px !important; height:36px !important; border-radius:9px !important; background:transparent !important; border:1px solid transparent !important; font-size:11px !important; font-weight:650 !important; text-align:left !important; padding:3px 10px !important; margin:0 0 3px !important; display:flex !important; align-items:center !important; justify-content:flex-start !important; }
    [data-testid="stSidebar"] .stButton > button > div { width:100% !important; justify-content:flex-start !important; text-align:left !important; }
    [data-testid="stSidebar"] .stButton > button [data-testid="stMarkdownContainer"] { width:100% !important; text-align:left !important; }
    [data-testid="stSidebar"] .stButton > button p { width:100% !important; margin:0 !important; padding:0 !important; text-align:left !important; text-indent:0 !important; }
    [data-testid="stSidebar"] .stButton > button:hover { border-color:rgba(102,112,133,.45) !important; background:#172238 !important; transform:translateX(2px); }
    .di-page-enter { animation:diPageEnter .42s cubic-bezier(.2,.8,.2,1) both; }
    .di-info-card, .di-trust-card, .di-result-header, .di-card, .di-step { transition:transform .22s ease, box-shadow .22s ease, border-color .22s ease; }
    .di-info-card:hover, .di-trust-card:hover, .di-card:hover { transform:translateY(-2px); box-shadow:0 12px 30px rgba(16,24,40,.07); border-color:#d0d5dd; }
    .di-hero { position:relative; overflow:hidden; }
    .di-hero:after { content:""; position:absolute; width:260px; height:260px; right:-100px; top:-130px; border-radius:50%; background:rgba(255,255,255,.07); animation:diFloat 6s ease-in-out infinite; }
    @keyframes diFloat { 0%,100%{transform:translate3d(0,0,0)} 50%{transform:translate3d(-12px,10px,0)} }
    @keyframes diPageEnter { from { opacity:0; transform:translateY(7px); } to { opacity:1; transform:translateY(0); } }

    /* Final sidebar spacing polish */
    [data-testid="stSidebar"] > div:first-child { padding-top:4px !important; padding-bottom:12px !important; }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top:0 !important; }
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top:0 !important; }
    [data-testid="stSidebar"] .block-container { padding-top:0 !important; }
    .di-product-brand { padding-top:2px !important; padding-bottom:12px !important; }
    .di-product-rule { margin-bottom:12px !important; }

    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# SESSION STATE
# -----------------------------------------------------------------------------

for key, default in {
    "page": "upload",
    "result_dir": None,
    "result_name": None,
    "result_mode": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        st.error(f"Unable to read {path.name}: {exc}")
        return None


def pretty_label(value):
    return str(value).replace("_", " ").replace("-", " ").strip().title()


def nonempty(value):
    return value not in (None, "", [], {})


def scalar_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def read_text_file(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def find_page_images(result_dir):
    return sorted(
        result_dir.glob("page_*.png"),
        key=lambda p: int(p.stem.split("_")[1]) if p.stem.split("_")[1].isdigit() else 999999,
    )


def find_ocr_files(result_dir):
    return sorted(
        result_dir.glob("page_*_ocr.txt"),
        key=lambda p: int(p.stem.split("_")[1]) if p.stem.split("_")[1].isdigit() else 999999,
    )


def load_classification(result_dir):
    path = result_dir / "document_type.json"
    if not path.exists():
        return "UNKNOWN", {}
    data = load_json(path)
    if not isinstance(data, dict):
        return "UNKNOWN", {}
    value = data.get("document_type")
    if not value and isinstance(data.get("classification"), dict):
        value = data["classification"].get("document_type")
    return value or "UNKNOWN", data


def detect_amazon_document(result_dir):
    """Return True only for an invoice that is actually identified as Amazon.

    The previous implementation treated the mere presence of the word
    ``amazon`` as proof that the document was an Amazon invoice. That caused
    unrelated documents such as resumes mentioning Amazon services to be
    routed into the structured-invoice UI.

    Routing now requires both:
      1. the document classifier to identify the document as an invoice, and
      2. Amazon invoice evidence in the extracted source text.

    All other documents use the generic OCR/presentation workflow.
    """
    classification, _ = load_classification(result_dir)
    if str(classification).upper() != "INVOICE":
        return False

    evidence_parts = []
    for path in find_ocr_files(result_dir):
        evidence_parts.append(read_text_file(path))

    for name in ("invoice_data.json", "final_invoice_data.json", "generic_final.json", "generic_fields.json"):
        path = result_dir / name
        if path.exists():
            try:
                evidence_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    evidence = "\n".join(evidence_parts).lower()
    if "amazon" not in evidence:
        return False

    # Require invoice-related evidence as well, so a document that merely
    # mentions Amazon is not treated as an Amazon invoice.
    invoice_markers = (
        "invoice",
        "tax invoice",
        "invoice no",
        "invoice number",
        "sold by",
        "order number",
        "order id",
    )
    return any(marker in evidence for marker in invoice_markers)


def render_mapping_cards(data, columns=3):
    if not isinstance(data, dict):
        return
    values = [(key, value) for key, value in data.items() if nonempty(value)]
    if not values:
        return
    cols = st.columns(min(columns, len(values)))
    for index, (key, value) in enumerate(values):
        with cols[index % len(cols)]:
            st.markdown(
                f'<div class="di-info-card"><div class="di-info-label">{pretty_label(key)}</div>'
                f'<div class="di-info-value">{scalar_text(value)}</div></div>',
                unsafe_allow_html=True,
            )


def render_dynamic_table(records, title=None):
    if not records:
        return
    rows = []
    for index, record in enumerate(records, start=1):
        if isinstance(record, dict):
            row = {"#": index}
            for key, value in record.items():
                if nonempty(value):
                    row[pretty_label(key)] = scalar_text(value)
            rows.append(row)
        else:
            rows.append({"#": index, "Value": scalar_text(record)})
    if title:
        st.markdown(f'<div class="di-section-title">{title}</div>', unsafe_allow_html=True)
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_source_preview(result_dir):
    """Show a compact source preview without exposing pipeline/internal diagnostics."""
    images = find_page_images(result_dir)
    if not images:
        return

    st.markdown('<div class="di-section-title">Source document</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="di-section-sub">The original document remains available as the reference for the extracted result.</div>',
        unsafe_allow_html=True,
    )

    preview_col, _ = st.columns([0.62, 0.38])
    with preview_col:
        st.markdown('<div class="di-source-frame di-source-compact">', unsafe_allow_html=True)
        st.image(str(images[0]), width=560, caption="Original source · Page 1")
        st.markdown('</div>', unsafe_allow_html=True)

    if len(images) > 1:
        with st.expander(f"View other source pages · {len(images) - 1} more", expanded=False):
            cols = st.columns(min(2, len(images) - 1))
            for index, image in enumerate(images[1:], start=2):
                with cols[(index - 2) % len(cols)]:
                    st.image(str(image), use_container_width=True, caption=f"Source · Page {index}")

def render_source_evidence(result_dir):
    images = find_page_images(result_dir)
    ocr_files = find_ocr_files(result_dir)
    if images:
        with st.expander(f"🖼️ Source document — {len(images)} page(s)", expanded=False):
            for image in images:
                st.image(str(image), use_container_width=True, caption=image.name)
    if ocr_files:
        with st.expander(f"📝 OCR source text — {len(ocr_files)} page(s)", expanded=False):
            for path in ocr_files:
                text = read_text_file(path)
                st.markdown(f"**{path.name}**")
                st.text_area(path.name, text, height=260, label_visibility="collapsed")


def render_validation(result_dir):
    path = result_dir / "validation_report.json"
    if not path.exists():
        return
    validation = load_json(path)
    if not isinstance(validation, dict):
        return
    status = str(validation.get("overall_status", validation.get("status", "UNKNOWN")))
    upper = status.upper()
    if upper == "PASSED":
        st.markdown(f'<div class="di-status di-status-pass">✓ Validation completed — {status}</div>', unsafe_allow_html=True)
    elif upper in {"NEEDS_REVIEW", "REVIEW"}:
        st.markdown(f'<div class="di-status di-status-review">! Validation requires review — {status}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="di-status di-status-neutral">Validation status — {status}</div>', unsafe_allow_html=True)
    with st.expander("🔎 Validation details", expanded=False):
        st.json(validation)


def render_downloads(result_dir):
    candidates = [
        ("Integrated JSON", result_dir / "generic_final.json", "application/json"),
        ("Invoice JSON", result_dir / "final_invoice_data.json", "application/json"),
        ("Raw Invoice Data", result_dir / "invoice_data.json", "application/json"),
        ("CSV", result_dir / "invoice_output.csv", "text/csv"),
        ("Excel", result_dir / "invoice_output.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("Validation JSON", result_dir / "validation_report.json", "application/json"),
    ]
    available = [(label, path, mime) for label, path, mime in candidates if path.exists()]
    if not available:
        return
    st.markdown('<div class="di-section-title">Available outputs</div>', unsafe_allow_html=True)
    columns = st.columns(min(3, len(available)))
    for index, (label, path, mime) in enumerate(available):
        with columns[index % len(columns)]:
            st.download_button(
                f"⬇️ {label}",
                data=path.read_bytes(),
                file_name=path.name,
                mime=mime,
                use_container_width=True,
                key=f"download_{path.name}",
            )

# -----------------------------------------------------------------------------
# AMAZON / STRUCTURED INVOICE UI
# -----------------------------------------------------------------------------

def _first_value(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if nonempty(value):
            return value
    return None


def _render_field_group(title, data, fields, columns=3):
    if not isinstance(data, dict):
        return
    cards = []
    for label, keys in fields:
        value = _first_value(data, *keys)
        if nonempty(value):
            cards.append((label, value))
    if not cards:
        return
    st.markdown(f'<div class="di-section-title">{title}</div>', unsafe_allow_html=True)
    cols = st.columns(min(columns, len(cards)))
    for index, (label, value) in enumerate(cards):
        with cols[index % len(cols)]:
            st.markdown(
                f'<div class="di-info-card"><div class="di-info-label">{label}</div>'
                f'<div class="di-info-value">{scalar_text(value)}</div></div>',
                unsafe_allow_html=True,
            )


def _render_invoice_table(records, title, kind="product"):
    if not records:
        return
    rows = []
    for record in records:
        if not isinstance(record, dict):
            rows.append({"Description": scalar_text(record)})
            continue
        if kind == "product":
            preferred = [
                ("Description", ("description", "product_name", "item_description")),
                ("Quantity", ("quantity", "qty")),
                ("Unit price", ("unit_price", "unit_price_amount")),
                ("Net amount", ("net_amount", "taxable_amount", "line_amount")),
                ("Tax rate", ("tax_rate",)),
                ("Tax amount", ("tax_amount", "total_tax")),
                ("Total", ("total_amount", "line_total", "amount")),
                ("ASIN", ("asin",)),
                ("HSN", ("hsn", "hsn_code")),
            ]
        else:
            preferred = [
                ("Description", ("description", "charge_description", "name")),
                ("Charge", ("charge_type", "type")),
                ("Amount", ("net_amount", "unit_price", "amount")),
                ("Tax rate", ("tax_rate",)),
                ("Tax", ("tax_amount", "total_tax")),
                ("Total", ("total_amount", "line_total", "amount")),
            ]
        row = {}
        for label, keys in preferred:
            value = _first_value(record, *keys)
            if nonempty(value):
                row[label] = scalar_text(value)
        if row:
            rows.append(row)
    if rows:
        st.markdown(f'<div class="di-section-title">{title}</div>', unsafe_allow_html=True)
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_structured_document(document):
    """Render user-facing invoice information while preserving observed document structure."""
    if not isinstance(document, dict):
        return

    # Some invoice outputs store invoice/order metadata in dedicated nested
    # objects, while other outputs keep the values at the document root.
    # Read both forms so the UI displays values that were actually extracted.
    invoice = document.get("invoice") if isinstance(document.get("invoice"), dict) else {}
    order = document.get("order") if isinstance(document.get("order"), dict) else {}

    overview_fields = [
        ("Invoice number", (
            ("invoice_number", "invoice_no", "invoice_id"),
            invoice,
        )),
        ("Invoice date", (
            ("invoice_date", "date"),
            invoice,
        )),
        ("Order number", (
            ("order_number", "order_no", "order_id"),
            order,
        )),
        ("Order date", (
            ("order_date", "date"),
            order,
        )),
        ("Invoice details", (
            ("invoice_details", "details"),
            invoice,
        )),
    ]

    overview_cards = []
    for label, (keys, nested_data) in overview_fields:
        value = _first_value(nested_data, *keys)
        if not nonempty(value):
            value = _first_value(document, *keys)
        if nonempty(value):
            overview_cards.append((label, value))

    total_value = _first_value(document, "total_amount", "grand_total", "invoice_total")
    totals = document.get("totals") if isinstance(document.get("totals"), dict) else {}
    if not nonempty(total_value):
        total_value = _first_value(totals, "total_amount", "grand_total", "invoice_total")
    if nonempty(total_value):
        overview_cards.append(("Total amount", total_value))

    if overview_cards:
        st.markdown('<div class="di-section-title">Invoice overview</div>', unsafe_allow_html=True)
        cols = st.columns(min(3, len(overview_cards)))
        for index, (label, value) in enumerate(overview_cards):
            with cols[index % len(cols)]:
                st.markdown(
                    f'<div class="di-info-card"><div class="di-info-label">{label}</div>'
                    f'<div class="di-info-value">{scalar_text(value)}</div></div>',
                    unsafe_allow_html=True,
                )

    seller = document.get("seller")
    billing = document.get("billing")
    shipping = document.get("shipping")
    supply = document.get("supply_delivery") or document.get("supply")

    _render_field_group(
        "Seller",
        seller,
        [
            ("Name", ("name", "seller_name")),
            ("GSTIN", ("gstin", "gst_number", "gst")),
            ("PAN", ("pan", "pan_number")),
            ("Address", ("address",)),
        ],
        columns=2,
    )

    _render_field_group(
        "Customer",
        billing,
        [
            ("Name", ("name", "customer_name", "buyer_name")),
            ("Address", ("address",)),
            ("State code", ("state_code",)),
        ],
        columns=3,
    )

    shipping_fields = [
        ("Name", ("name", "customer_name", "buyer_name")),
        ("Address", ("address",)),
        ("State code", ("state_code",)),
    ]
    if isinstance(shipping, dict) and any(
        nonempty(_first_value(shipping, *keys)) for _, keys in shipping_fields
    ):
        _render_field_group("Shipping", shipping, shipping_fields, columns=3)

    if isinstance(supply, dict):
        _render_field_group(
            "Supply & delivery",
            supply,
            [
                ("Place of supply", ("place_of_supply", "supply_place")),
                ("Place of delivery", ("place_of_delivery", "delivery_place")),
            ],
            columns=2,
        )

    amount_words = (
        _first_value(document, "amount_in_words", "total_in_words")
        or _first_value(totals, "amount_in_words", "total_in_words")
    )
    if nonempty(total_value) or nonempty(amount_words):
        cards = []
        if nonempty(total_value):
            cards.append(("Total amount", total_value))
        if nonempty(amount_words):
            cards.append(("Amount in words", amount_words))
        st.markdown('<div class="di-section-title">Totals</div>', unsafe_allow_html=True)
        cols = st.columns(min(2, len(cards)))
        for index, (label, value) in enumerate(cards):
            with cols[index % len(cols)]:
                st.markdown(
                    f'<div class="di-info-card"><div class="di-info-label">{label}</div>'
                    f'<div class="di-info-value">{scalar_text(value)}</div></div>',
                    unsafe_allow_html=True,
                )

    payment = document.get("payment")
    if isinstance(payment, dict):
        _render_field_group(
            "Payment",
            payment,
            [
                ("Mode", ("mode", "payment_mode")),
                ("Transaction ID", ("transaction_id", "transaction", "reference")),
                ("Invoice value", ("invoice_value", "value", "amount")),
            ],
            columns=3,
        )

    products = document.get("products") or document.get("items") or []
    charges = document.get("charges") or []
    _render_invoice_table(products, "Items", "product")
    _render_invoice_table(charges, "Charges", "charge")


def render_amazon_results(result_dir, result_name):
    st.markdown('<div class="di-page-enter">', unsafe_allow_html=True)
    classification, classification_data = load_classification(result_dir)
    invoice_path = result_dir / "final_invoice_data.json"
    if not invoice_path.exists():
        invoice_path = result_dir / "invoice_data.json"

    data = load_json(invoice_path) if invoice_path.exists() else None
    documents = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(documents, list):
        documents = [data] if isinstance(data, dict) else []

    validation = load_json(result_dir / "validation_report.json") if (result_dir / "validation_report.json").exists() else {}

    st.markdown(
        f"""<div class="di-result-header">
        <div class="di-result-kicker">✦ AMAZON INVOICE · STRUCTURED EXTRACTION</div>
        <div class="di-result-title">Invoice ready for review</div>
        <div class="di-result-file">{result_name}</div>
        </div>""",
        unsafe_allow_html=True,
    )

    render_source_preview(result_dir)

    st.markdown('<div class="di-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="di-section-title">Extracted invoice</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="di-section-sub">Key information, items, charges, and payment details found in the document.</div>',
        unsafe_allow_html=True,
    )

    if not documents:
        st.error("Invoice processing completed, but no structured invoice document was found.")
    else:
        for index, document in enumerate(documents, start=1):
            page_label = document.get("source_page", document.get("page", index)) if isinstance(document, dict) else index
            with st.expander(f"Invoice section · Page {page_label}", expanded=True):
                render_structured_document(document)

    st.markdown('<div class="di-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="di-section-title">Review & technical details</div>', unsafe_allow_html=True)
    with st.expander("🔎 Validation details", expanded=False):
        if isinstance(validation, dict) and validation:
            st.json(validation)
        else:
            st.info("No validation report was produced.")

    with st.expander("🧠 Classification details", expanded=False):
        st.json(classification_data)

    with st.expander("🖼️ Source document evidence", expanded=False):
        for image in find_page_images(result_dir):
            st.image(str(image), use_container_width=True, caption=image.name)

    with st.expander(f"📝 OCR source text · {len(find_ocr_files(result_dir))} page(s)", expanded=False):
        for path in find_ocr_files(result_dir):
            st.markdown(f"**{path.name}**")
            st.text_area(path.name, read_text_file(path), height=240, label_visibility="collapsed")

    with st.expander("🧩 Complete invoice JSON", expanded=False):
        if data is not None:
            st.json(data)

    render_downloads(result_dir)

    with st.expander("🖥️ Processing log", expanded=False):
        for path in (result_dir / "processing_log.txt", result_dir / "pipeline.log"):
            if path.exists():
                st.code(read_text_file(path))
                break
        else:
            st.info("Pipeline console output was not stored as a file.")

    st.markdown('<div class="di-footer-note">Document Intelligence · Source-preserving OCR and invoice extraction demo</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# GENERIC / NON-AMAZON UI
# -----------------------------------------------------------------------------

def render_generic_text_page(result_dir):
    """Professional readable view for documents outside the supported invoice workflow."""
    st.markdown('<div class="di-page-enter">', unsafe_allow_html=True)
    classification, _ = load_classification(result_dir)
    ocr_files = find_ocr_files(result_dir)
    source_pages = len(find_page_images(result_dir))
    page_count = source_pages or len(ocr_files)
    display_type = classification if classification not in {"UNKNOWN", ""} else "General document"

    st.markdown(
        f'''<div class="di-result-header">
        <div class="di-result-kicker">✦ Document Intelligence · OCR Workflow</div>
        <div class="di-result-title">Document review</div>
        <div class="di-result-file">{st.session_state.result_name or result_dir.name}</div>
        </div>''',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""<div class=\"di-generic-type-card\">
            <div class=\"di-generic-type-label\">Document</div>
            <div class=\"di-generic-type-value\">{display_type}</div>
        </div>""",
        unsafe_allow_html=True,
    )

    render_source_preview(result_dir)

    st.markdown('<div class="di-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="di-section-title">Readable OCR</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="di-section-sub">The document was not forced into invoice fields. The OCR result is shown as read from the uploaded source.</div>',
        unsafe_allow_html=True,
    )

    if not ocr_files:
        st.warning("No OCR text was generated for this document.")
    else:
        for index, path in enumerate(ocr_files, start=1):
            text = read_text_file(path)
            with st.expander(f"Page {index}", expanded=True):
                if text.strip():
                    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    st.markdown(
                        f'<div class="di-ocr-readable">{safe_text}</div>',
                        unsafe_allow_html=True,
                    )
                    st.download_button(
                        "⬇️ Download OCR text",
                        data=text,
                        file_name=path.name,
                        mime="text/plain",
                        key=f"generic_download_{path.name}",
                    )
                else:
                    st.info("This page has no readable OCR text.")

    with st.expander("Document classification", expanded=False):
        classification, classification_data = load_classification(result_dir)
        st.write(f"Detected document type: **{classification}**")
        if classification_data:
            st.json(classification_data)

    render_source_evidence(result_dir)
    st.markdown('<div class="di-footer-note">Document Intelligence · Source-preserving OCR</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# PIPELINE
# -----------------------------------------------------------------------------

def safe_filename(name):
    return Path(name).name


def render_live_pipeline(container, current_index, detail, percent, failed=False):
    """Render an honest, easy-to-understand live pipeline without changing backend logic."""
    stages = [
        ("📤", "Upload", "Receive source"),
        ("🔎", "OCR", "Read document"),
        ("🧠", "Classify", "Identify type"),
        ("🧩", "Extract", "Capture information"),
        ("✓", "Show result", "Present what was found"),
    ]
    cards = []
    for index, (icon, label, sub) in enumerate(stages):
        if failed and index == current_index:
            state = "active"
            icon = "⚠️"
        elif index < current_index:
            state = "done"
            icon = "✓"
        elif index == current_index:
            state = "active"
        else:
            state = ""
        cards.append(
            f'<div class="di-stage {state}"><div class="di-stage-icon">{icon}</div>'
            f'<div class="di-stage-label">{label}</div><div class="di-stage-sub">{sub}</div></div>'
        )
    pct = max(0, min(100, int(percent)))
    html = f"""<div class="di-progress-shell">
      <div class="di-progress-head">
        <div><div class="di-progress-title"><span class="di-live-dot"></span>{detail}</div>
        <div class="di-progress-detail">The document is being processed step by step. No result is shown until processing is complete.</div></div>
        <div class="di-progress-percent">{pct}%</div>
      </div>
      <div class="di-progress-track"><div class="di-progress-fill" style="width:{pct}%"></div></div>
      <div class="di-stage-grid">{''.join(cards)}</div>
    </div>"""
    container.markdown(html, unsafe_allow_html=True)


def pipeline_state_from_line(line):
    """Map existing main.py console messages to presentation stages."""
    text = line.lower()
    if "preparing document working directory" in text or "working directory ready" in text:
        return (0, 5, "Preparing your document")
    if "step: ocr" in text or ("running:" in text and "pdf_ocr.py" in text):
        return (1, 18, "Reading the document with OCR")
    if "ocr completed" in text:
        return (1, 28, "OCR completed · identifying document type")
    if "step: document classification" in text:
        return (2, 36, "Identifying the document structure")
    if "document classification completed" in text:
        return (2, 42, "Document type identified")
    if "detected type" in text:
        return (3, 45, "Routing the document to the right workflow")
    if "step: field extraction" in text:
        return (3, 58, "Extracting invoice information")
    if "field extraction completed" in text:
        return (3, 64, "Invoice information extracted")
    if "step: table extraction" in text:
        return (3, 68, "Reading line items and charges")
    if "table extraction completed" in text:
        return (3, 73, "Line items identified")
    if "step: invoice integration" in text:
        return (3, 77, "Combining extracted information")
    if "invoice integration completed" in text:
        return (3, 81, "Structured result assembled")
    if "step: invoice validation" in text:
        return (4, 86, "Checking extracted values")
    if "invoice validation completed" in text:
        return (4, 91, "Validation completed")
    if "step: invoice export" in text:
        return (5, 95, "Preparing downloadable outputs")
    if "invoice export completed" in text:
        return (5, 99, "Outputs ready")
    if "non-invoice document handling" in text or "route         : generic" in text:
        return (5, 90, "Preparing readable OCR result")
    return None


def process_uploaded_file(uploaded_file):
    filename = safe_filename(uploaded_file.name)
    input_path = INPUT_DIR / filename
    result_dir = OUTPUT_DIR / Path(filename).stem

    try:
        input_path.write_bytes(uploaded_file.getbuffer())
    except Exception as exc:
        st.error(f"Unable to save uploaded file: {exc}")
        return

    if result_dir.exists():
        try:
            shutil.rmtree(result_dir)
        except Exception as exc:
            st.error(f"Could not clear previous result: {exc}")
            return

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    progress_box = st.empty()
    current_index, current_percent = 0, 4
    render_live_pipeline(progress_box, current_index, "Starting document processing", current_percent)

    lines = []
    process = None
    started = time.perf_counter()

    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(PROJECT_ROOT / "main.py"), str(input_path)],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )

        for raw_line in iter(process.stdout.readline, ""):
            line = raw_line.rstrip()
            if not line:
                continue
            lines.append(line)
            state = pipeline_state_from_line(line)
            if state:
                current_index, current_percent, detail = state
                render_live_pipeline(progress_box, current_index, detail, current_percent)

        process.stdout.close()
        return_code = process.wait()
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
        render_live_pipeline(progress_box, current_index, "Processing could not be completed", current_percent, failed=True)
        st.error(str(exc))
        return

    elapsed = time.perf_counter() - started

    if return_code != 0:
        render_live_pipeline(progress_box, current_index, "Processing needs attention", current_percent, failed=True)
        with st.expander("Processing details", expanded=False):
            st.code("\n".join(lines))
        return

    if not result_dir.exists():
        render_live_pipeline(progress_box, current_index, "No output was produced", current_percent, failed=True)
        st.error("Output directory was not created.")
        return

    progress_box.markdown(
        f"""<div class="di-progress-shell">
          <div class="di-complete-banner">✓ Processing complete · Your document is ready to review</div>
          <div class="di-file-ready"><div class="di-file-ready-icon">📄</div>
          <div><strong>{filename}</strong><div class="di-progress-detail">Processed in {elapsed:.1f} seconds · OCR and document workflow completed successfully.</div></div></div>
        </div>""",
        unsafe_allow_html=True,
    )

    with st.expander("Processing details", expanded=False):
        st.code("\n".join(lines))

    is_amazon = detect_amazon_document(result_dir)
    st.session_state.result_dir = str(result_dir)
    st.session_state.result_name = filename
    st.session_state.result_mode = "amazon" if is_amazon else "generic"
    st.session_state.page = "results"
    time.sleep(0.35)
    st.rerun()

# -----------------------------------------------------------------------------
# UPLOAD PAGE
# -----------------------------------------------------------------------------

def render_upload_page():
    st.markdown('<div class="di-page-enter">', unsafe_allow_html=True)
    st.markdown(
        """<div class="di-topbar">
            <div class="di-topbar-title">Document Intelligence Workspace</div>
            <div class="di-topbar-state"><span class="dot"></span> System ready</div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        """<div class="di-hero-v2">
            <div class="eyebrow">✦ Document processing workspace</div>
            <h1>Document intelligence, built to read what you upload.</h1>
            <p>Amazon invoices can be processed through the structured invoice workflow. Other documents are read with OCR and presented based on the information found in the source.</p>
            <div class="di-hero-points">
                <span class="di-hero-point">✓ Amazon invoice → structured data</span>
                <span class="di-hero-point">✓ Other invoices → extracted information</span>
                <span class="di-hero-point">✓ Other documents → OCR</span>
                <span class="di-hero-point">✓ Source preserved</span>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="di-section-title" style="margin-top:8px">Upload your document</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="di-section-sub">Start with an invoice or upload any supported document. The workflow adapts after the document is read.</div>',
        unsafe_allow_html=True,
    )
    uploaded_file = st.file_uploader(
        "Drop your document here or choose a file",
        type=SUPPORTED_EXTENSIONS,
        help="Upload an invoice or any supported document/image.",
    )
    if uploaded_file is not None:
        if st.button("Process document  →", type="primary", use_container_width=True):
            process_uploaded_file(uploaded_file)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown(
        """<div class="di-flow-card">
            <div class="di-flow-head">
                <div><div class="di-flow-title">How it works</div><div class="di-flow-sub">One simple path from source document to usable result.</div></div>
                <div class="di-flow-badge">PROCESSING FLOW</div>
            </div>
            <div class="di-flow-line">
                <div class="di-flow-item"><div class="di-flow-num">01</div><div class="di-flow-label">Upload</div><div class="di-flow-copy">Choose a document</div></div>
                <div class="di-flow-item"><div class="di-flow-num">02</div><div class="di-flow-label">OCR</div><div class="di-flow-copy">Read the source</div></div>
                <div class="di-flow-item"><div class="di-flow-num">03</div><div class="di-flow-label">Classify</div><div class="di-flow-copy">Understand the type</div></div>
                <div class="di-flow-item"><div class="di-flow-num">04</div><div class="di-flow-label">Extract</div><div class="di-flow-copy">Structure when supported</div></div>
                <div class="di-flow-item"><div class="di-flow-num">05</div><div class="di-flow-label">Show result</div><div class="di-flow-copy">Present what was found</div></div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
    if st.session_state.result_dir and Path(st.session_state.result_dir).exists():
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown(
            f"""<div class="di-card"><div class="di-card-title">Continue where you left off</div><div class="di-card-sub">Previous result · {st.session_state.result_name or Path(st.session_state.result_dir).name}</div></div>""",
            unsafe_allow_html=True,
        )
        if st.button("Open previous result  →", use_container_width=True):
            st.session_state.page = "results"
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# SIDEBAR — PRODUCT WORKSPACE
# -----------------------------------------------------------------------------

def _recent_documents(limit=5):
    items = []
    if not OUTPUT_DIR.exists():
        return items
    for directory in OUTPUT_DIR.iterdir():
        if not directory.is_dir():
            continue
        try:
            stamp = directory.stat().st_mtime
        except OSError:
            continue
        filename = directory.name
        source_name = filename
        meta_path = directory / "source_filename.txt"
        if meta_path.exists():
            try:
                value = meta_path.read_text(encoding="utf-8", errors="ignore").strip()
                if value:
                    source_name = value
            except OSError:
                pass
        classification, _ = load_classification(directory)
        if detect_amazon_document(directory):
            kind = "Invoice"
        elif classification == "RECEIPT":
            kind = "Receipt"
        elif classification in {"GENERAL_DOCUMENT", "UNKNOWN"}:
            kind = "Document"
        else:
            kind = str(classification or "Document").replace("_", " ").title()
        items.append((stamp, source_name, kind, directory))
    items.sort(key=lambda x: x[0], reverse=True)
    return items[:limit]

with st.sidebar:
    viewing_result = st.session_state.page == "results"
    has_result = bool(st.session_state.result_dir and Path(st.session_state.result_dir).exists())

    st.markdown(
        """<div class="di-product-brand">
            <div class="di-product-logo">✦</div>
            <div>
                <div class="di-product-name">Document Intelligence</div>
                <div class="di-product-tagline">Read · Understand · Extract</div>
            </div>
        </div>
        <div class="di-product-rule"></div>""",
        unsafe_allow_html=True,
    )

    if st.button("＋  New document", type="primary", use_container_width=True, key="side_new_product"): 
        st.session_state.page = "upload"
        st.session_state.result_mode = None
        st.session_state.result_dir = None
        st.session_state.result_name = None
        st.rerun()

    if has_result:
        current_name = st.session_state.result_name or Path(st.session_state.result_dir).name
        st.markdown(
            f"""<div class="di-current-doc">
                <div class="di-current-label">CURRENT DOCUMENT</div>
                <div class="di-current-name">{current_name}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    recent = _recent_documents()
    recent_count = len(recent)
    st.markdown(
        f'<div class="di-recent-heading"><div class="di-recent-title">RECENT DOCUMENTS</div><div class="di-recent-count">{recent_count}</div></div>',
        unsafe_allow_html=True,
    )
    if recent:
        st.markdown('<div class="di-recent-scroll">', unsafe_allow_html=True)
        for index, (_, name, kind, directory) in enumerate(recent):
            label = f"▣  {name}"
            if st.button(label, use_container_width=True, key=f"recent_doc_{index}_{directory.name}"):
                st.session_state.result_dir = str(directory)
                st.session_state.result_name = name
                st.session_state.page = "results"
                st.session_state.result_mode = "amazon" if detect_amazon_document(directory) else "generic"
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="di-empty-recent">Your processed documents<br>will appear here.</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="di-side-spacer"></div>', unsafe_allow_html=True)
    status_text = "Reviewing document" if viewing_result else "Ready for a document"
    st.markdown(
        f"""<div class="di-engine">
            <div class="di-engine-dot"></div>
            <div><div class="di-engine-title">Processing engine</div><div class="di-engine-status">{status_text}</div></div>
        </div>
        <div class="di-side-footer">Source-preserving workflow<br>Adaptive OCR · Document-aware routing</div>""",
        unsafe_allow_html=True,
    )

    if viewing_result:
        if st.button("←  Back to upload", use_container_width=True, key="side_back_product"):
            st.session_state.page = "upload"
            st.session_state.result_mode = None
            st.rerun()

# -----------------------------------------------------------------------------
# ROUTER
# -----------------------------------------------------------------------------

if st.session_state.page == "results" and st.session_state.result_dir:
    result_dir = Path(st.session_state.result_dir)
    if not result_dir.exists():
        st.session_state.page = "upload"
        st.session_state.result_dir = None
        st.rerun()

    mode = st.session_state.result_mode
    if mode not in {"amazon", "generic"}:
        mode = "amazon" if detect_amazon_document(result_dir) else "generic"
        st.session_state.result_mode = mode

    if mode == "amazon":
        render_amazon_results(result_dir, st.session_state.result_name or result_dir.name)
    else:
        render_generic_text_page(result_dir)
else:
    render_upload_page()