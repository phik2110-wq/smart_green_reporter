import ast
import datetime
from io import BytesIO
import base64
import json
import os
import re
import sqlite3
import uuid

import folium
import requests
import streamlit as st
from PIL import Image, ImageOps
from streamlit_folium import st_folium


# =========================================================
# 1. CẤU HÌNH
# =========================================================

st.set_page_config(
    page_title="Urban GreenEye AI",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_FILE = "reports.db"
UPLOAD_DIR = "uploaded_images"
CLEANUP_DIR = "cleanup_images"

CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
MAX_IMAGE_MB = 10
SPAM_RETENTION_DAYS = 7

DEFAULT_LAT = 10.9570
DEFAULT_LNG = 106.8427


def get_setting(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, default)


CF_ACCOUNT_ID = get_setting("CLOUDFLARE_ACCOUNT_ID")
CF_AUTH_TOKEN = get_setting("CLOUDFLARE_AUTH_TOKEN") or get_setting("CLOUDFLARE_API_TOKEN")
ADMIN_PIN = get_setting("ADMIN_PIN", "1234")
TEAM_PIN = get_setting("TEAM_PIN", "1234")
STAFF_PIN = get_setting("STAFF_PIN", "1234")


def unlock_with_pin(pin: str, session_key: str) -> bool:
    """Admin PIN có toàn quyền; PIN riêng chỉ mở đúng khu vực."""
    if pin == ADMIN_PIN:
        st.session_state["admin_full_access"] = True
        st.session_state[session_key] = True
        return True
    if session_key == "admin_unlocked":
        ok = pin == ADMIN_PIN
    elif session_key == "team_unlocked":
        ok = pin == TEAM_PIN
    elif session_key == "staff_unlocked":
        ok = pin == STAFF_PIN
    else:
        ok = False
    st.session_state[session_key] = ok
    return ok


os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANUP_DIR, exist_ok=True)


# =========================================================
# 2. CSS
# =========================================================

st.markdown(
    """
<style>
/* ===== TOÀN BỘ GIAO DIỆN ===== */
.stApp {
    background: #edf7ef;
    color: #111111 !important;
}

.stApp *,
.stApp p,
.stApp span,
.stApp label,
.stApp div,
.stApp h1,
.stApp h2,
.stApp h3,
.stApp h4,
.stApp h5,
.stApp h6,
.stApp button,
.stApp input,
.stApp textarea,
.stApp [data-baseweb="select"] * {
    color: #111111 !important;
}

.block-container {
    max-width: 1450px;
    padding-top: 1rem;
    padding-bottom: 2rem;
}

/* ===== SIDEBAR ===== */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #e5f6e8 0%, #d2eed8 55%, #c5e6cc 100%);
    border-right: 1px solid #b5dcbc;
}

[data-testid="stSidebar"] > div:first-child {
    padding: 18px 14px 22px 14px;
}

[data-testid="stSidebar"] * {
    color: #111111 !important;
}

.sidebar-brand {
    background: rgba(255,255,255,.78);
    border: 1px solid #a9d5b1;
    border-radius: 20px;
    padding: 15px;
    margin-bottom: 14px;
    box-shadow: 0 5px 16px rgba(35, 105, 50, .08);
}

.sidebar-brand-row {
    display: flex;
    align-items: center;
    gap: 12px;
}

.sidebar-logo {
    width: 46px;
    height: 46px;
    min-width: 46px;
    border-radius: 14px;
    background: #176b35;
    color: #111111 !important;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    font-weight: 900;
    letter-spacing: -1px;
}

.sidebar-brand-title {
    font-size: 1.02rem;
    font-weight: 900;
    line-height: 1.2;
}

.sidebar-brand-sub {
    font-size: .76rem;
    margin-top: 4px;
    color: #4b6250 !important;
}

.sidebar-live {
    display: flex;
    align-items: center;
    gap: 8px;
    background: #f7fcf8;
    border: 1px solid #b8dfbf;
    border-radius: 12px;
    padding: 9px 11px;
    margin: 10px 0 14px;
    font-size: .8rem;
    font-weight: 700;
}

.sidebar-live-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #27a745;
    box-shadow: 0 0 0 4px #d9f1de;
}

.sidebar-section-title {
    font-size: .72rem;
    font-weight: 900;
    letter-spacing: .12em;
    margin: 14px 4px 7px;
    color: #3e5d46 !important;
    text-transform: uppercase;
}

.sidebar-info {
    background: rgba(255,255,255,.55);
    border: 1px solid #b6ddbd;
    border-radius: 15px;
    padding: 11px 12px;
    margin-top: 14px;
    font-size: .76rem;
    line-height: 1.5;
}

/* Streamlit radio menu */
[data-testid="stSidebar"] [data-testid="stRadio"] > label {
    font-weight: 900 !important;
    margin-bottom: 7px;
}

[data-testid="stSidebar"] [data-testid="stRadio"] > div {
    gap: 6px;
}

[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {
    gap: 6px !important;
}

[data-testid="stSidebar"] [data-testid="stRadio"] label {
    background: rgba(255,255,255,.42);
    border: 1px solid transparent;
    border-radius: 13px;
    padding: 9px 10px;
    transition: .15s ease;
}

[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,.85);
    border-color: #a7d3af;
}

[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: #ffffff;
    border-color: #8dc59a;
    box-shadow: 0 4px 12px rgba(31, 102, 47, .10);
}

/* Ẩn radio tròn để menu giống nút điều hướng */
[data-testid="stSidebar"] [data-testid="stRadio"] input {
    opacity: 0;
    width: 0;
    height: 0;
}

/* ===== HERO ===== */
.hero {
    background: linear-gradient(135deg, #dff3e3, #f7fcf8);
    color: #111111 !important;
    border: 1px solid #afd8b7;
    border-radius: 24px;
    padding: 30px;
    margin-bottom: 20px;
    box-shadow: 0 10px 30px rgba(20, 100, 45, .10);
}

.hero h1,
.hero p {
    color: #111111 !important;
}

.hero h1 {
    margin: 0;
    font-size: 2.2rem;
}

.hero p {
    margin: 8px 0 0;
    opacity: .85;
}

/* ===== CARD ===== */
.card {
    background: white;
    border: 1px solid #b9dfc0;
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow: 0 6px 20px rgba(30, 100, 45, .06);
}

.stat-card {
    background: white;
    border: 1px solid #b9dfc0;
    border-radius: 18px;
    padding: 18px;
    text-align: center;
    box-shadow: 0 5px 16px rgba(30, 100, 45, .05);
}

.stat-number {
    font-size: 1.8rem;
    font-weight: 800;
    color: #111111 !important;
}

.stat-label {
    color: #111111 !important;
    font-size: .9rem;
}

.report-title {
    color: #111111 !important;
    font-weight: 800;
    font-size: 1.15rem;
}

.status {
    display: inline-block;
    padding: 5px 12px;
    border-radius: 999px;
    background: #e3f4e7;
    color: #111111 !important;
    font-weight: 700;
    font-size: .85rem;
}

/* ===== Ô NHẬP LIỆU: NỀN TỐI, CHỮ TRẮNG ===== */
.stApp input,
.stApp textarea,
.stApp [data-baseweb="input"],
.stApp [data-baseweb="textarea"],
.stApp [data-baseweb="input"] > div,
.stApp [data-baseweb="textarea"] > div {
    background-color: #252631 !important;
    color: #ffffff !important;
    border-color: #3d4050 !important;
}

.stApp input,
.stApp textarea {
    -webkit-text-fill-color: #ffffff !important;
    caret-color: #ffffff !important;
}

.stApp input::placeholder,
.stApp textarea::placeholder {
    color: #c8cbd3 !important;
    opacity: 1 !important;
}

.stApp [data-baseweb="input"] input,
.stApp [data-baseweb="textarea"] textarea {
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

.stApp [data-baseweb="select"] > div {
    background-color: #252631 !important;
    color: #ffffff !important;
}

.stApp [data-baseweb="select"] span {
    color: #ffffff !important;
}

div[data-testid="stFileUploader"] {
    border: 1px dashed #75b982;
    border-radius: 16px;
    padding: 8px;
    background: #f7fcf8;
}

/* File uploader: nền tối và tất cả chữ bên trong màu trắng */
div[data-testid="stFileUploader"] button,
div[data-testid="stFileUploader"] button *,
div[data-testid="stFileUploader"] [data-testid="stBaseButton-secondary"],
div[data-testid="stFileUploader"] [data-testid="stBaseButton-secondary"] *,
div[data-testid="stFileUploader"] small,
div[data-testid="stFileUploader"] span,
div[data-testid="stFileUploader"] p {
    color: #ffffff !important;
}

div[data-testid="stFileUploader"] button {
    background: #252631 !important;
    border: 1px solid #4a4d5b !important;
}

div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] *,
div[data-testid="stFileUploader"] section * {
    color: #ffffff !important;
}

/* Không để rule màu đen chung đè lên chữ nhập trong các ô tối */
.stApp input,
.stApp textarea,
.stApp input:focus,
.stApp textarea:focus {
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

.stButton > button {
    background: #111111 !important;
    border: 1px solid #111111 !important;
    border-radius: 12px;
    font-weight: 800;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

.stButton > button:hover,
.stButton > button:focus {
    background: #000000 !important;
    border-color: #000000 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

.stButton > button[kind="primary"] {
    background: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

/* ===== SIDEBAR COLLAPSE / MỞ-ĐÓNG ===== */
/* Nút mở/đóng sidebar: nền đen, biểu tượng trắng */
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button {
    background: #111111 !important;
    border: 1px solid #000000 !important;
    border-radius: 10px !important;
    color: #ffffff !important;
    box-shadow: 0 3px 10px rgba(0,0,0,.18) !important;
}

[data-testid="stSidebarCollapseButton"] button:hover,
[data-testid="stSidebarCollapsedControl"] button:hover {
    background: #000000 !important;
}

[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg {
    color: #ffffff !important;
    fill: #ffffff !important;
    stroke: #ffffff !important;
}

[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button {
    font-size: 0 !important;
}

[data-testid="stSidebarCollapseButton"] button::before {
    content: "‹";
    font-size: 26px;
    font-weight: 900;
    line-height: 1;
    color: #ffffff !important;
}

[data-testid="stSidebarCollapsedControl"] button::before {
    content: "›";
    font-size: 26px;
    font-weight: 900;
    line-height: 1;
    color: #ffffff !important;
}

[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg {
    display: none !important;
}

/* ===== CÀI ĐẶT AI: Ô NHẬP ĐEN, CHỮ TRẮNG ===== */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border: 1px solid #000000 !important;
}

[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {
    color: #d1d5db !important;
    -webkit-text-fill-color: #d1d5db !important;
    opacity: 1 !important;
}

/* ===== MOBILE ===== */
@media (max-width: 768px) {
    .block-container {
        padding-left: .75rem;
        padding-right: .75rem;
        padding-top: .5rem;
    }

    .hero {
        padding: 22px 18px;
        border-radius: 18px;
    }

    .hero h1 {
        font-size: 1.65rem;
    }

    .card {
        padding: 15px;
        border-radius: 16px;
    }

    .stat-card {
        padding: 13px 8px;
    }

    .stat-number {
        font-size: 1.4rem;
    }

    [data-testid="stSidebar"] > div:first-child {
        padding: 12px 10px 18px 10px;
    }
}

    /* ===== INPUT / SELECT / TEXTAREA: focus = black + white ===== */
    .stTextInput input,
    .stTextArea textarea,
    .stNumberInput input,
    .stDateInput input,
    .stTimeInput input {
        background-color: #ffffff !important;
        color: #111111 !important;
        -webkit-text-fill-color: #111111 !important;
        border: 1px solid #94a3b8 !important;
    }

    .stTextInput input:focus,
    .stTextArea textarea:focus,
    .stNumberInput input:focus,
    .stDateInput input:focus,
    .stTimeInput input:focus {
        background-color: #000000 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border-color: #000000 !important;
        box-shadow: 0 0 0 1px #000000 !important;
    }

    .stTextInput input::placeholder,
    .stTextArea textarea::placeholder,
    .stNumberInput input::placeholder,
    .stDateInput input::placeholder,
    .stTimeInput input::placeholder {
        color: #64748b !important;
        -webkit-text-fill-color: #64748b !important;
    }

    .stTextInput input:focus::placeholder,
    .stTextArea textarea:focus::placeholder,
    .stNumberInput input:focus::placeholder,
    .stDateInput input:focus::placeholder,
    .stTimeInput input:focus::placeholder {
        color: #d1d5db !important;
        -webkit-text-fill-color: #d1d5db !important;
    }

    /* ===== SELECTBOX / MULTISELECT ===== */
    div[data-baseweb="select"] > div {
        background-color: #ffffff !important;
        color: #111111 !important;
        border-color: #94a3b8 !important;
    }

    div[data-baseweb="select"]:focus-within > div {
        background-color: #000000 !important;
        color: #ffffff !important;
        border-color: #000000 !important;
    }

    div[data-baseweb="select"] *,
    div[data-baseweb="select"] input {
        color: #111111 !important;
        -webkit-text-fill-color: #111111 !important;
    }

    div[data-baseweb="select"]:focus-within *,
    div[data-baseweb="select"]:focus-within input {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }

    /* Dropdown menu */
    div[role="listbox"],
    div[role="option"] {
        background-color: #ffffff !important;
        color: #111111 !important;
    }

    div[role="option"]:hover,
    div[role="option"][aria-selected="true"] {
        background-color: #000000 !important;
        color: #ffffff !important;
    }

    /* ===== BUTTONS ===== */
    .stButton > button,
    .stFormSubmitButton > button,
    button[kind="secondary"],
    button[kind="primary"] {
        background-color: #000000 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: 1px solid #000000 !important;
    }

    .stButton > button:hover,
    .stFormSubmitButton > button:hover,
    button[kind="secondary"]:hover,
    button[kind="primary"]:hover {
        background-color: #222222 !important;
        color: #ffffff !important;
        border-color: #000000 !important;
    }

    /* ===== FILE UPLOADER ===== */
    div[data-testid="stFileUploader"] section {
        background-color: #ffffff !important;
        border: 1px solid #94a3b8 !important;
    }

    div[data-testid="stFileUploader"] section *,
    div[data-testid="stFileUploader"] small,
    div[data-testid="stFileUploader"] span,
    div[data-testid="stFileUploader"] p {
        color: #111111 !important;
        -webkit-text-fill-color: #111111 !important;
    }

    div[data-testid="stFileUploader"] button {
        background-color: #000000 !important;
        color: #ffffff !important;
        border: 1px solid #000000 !important;
    }

    div[data-testid="stFileUploader"] button * {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }

    /* ===== CHECKBOX / RADIO ===== */
    .stCheckbox label,
    .stRadio label,
    .stToggle label {
        color: #111111 !important;
    }

    /* ===== SIDEBAR RADIO / SELECT ===== */
    section[data-testid="stSidebar"] .stRadio label,
    section[data-testid="stSidebar"] .stSelectbox label {
        color: #111111 !important;
    }

    /* ===== EXPANDER ===== */
    details summary {
        color: #111111 !important;
    }

    
    /* Sidebar open/close control */
    button[data-testid="stSidebarCollapseButton"],
    button[data-testid="stSidebarCollapsedControl"] {
        background: #000000 !important;
        color: #ffffff !important;
        border: 1px solid #000000 !important;
    }

    button[data-testid="stSidebarCollapseButton"] *,
    button[data-testid="stSidebarCollapsedControl"] * {
        color: #ffffff !important;
        fill: #ffffff !important;
    }

    
/* =========================================================
   FINAL GLOBAL THEME OVERRIDE — ÁP DỤNG TOÀN BỘ CÁC TRANG
   Ô nhập: ĐEN + CHỮ TRẮNG | Nút: ĐEN + CHỮ TRẮNG
   ========================================================= */
.stApp div[data-baseweb="input"],
.stApp div[data-baseweb="textarea"],
.stApp div[data-baseweb="input"] > div,
.stApp div[data-baseweb="textarea"] > div,
.stApp input,
.stApp textarea {
    background-color: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: #333333 !important;
}

.stApp input::placeholder,
.stApp textarea::placeholder {
    color: #cbd5e1 !important;
    -webkit-text-fill-color: #cbd5e1 !important;
    opacity: 1 !important;
}

.stApp input:focus,
.stApp textarea:focus,
.stApp div[data-baseweb="input"]:focus-within,
.stApp div[data-baseweb="textarea"]:focus-within {
    background-color: #000000 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: #000000 !important;
    box-shadow: 0 0 0 1px #000000 !important;
}

/* Password eye / show-hide button: tách màu khỏi nền ô nhập */
.stApp div[data-baseweb="input"] button {
    background-color: #374151 !important;
    border: 1px solid #4b5563 !important;
    color: #ffffff !important;
    border-radius: 0 8px 8px 0 !important;
}
.stApp div[data-baseweb="input"] button:hover {
    background-color: #4b5563 !important;
}
.stApp div[data-baseweb="input"] button svg {
    color: #ffffff !important;
    fill: #ffffff !important;
    stroke: #ffffff !important;
}

/* Selectbox / multiselect toàn hệ thống */
.stApp div[data-baseweb="select"] > div {
    background-color: #111111 !important;
    color: #ffffff !important;
    border-color: #333333 !important;
}
.stApp div[data-baseweb="select"] * {
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}
.stApp div[data-baseweb="select"]:focus-within > div {
    background-color: #000000 !important;
    border-color: #000000 !important;
}

/* Menu dropdown */
.stApp div[role="listbox"],
.stApp div[role="option"] {
    background-color: #ffffff !important;
    color: #111111 !important;
}
.stApp div[role="option"] * {
    color: #111111 !important;
    -webkit-text-fill-color: #111111 !important;
}
.stApp div[role="option"]:hover,
.stApp div[role="option"][aria-selected="true"] {
    background-color: #000000 !important;
}
.stApp div[role="option"]:hover * {
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

/* Tất cả button trên toàn app */
.stApp button,
.stButton > button,
.stFormSubmitButton > button {
    background-color: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: #111111 !important;
}
.stApp button:hover,
.stButton > button:hover,
.stFormSubmitButton > button:hover {
    background-color: #000000 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: #000000 !important;
}

/* File uploader button */
.stApp div[data-testid="stFileUploader"] button,
.stApp div[data-testid="stFileUploader"] button * {
    background-color: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
}

/* Model AI / code box: luôn dễ đọc, không bị global color đè */
.stApp div[data-testid="stCodeBlock"],
.stApp div[data-testid="stCodeBlock"] pre,
.stApp div[data-testid="stCodeBlock"] code,
.stApp pre,
.stApp pre code {
    background-color: #f1f5f9 !important;
    color: #111111 !important;
    -webkit-text-fill-color: #111111 !important;
    border-color: #cbd5e1 !important;
}

/* Checkbox / radio text */
.stApp [data-testid="stCheckbox"] label,
.stApp [data-testid="stRadio"] label,
.stApp [data-testid="stToggle"] label {
    color: #111111 !important;
    -webkit-text-fill-color: #111111 !important;
}

/* Sidebar mở/đóng */
button[data-testid="stSidebarCollapseButton"],
button[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button {
    background-color: #111111 !important;
    color: #ffffff !important;
    border: 1px solid #000000 !important;
}
button[data-testid="stSidebarCollapseButton"] svg,
button[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg {
    color: #ffffff !important;
    fill: #ffffff !important;
    stroke: #ffffff !important;
}


/* ===== THỐNG KÊ: GỌN TRÊN ĐIỆN THOẠI ===== */
.stats-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 8px 0 18px;
}
.stats-grid .stat-card {
    margin: 0;
    min-height: 118px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    padding: 12px;
}
.stats-grid .stat-icon {
    font-size: 1.45rem;
    line-height: 1;
    margin-bottom: 5px;
}
.stats-grid .stat-number {
    font-size: 1.55rem;
    line-height: 1.05;
}
.stats-grid .stat-label {
    font-size: .82rem;
    margin-top: 5px;
}

/* Khung từng báo cáo trong khu vực dọn dẹp */
.cleanup-report-card {
    background: #ffffff;
    border: 1px solid #b9dfc0;
    border-radius: 18px;
    padding: 14px;
    margin: 12px 0;
    box-shadow: 0 4px 14px rgba(30,100,45,.06);
}
.cleanup-report-card .cleanup-report-head {
    background: #edf7ef;
    border: 1px solid #c7e5cd;
    border-radius: 12px;
    padding: 9px 12px;
    margin-bottom: 12px;
    font-weight: 800;
}

@media (max-width: 700px) {
    .block-container {
        padding-left: .7rem !important;
        padding-right: .7rem !important;
        padding-top: .55rem !important;
    }

    .stats-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 12px;
    }

    .stats-grid .stat-card {
        min-height: 86px;
        padding: 8px 5px;
        border-radius: 14px;
    }

    .stats-grid .stat-icon {
        font-size: 1.05rem;
        margin-bottom: 3px;
    }

    .stats-grid .stat-number {
        font-size: 1.25rem;
    }

    .stats-grid .stat-label {
        font-size: .70rem;
        line-height: 1.15;
        margin-top: 3px;
    }

    .cleanup-report-card {
        padding: 10px;
        border-radius: 14px;
        margin: 9px 0;
    }

    .cleanup-report-card .cleanup-report-head {
        padding: 7px 9px;
        margin-bottom: 8px;
        font-size: .88rem;
    }
}

</style>
""",
    unsafe_allow_html=True,
)

# =========================================================
# 3. DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _table_columns(conn, table_name):
    return {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_points_table(conn, table_name):
    """Tự sửa bảng điểm cũ nếu schema không còn đúng."""
    cols = _table_columns(conn, table_name)
    if not cols:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table_name} (name TEXT PRIMARY KEY, points INTEGER DEFAULT 0)"
        )
        return

    required = {"name", "points"}
    if required.issubset(cols.keys()):
        return

    # Giữ lại dữ liệu cũ nếu tìm được cột tên/điểm tương ứng.
    old_name = next((c for c in ("name", "username", "user_name", "team_name") if c in cols), None)
    old_points = next((c for c in ("points", "score", "total_points") if c in cols), None)
    temp = f"{table_name}_new"
    conn.execute(f"DROP TABLE IF EXISTS {temp}")
    conn.execute(f"CREATE TABLE {temp} (name TEXT PRIMARY KEY, points INTEGER DEFAULT 0)")

    if old_name and old_points:
        rows = conn.execute(f"SELECT {old_name}, {old_points} FROM {table_name}").fetchall()
        for r in rows:
            if r[0] is not None:
                try:
                    pts = int(r[1] or 0)
                except Exception:
                    pts = 0
                conn.execute(
                    f"INSERT OR REPLACE INTO {temp}(name, points) VALUES (?, ?)",
                    (str(r[0]), pts),
                )

    conn.execute(f"DROP TABLE {table_name}")
    conn.execute(f"ALTER TABLE {temp} RENAME TO {table_name}")


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            reporter_name TEXT,
            image_path TEXT,
            description TEXT,
            location TEXT,
            latitude REAL,
            longitude REAL,
            status TEXT DEFAULT 'Đang xử lý',
            ai_result TEXT,
            ai_raw_json TEXT,
            ai_analyzed INTEGER DEFAULT 0,
            ai_error TEXT,
            assigned_team TEXT,
            cleanup_image_path TEXT,
            cleanup_note TEXT,
            cleaned_at TEXT,
            created_at TEXT
        )
        """
    )

    # Bổ sung cột còn thiếu cho database cũ.
    columns = _table_columns(conn, "reports")
    extra_columns = {
        "reporter_name": "TEXT",
        "image_path": "TEXT",
        "description": "TEXT",
        "location": "TEXT",
        "latitude": "REAL",
        "longitude": "REAL",
        "status": "TEXT DEFAULT 'Đang xử lý'",
        "ai_result": "TEXT",
        "ai_raw_json": "TEXT",
        "ai_analyzed": "INTEGER DEFAULT 0",
        "ai_error": "TEXT",
        "assigned_team": "TEXT",
        "cleanup_image_path": "TEXT",
        "cleanup_note": "TEXT",
        "cleaned_at": "TEXT",
        "created_at": "TEXT",
    }
    for column, dtype in extra_columns.items():
        if column not in columns:
            cur.execute(f"ALTER TABLE reports ADD COLUMN {column} {dtype}")

    _ensure_points_table(conn, "user_points")
    _ensure_points_table(conn, "team_points")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at)")

    conn.commit()
    conn.close()


def cleanup_expired_spam():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, image_path, cleanup_image_path
        FROM reports
        WHERE status = 'Spam/Từ chối'
          AND datetime(created_at) < datetime('now', ?)
        """,
        (f"-{SPAM_RETENTION_DAYS} days",),
    ).fetchall()

    for row in rows:
        for path in [row["image_path"], row["cleanup_image_path"]]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        conn.execute("DELETE FROM reports WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()


init_db()
cleanup_expired_spam()


# =========================================================
# 4. CLOUDFLARE WORKERS AI
# =========================================================

def cloudflare_configured():
    return bool(CF_ACCOUNT_ID and CF_AUTH_TOKEN)


def cloudflare_url():
    return (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    )


def cloudflare_agree():
    """Gửi yêu cầu agree một lần khi quản trị viên chủ động bấm nút."""
    if not cloudflare_configured():
        return False, "Thiếu CLOUDFLARE_ACCOUNT_ID hoặc CLOUDFLARE_AUTH_TOKEN/CLOUDFLARE_API_TOKEN."

    try:
        response = requests.post(
            cloudflare_url(),
            headers={
                "Authorization": f"Bearer {CF_AUTH_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"prompt": "agree"},
            timeout=60,
        )

        data = response.json()

        if response.ok and data.get("success"):
            return True, "Đã gửi xác nhận model thành công."

        return False, json.dumps(data, ensure_ascii=False)

    except Exception as exc:
        return False, str(exc)


def prepare_image(image_bytes: bytes):
    if len(image_bytes) > MAX_IMAGE_MB * 1024 * 1024:
        raise ValueError(f"Ảnh vượt quá {MAX_IMAGE_MB} MB.")

    image = Image.open(BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")

    max_side = 1400
    if max(image.size) > max_side:
        scale = max_side / max(image.size)
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.LANCZOS,
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()


def image_to_data_uri(image_bytes: bytes):
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def extract_json_object(text: str):
    """
    Đọc kết quả Vision kể cả khi model trả:
    - JSON chuẩn với dấu ngoặc kép
    - Python dict với dấu nháy đơn và True/False
    - JSON/Python dict nằm trong code fence
    """
    if isinstance(text, dict):
        return text

    text = str(text or "").strip()
    if not text:
        raise ValueError("AI không trả về nội dung.")

    cleaned = re.sub(r"```(?:json|python)?\s*", "", text, flags=re.I)
    cleaned = cleaned.replace("```", "").strip()

    candidates = [cleaned]

    # Lấy từng object cân bằng ngoặc để xử lý trường hợp model thêm lời văn.
    objects = []
    depth = 0
    in_string = False
    quote_char = None
    escaped = False
    start_pos = None

    for i, ch in enumerate(cleaned):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote_char:
                in_string = False
                quote_char = None
            continue

        if ch in ("\"", "'"):
            in_string = True
            quote_char = ch
        elif ch == "{":
            if depth == 0:
                start_pos = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start_pos is not None:
                objects.append(cleaned[start_pos:i + 1])
                start_pos = None

    # Thử object đầy đủ trước, rồi từng object được tìm thấy.
    candidates.extend(objects)

    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        # 1. JSON chuẩn
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except Exception:
            pass

        # 2. Model Llama đôi khi trả Python dict: {'key': True}
        # ast.literal_eval an toàn hơn eval và đọc được True/False/None.
        try:
            value = ast.literal_eval(candidate)
            if isinstance(value, dict):
                return value
        except Exception:
            pass

    raise ValueError(
        "AI đã trả về nội dung nhưng không đúng JSON/Python dict. "
        f"Phản hồi nhận được: {text[:1200]}"
    )

def normalize_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        return value.strip().lower() in {
            "true", "1", "yes", "có", "co", "đúng", "true.",
        }

    return False


def _cloudflare_request(prompt: str, image_bytes: bytes):
    payload = {
        "prompt": prompt,
        "image": image_to_data_uri(image_bytes),
        "max_tokens": 500,
        "temperature": 0,
    }

    try:
        response = requests.post(
            cloudflare_url(),
            headers={
                "Authorization": f"Bearer {CF_AUTH_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Không kết nối được Cloudflare: {exc}") from exc

    try:
        data = response.json()
    except Exception:
        data = {}

    if not response.ok or not data.get("success"):
        detail = (
            json.dumps(data, ensure_ascii=False)[:2500]
            if data else response.text[:2500]
        )
        raise RuntimeError(
            f"Cloudflare HTTP {response.status_code}: {detail}"
        )

    result = data.get("result", {})
    if isinstance(result, dict):
        text = (
            result.get("response")
            or result.get("text")
            or result.get("output")
            or result.get("generated_text")
        )
        if text is None:
            text = json.dumps(result, ensure_ascii=False)
    else:
        text = str(result)

    return str(text), data


def _normalize_ai_result(parsed):
    if not isinstance(parsed, dict):
        raise ValueError("Kết quả AI không phải object JSON.")

    parsed["contains_waste"] = normalize_bool(
        parsed.get("contains_waste")
    )
    parsed["is_waste_amount_sufficient"] = normalize_bool(
        parsed.get("is_waste_amount_sufficient")
    )

    for key in [
        "natural_report", "waste_type", "severity",
        "visual_evidence", "spam_reason", "dispatch_plan",
    ]:
        parsed[key] = str(parsed.get(key, "") or "").strip()

    return parsed


def analyze_image_with_cloudflare(image_bytes: bytes):
    if not cloudflare_configured():
        raise RuntimeError(
            "Chưa cấu hình CLOUDFLARE_ACCOUNT_ID và "
            "CLOUDFLARE_AUTH_TOKEN/CLOUDFLARE_API_TOKEN."
        )

    prompt = """
Bạn là AI kiểm duyệt ảnh rác thải đô thị.

Hãy nhìn trực tiếp vào ảnh và quyết định có rác thải nhìn thấy rõ hay không.
Chân dung, selfie, ảnh người, ảnh nhóm, ảnh thẻ hoặc khuôn mặt KHÔNG phải rác.
Nếu không thấy rác rõ ràng hoặc không chắc chắn thì trả contains_waste=false.
Không được suy đoán vật thể không nhìn thấy.

QUAN TRỌNG: Phản hồi phải là MỘT JSON OBJECT DUY NHẤT.
Không markdown, không ```json, không lời mở đầu, không lời kết.
Dùng dấu ngoặc kép chuẩn JSON, KHÔNG dùng dấu nháy đơn kiểu Python.
Ký tự đầu tiên phải là { và ký tự cuối cùng phải là }.
Dùng đúng 8 khóa dưới đây.

{
  "contains_waste": false,
  "is_waste_amount_sufficient": false,
  "natural_report": "Nhận xét tự nhiên bằng tiếng Việt.",
  "waste_type": "Loại rác nếu có, nếu không thì chuỗi rỗng.",
  "severity": "Mức độ nếu có, nếu không thì chuỗi rỗng.",
  "visual_evidence": "Những gì thực sự nhìn thấy trong ảnh.",
  "spam_reason": "Lý do không hợp lệ nếu không có rác, nếu hợp lệ thì chuỗi rỗng.",
  "dispatch_plan": "Phương án xử lý do AI tự đề xuất nếu có rác, nếu không thì chuỗi rỗng."
}

Không dùng câu xử lý cố định. dispatch_plan phải dựa trên đúng những gì nhìn thấy.
"""

    text, raw_data = _cloudflare_request(prompt, image_bytes)

    try:
        parsed = extract_json_object(text)
        return _normalize_ai_result(parsed), raw_data
    except Exception as first_error:
        # Model đôi khi trả lời bằng văn bản dù đã yêu cầu JSON. Gửi lại một
        # lần với prompt cực ngắn để chuẩn hóa, thay vì làm mất báo cáo.
        repair_prompt = f"""
Hãy phân tích lại chính ảnh này. Chỉ trả về JSON object hợp lệ, không markdown.
Dùng dấu ngoặc kép chuẩn JSON, KHÔNG dùng dấu nháy đơn kiểu Python.
Không thấy rác rõ ràng hoặc không chắc chắn => contains_waste=false.
Người/chân dung/selfie không phải rác.

JSON bắt buộc có đúng các khóa:
contains_waste, is_waste_amount_sufficient, natural_report, waste_type, severity, visual_evidence, spam_reason, dispatch_plan.

Lần trả lời trước (chỉ để tham khảo, không tin tuyệt đối):
{text[:1200]}
"""

        try:
            repaired_text, repaired_raw = _cloudflare_request(
                repair_prompt, image_bytes
            )
            parsed = extract_json_object(repaired_text)
            return _normalize_ai_result(parsed), repaired_raw
        except Exception as second_error:
            raise RuntimeError(
                "AI đã nhận được ảnh nhưng không trả về JSON hợp lệ sau 2 lần thử. "
                f"Lần 1: {first_error}; Lần 2: {second_error}"
            ) from second_error


# =========================================================
# 5. REPORT FUNCTIONS
# =========================================================

def insert_report(
    reporter_name,
    image_path,
    description,
    location,
    latitude,
    longitude,
):
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    conn = get_db()

    # Database cũ có thể dùng INTEGER PRIMARY KEY.
    # Database mới dùng TEXT UUID. Tự nhận diện để tránh "datatype mismatch".
    id_info = conn.execute("PRAGMA table_info(reports)").fetchall()
    id_column = next((r for r in id_info if r["name"] == "id"), None)

    if id_column and "INT" in (id_column["type"] or "").upper() and id_column["pk"] == 1:
        cur = conn.execute(
            """
            INSERT INTO reports (
                reporter_name, image_path, description, location,
                latitude, longitude, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'Đang xử lý', ?)
            """,
            (
                reporter_name, image_path, description, location,
                latitude, longitude, created_at,
            ),
        )
        report_id = str(cur.lastrowid)
    else:
        report_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO reports (
                id, reporter_name, image_path, description, location,
                latitude, longitude, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Đang xử lý', ?)
            """,
            (
                report_id, reporter_name, image_path, description, location,
                latitude, longitude, created_at,
            ),
        )

    conn.commit()
    conn.close()
    return report_id


def save_ai_result(report_id, parsed, raw_data):
    valid = (
        parsed.get("contains_waste", False)
        and parsed.get("is_waste_amount_sufficient", False)
    )

    status = "Đã duyệt" if valid else "Spam/Từ chối"

    ai_result = parsed.get("natural_report", "")

    conn = get_db()
    conn.execute(
        """
        UPDATE reports
        SET status = ?,
            ai_result = ?,
            ai_raw_json = ?,
            ai_analyzed = 1,
            ai_error = NULL
        WHERE id = ?
        """,
        (
            status,
            ai_result,
            json.dumps(
                parsed,
                ensure_ascii=False,
                indent=2,
            ),
            report_id,
        ),
    )
    conn.commit()
    conn.close()

    return status


def save_ai_error(report_id, error_text):
    conn = get_db()
    conn.execute(
        """
        UPDATE reports
        SET status = 'Lỗi AI',
            ai_analyzed = 0,
            ai_error = ?
        WHERE id = ?
        """,
        (error_text, report_id),
    )
    conn.commit()
    conn.close()


def get_report(report_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    conn.close()
    return row


def get_reports(status=None):
    conn = get_db()

    if status:
        rows = conn.execute(
            """
            SELECT * FROM reports
            WHERE status = ?
            ORDER BY datetime(created_at) DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM reports
            ORDER BY datetime(created_at) DESC
            """
        ).fetchall()

    conn.close()
    return rows


def delete_report(report_id):
    row = get_report(report_id)

    if not row:
        return

    for path in [row["image_path"], row["cleanup_image_path"]]:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    conn = get_db()
    conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    conn.commit()
    conn.close()


def add_team_points(name, points):
    if not name.strip():
        return

    conn = get_db()
    conn.execute(
        """
        INSERT INTO team_points(name, points)
        VALUES (?, ?)
        ON CONFLICT(name)
        DO UPDATE SET points = points + excluded.points
        """,
        (name.strip(), points),
    )
    conn.commit()
    conn.close()


def add_user_points(name, points):
    if not name.strip():
        return

    conn = get_db()
    conn.execute(
        """
        INSERT INTO user_points(name, points)
        VALUES (?, ?)
        ON CONFLICT(name)
        DO UPDATE SET points = points + excluded.points
        """,
        (name.strip(), points),
    )
    conn.commit()
    conn.close()


def parse_location_coordinates(location):
    if not location:
        return None, None

    patterns = [
        r"(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)",
        r"lat(?:itude)?\s*[:=]\s*(-?\d+(?:\.\d+)?).*?"
        r"lon(?:gitude)?\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, location, re.I | re.S)
        if match:
            try:
                return float(match.group(1)), float(match.group(2))
            except Exception:
                pass

    return None, None


# =========================================================
# 6. UI HELPERS
# =========================================================

def show_header():
    st.markdown(
        """
        <div class="hero">
            <h1>🌱 Urban GreenEye AI</h1>
            <p>Clean City • Green Future — AI tự động phát hiện và xử lý phản ánh rác thải.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_stats():
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    approved = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE status = 'Đã duyệt'"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE status IN ('Đang xử lý', 'Lỗi AI')"
    ).fetchone()[0]
    cleaned = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE status = 'Đã dọn'"
    ).fetchone()[0]

    conn.close()

    stats = [
        ("📋", total, "Tổng báo cáo"),
        ("🤖", approved, "AI xác nhận"),
        ("⏳", pending, "Đang xử lý"),
        ("🧹", cleaned, "Đã dọn"),
    ]

    cards = "".join(
        f"""<div class=\"stat-card\">
            <div class=\"stat-icon\">{icon}</div>
            <div class=\"stat-number\">{number}</div>
            <div class=\"stat-label\">{label}</div>
        </div>"""
        for icon, number, label in stats
    )

    st.markdown(
        f'<div class="stats-grid">{cards}</div>',
        unsafe_allow_html=True,
    )


def show_task_map(row):
    lat = row["latitude"]
    lng = row["longitude"]

    if lat is None or lng is None:
        lat, lng = parse_location_coordinates(row["location"])

    if lat is None or lng is None:
        st.info("Báo cáo này chưa có tọa độ bản đồ.")
        return

    fmap = folium.Map(
        location=[lat, lng],
        zoom_start=16,
        control_scale=True,
    )

    folium.Marker(
        [lat, lng],
        tooltip="📍 Vị trí nhiệm vụ",
        popup=folium.Popup(
            f"""
            <b>Vị trí báo cáo</b><br>
            {row["location"] or "Không có địa chỉ"}
            """,
            max_width=320,
        ),
        icon=folium.Icon(color="red", icon="trash", prefix="fa"),
    ).add_to(fmap)

    st_folium(
        fmap,
        width=None,
        height=330,
        returned_objects=[],
        key=f"task_map_{row['id']}",
    )


def show_report_card(row):
    st.markdown('<div class="card">', unsafe_allow_html=True)

    st.markdown(
        f'<div class="report-title">📋 Báo cáo {str(row["id"])[:8]}</div>',
        unsafe_allow_html=True,
    )

    st.write(f"**Người gửi:** {row['reporter_name'] or 'Ẩn danh'}")
    st.write(f"**Vị trí:** {row['location'] or 'Chưa có'}")
    st.write(f"**Mô tả:** {row['description'] or 'Không có'}")

    status = row["status"] or "Không xác định"
    st.markdown(
        f'<span class="status">{status}</span>',
        unsafe_allow_html=True,
    )

    if row["ai_result"]:
        st.info(f"🤖 {row['ai_result']}")

    if row["image_path"] and os.path.exists(row["image_path"]):
        st.image(row["image_path"], use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)


# =========================================================
# 7. TRANG GỬI BÁO CÁO
# =========================================================

def page_report():
    st.subheader("📷 Gửi báo cáo môi trường")

    with st.container(border=True):
        name = st.text_input(
            "Tên người báo cáo",
            placeholder="Nhập tên hoặc biệt danh",
        )

        uploaded = st.file_uploader(
            "Ảnh hiện trường",
            type=["jpg", "jpeg", "png", "webp"],
            help=f"Tối đa {MAX_IMAGE_MB} MB.",
        )

        description = st.text_area(
            "Mô tả",
            placeholder="Ví dụ: Có nhiều túi rác bên lề đường...",
        )

        st.markdown("### 📍 Chọn vị trí trên bản đồ")

        fmap = folium.Map(
            location=[DEFAULT_LAT, DEFAULT_LNG],
            zoom_start=12,
            control_scale=True,
        )

        map_result = st_folium(
            fmap,
            width=None,
            height=360,
            returned_objects=["last_clicked"],
            key="citizen_location_map",
        )

        latitude = None
        longitude = None

        if map_result and map_result.get("last_clicked"):
            latitude = map_result["last_clicked"].get("lat")
            longitude = map_result["last_clicked"].get("lng")

        if latitude is not None and longitude is not None:
            st.success(
                f"📍 Đã chọn: {latitude:.6f}, {longitude:.6f}"
            )

        location = st.text_input(
            "Địa chỉ / ghi chú vị trí",
            placeholder="Ví dụ: Đường..., phường..., TP...",
        )

        submit = st.button(
            "🚀 Gửi báo cáo & AI phân tích ngay",
            type="primary",
            use_container_width=True,
        )

    if submit:
        if not uploaded:
            st.error("Vui lòng tải ảnh lên.")
            return

        try:
            raw = uploaded.getvalue()
            prepared = prepare_image(raw)

            filename = f"{uuid.uuid4().hex}.jpg"
            image_path = os.path.join(UPLOAD_DIR, filename)

            with open(image_path, "wb") as file:
                file.write(prepared)

            report_id = insert_report(
                reporter_name=name.strip() or "Ẩn danh",
                image_path=image_path,
                description=description.strip(),
                location=location.strip(),
                latitude=latitude,
                longitude=longitude,
            )

            # Xác nhận đã gửi ngay sau khi DB nhận báo cáo.
            st.success(
                f"✅ Gửi báo cáo thành công! Mã báo cáo: {str(report_id)[:12]}"
            )

            # AI chạy ngay sau khi báo cáo được lưu.
            if not cloudflare_configured():
                error = (
                    "Chưa cấu hình Cloudflare Workers AI. "
                    "Hãy kiểm tra CLOUDFLARE_ACCOUNT_ID và "
                    "CLOUDFLARE_AUTH_TOKEN/CLOUDFLARE_API_TOKEN."
                )
                save_ai_error(report_id, error)
                st.warning("⚠️ Báo cáo đã lưu nhưng AI chưa được cấu hình.")
                return

            with st.spinner("🤖 AI đang phân tích ảnh..."):
                try:
                    parsed, raw_ai = analyze_image_with_cloudflare(prepared)
                    status = save_ai_result(report_id, parsed, raw_ai)
                except Exception as ai_exc:
                    save_ai_error(report_id, str(ai_exc))
                    st.error(f"❌ AI chưa phân tích được: {ai_exc}")
                    st.info("Báo cáo vẫn được lưu. Quản trị viên có thể phân tích lại trong mục Cài đặt AI.")
                    return

            if status == "Đã duyệt":
                add_user_points(name.strip() or "Ẩn danh", 5)
                st.success("🤖 AI đã xác nhận đây là báo cáo rác hợp lệ.")
            else:
                st.warning("⚠️ AI xác định ảnh không đủ bằng chứng về rác nên báo cáo được đưa vào Spam/Từ chối.")

        except Exception as exc:
            st.error(f"❌ Không thể lưu báo cáo: {exc}")


# =========================================================
# 8. ĐỘI DỌN DẸP
# =========================================================

def page_cleanup_team():
    st.subheader("🧹 Đội dọn dẹp nhận nhiệm vụ")

    pin = st.text_input(
        "Mã PIN đội dọn dẹp",
        type="password",
        key="team_pin_input",
    )

    if st.button(
        "→ Vào khu vực đội dọn dẹp",
        key="team_login",
        use_container_width=True,
    ):
        if unlock_with_pin(pin, "team_unlocked"):
            st.success("Đã mở quyền truy cập.")
        else:
            st.error("Mã PIN không đúng.")

    if (
        not st.session_state.get("team_unlocked", False)
        and not st.session_state.get("admin_full_access", False)
    ):
        st.info("Nhập mã PIN rồi bấm nút để vào.")
        return

    if st.session_state.get("admin_full_access", False):
        st.caption("🔐 Bạn đang truy cập bằng quyền quản trị toàn quyền.")

    team_name = st.text_input(
        "Tên đội / thành viên",
        placeholder="Ví dụ: Đội Môi Trường A3",
        key="cleanup_team_name",
    )

    # -----------------------------------------------------
    # NHIỆM VỤ CHỜ NHẬN
    # -----------------------------------------------------
    approved = get_reports("Đã duyệt")

    if approved:
        with st.container(border=True):
            st.markdown("### 📌 NHIỆM VỤ CHỜ NHẬN")
            st.caption(f"Có {len(approved)} nhiệm vụ đang chờ đội dọn dẹp nhận.")

            for row in approved:
                with st.container(border=True):
                    cols = st.columns([1, 1])

                    with cols[0]:
                        st.markdown(f"**📋 Báo cáo:** `{str(row['id'])[:8]}`")
                        st.write(f"📍 {row['location'] or 'Chưa có địa chỉ'}")
                        st.write(f"📝 {row['description'] or 'Không có mô tả'}")

                        if row["ai_raw_json"]:
                            try:
                                ai = json.loads(row["ai_raw_json"])
                                st.write(f"🗑️ **Loại rác:** {ai.get('waste_type', '')}")
                                st.write(f"⚠️ **Mức độ:** {ai.get('severity', '')}")
                                st.write(f"🚚 **Phương án:** {ai.get('dispatch_plan', '')}")
                            except Exception:
                                pass

                        if st.button(
                            "📌 Nhận nhiệm vụ",
                            key=f"assign_{row['id']}",
                            use_container_width=True,
                        ):
                            if not team_name.strip():
                                st.error("Nhập tên đội trước.")
                            else:
                                conn = get_db()
                                conn.execute(
                                    """
                                    UPDATE reports
                                    SET assigned_team = ?, status = 'Đang dọn'
                                    WHERE id = ? AND status = 'Đã duyệt'
                                    """,
                                    (team_name.strip(), row["id"]),
                                )
                                changed = conn.total_changes
                                conn.commit()
                                conn.close()

                                if changed:
                                    st.success(
                                        f"Đã nhận nhiệm vụ #{str(row['id'])[:8]}."
                                    )
                                st.rerun()

                    with cols[1]:
                        if row["image_path"] and os.path.exists(row["image_path"]):
                            st.image(
                                row["image_path"],
                                caption="Ảnh hiện trường",
                                use_container_width=True,
                            )

                    st.markdown("#### 📍 Vị trí trên bản đồ")
                    show_task_map(row)
    else:
        st.info("Hiện chưa có nhiệm vụ mới đang chờ nhận.")

    # -----------------------------------------------------
    # NHIỆM VỤ ĐÃ NHẬN — BÁO CÁO SAU DỌN DẸP
    # -----------------------------------------------------
    active = get_reports("Đang dọn")

    with st.container(border=True):
        st.markdown("### 🧹 BÁO CÁO SAU DỌN DẸP")
        st.caption(
            "Các nhiệm vụ đã nhận sẽ xuất hiện tại đây. "
            "Tải ảnh sau khi dọn và xác nhận hoàn thành."
        )

        if not active:
            st.info(
                "Chưa có nhiệm vụ đang dọn. Hãy bấm 'Nhận nhiệm vụ' ở phía trên."
            )
        else:
            st.success(f"Bạn đang có {len(active)} nhiệm vụ cần hoàn thành.")

            for row in active:
                with st.container(border=True):
                    st.markdown(
                        f"### 📋 Báo cáo `{str(row['id'])[:8]}`"
                    )
                    st.caption(
                        f"👷 Đội phụ trách: {row['assigned_team'] or 'Chưa xác định'}  "
                        f"• 📍 {row['location'] or 'Chưa có vị trí'}"
                    )

                    cols = st.columns([1, 1])

                    with cols[0]:
                        if row["image_path"] and os.path.exists(row["image_path"]):
                            st.image(
                                row["image_path"],
                                caption="Ảnh trước khi dọn",
                                use_container_width=True,
                            )

                    with cols[1]:
                        st.markdown("**📤 Ảnh sau khi dọn**")
                        cleanup_photo = st.file_uploader(
                            "Tải ảnh hiện trường sau khi dọn",
                            type=["jpg", "jpeg", "png", "webp"],
                            key=f"cleanup_photo_{row['id']}",
                        )

                        cleanup_note = st.text_area(
                            "📝 Ghi chú hoàn thành",
                            placeholder="Ví dụ: Đã thu gom toàn bộ rác và vệ sinh khu vực.",
                            key=f"cleanup_note_{row['id']}",
                        )

                        if st.button(
                            "✅ Xác nhận đã dọn xong",
                            key=f"finish_{row['id']}",
                            type="primary",
                            use_container_width=True,
                        ):
                            if not cleanup_photo:
                                st.error("Cần tải ảnh sau khi dọn.")
                            else:
                                try:
                                    prepared = prepare_image(cleanup_photo.getvalue())
                                    filename = f"{uuid.uuid4().hex}.jpg"
                                    cleanup_path = os.path.join(
                                        CLEANUP_DIR,
                                        filename,
                                    )

                                    with open(cleanup_path, "wb") as file:
                                        file.write(prepared)

                                    conn = get_db()
                                    conn.execute(
                                        """
                                        UPDATE reports
                                        SET status = 'Đã dọn',
                                            cleanup_image_path = ?,
                                            cleanup_note = ?,
                                            cleaned_at = ?
                                        WHERE id = ?
                                        """,
                                        (
                                            cleanup_path,
                                            cleanup_note.strip(),
                                            datetime.datetime.now().isoformat(
                                                timespec="seconds"
                                            ),
                                            row["id"],
                                        ),
                                    )
                                    conn.commit()
                                    conn.close()

                                    add_team_points(
                                        row["assigned_team"] or team_name,
                                        10,
                                    )

                                    st.success(
                                        f"Đã hoàn thành báo cáo #{str(row['id'])[:8]}."
                                    )
                                    st.rerun()

                                except Exception as exc:
                                    st.error(f"Lỗi: {exc}")

                    st.markdown("#### 📍 Vị trí nhiệm vụ")
                    show_task_map(row)


# =========================================================
# 9. ĐÃ DỌN
# =========================================================

def page_completed():
    st.subheader("✅ Danh sách đã dọn")

    rows = get_reports("Đã dọn")

    if not rows:
        st.info("Chưa có nhiệm vụ nào hoàn thành.")
        return

    for row in rows:
        with st.container(border=True):
            cols = st.columns(2)

            with cols[0]:
                st.markdown("**Ảnh trước khi dọn**")
                if row["image_path"] and os.path.exists(row["image_path"]):
                    st.image(
                        row["image_path"],
                        use_container_width=True,
                    )

            with cols[1]:
                st.markdown("**Ảnh sau khi dọn**")
                if (
                    row["cleanup_image_path"]
                    and os.path.exists(row["cleanup_image_path"])
                ):
                    st.image(
                        row["cleanup_image_path"],
                        use_container_width=True,
                    )

            st.write(
                f"📍 **Vị trí:** {row['location'] or 'Chưa có'}"
            )
            st.write(
                f"🧹 **Đội:** {row['assigned_team'] or 'Chưa có'}"
            )
            st.write(
                f"📝 **Ghi chú:** {row['cleanup_note'] or 'Không có'}"
            )

            show_task_map(row)


# =========================================================
# 10. BẢNG XẾP HẠNG
# =========================================================

def _read_points_table(table_name):
    conn = get_db()
    try:
        # Schema đã được init_db kiểm tra, nhưng vẫn giữ fallback để app không sập.
        cols = _table_columns(conn, table_name)
        if "name" not in cols or "points" not in cols:
            return []
        return conn.execute(
            f"SELECT name, points FROM {table_name} ORDER BY points DESC, name ASC LIMIT 50"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def page_leaderboard():
    st.subheader("🏆 Bảng xếp hạng tích điểm")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 👤 Người dân")
        users = _read_points_table("user_points")
        if not users:
            st.info("Chưa có người dân nào được cộng điểm.")
        else:
            for index, row in enumerate(users, 1):
                st.write(f"**{index}. {row['name']}** — {row['points']} điểm")

    with col2:
        st.markdown("### 🧹 Đội dọn dẹp")
        teams = _read_points_table("team_points")
        if not teams:
            st.info("Chưa có đội dọn dẹp nào được cộng điểm.")
        else:
            for index, row in enumerate(teams, 1):
                st.write(f"**{index}. {row['name']}** — {row['points']} điểm")


# =========================================================
# 11. SPAM
# =========================================================

def page_spam():
    st.subheader("🛡️ Báo cáo Spam & Xóa")

    pin = st.text_input(
        "PIN nhân viên",
        type="password",
        key="staff_pin_input",
    )
    if st.button("→ Vào khu vực Spam", key="staff_login", use_container_width=True):
        if unlock_with_pin(pin, "staff_unlocked"):
            st.success("Đã mở quyền truy cập.")
        else:
            st.error("Mã PIN không đúng.")

    if not st.session_state.get("staff_unlocked", False) and not st.session_state.get("admin_full_access", False):
        st.info("Nhập PIN rồi bấm nút để vào.")
        return

    if st.session_state.get("admin_full_access", False):
        st.caption("🔐 Bạn đang truy cập bằng quyền quản trị toàn quyền.")

    rows = get_reports("Spam/Từ chối")

    st.caption(
        f"Báo cáo Spam/Từ chối sẽ tự động xóa sau "
        f"{SPAM_RETENTION_DAYS} ngày, gồm cả ảnh và dữ liệu."
    )

    if not rows:
        st.success("Không có báo cáo Spam.")
        return

    if st.button(
        "🗑️ Xóa toàn bộ Spam ngay",
        type="secondary",
    ):
        for row in rows:
            delete_report(row["id"])
        st.success("Đã xóa toàn bộ Spam.")
        st.rerun()

    for row in rows:
        with st.container(border=True):
            st.write(f"**ID:** {row['id']}")
            st.write(
                f"**Người gửi:** {row['reporter_name']}"
            )
            st.write(
                f"**Ngày:** {row['created_at']}"
            )

            if row["ai_result"]:
                st.write(f"🤖 {row['ai_result']}")

            if row["ai_raw_json"]:
                try:
                    ai = json.loads(row["ai_raw_json"])
                    if ai.get("spam_reason"):
                        st.warning(
                            f"**Lý do AI:** {ai['spam_reason']}"
                        )
                except Exception:
                    pass

            if row["image_path"] and os.path.exists(row["image_path"]):
                st.image(
                    row["image_path"],
                    use_container_width=True,
                )

            if st.button(
                "🗑️ Xóa báo cáo này",
                key=f"delete_spam_{row['id']}",
            ):
                delete_report(row["id"])
                st.rerun()


# =========================================================
# 12. ADMIN / CÀI ĐẶT AI
# =========================================================

def page_admin():
    st.subheader("⚙️ Reset & Cài đặt AI")

    pin = st.text_input(
        "PIN quản trị viên",
        type="password",
        key="admin_pin_input",
    )
    if st.button("→ Vào khu vực quản trị", key="admin_login", use_container_width=True):
        if unlock_with_pin(pin, "admin_unlocked"):
            st.success("Đã mở toàn quyền quản trị.")
        else:
            st.error("Mã PIN không đúng.")

    if not st.session_state.get("admin_unlocked", False) and not st.session_state.get("admin_full_access", False):
        st.info("Nhập PIN rồi bấm nút để vào.")
        return

    st.success("🔐 Quyền quản trị: TOÀN QUYỀN")

    if cloudflare_configured():
        st.success("🟢 Cloudflare Workers AI đã được cấu hình.")
    else:
        st.error(
            "🔴 Chưa có CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_AUTH_TOKEN/CLOUDFLARE_API_TOKEN."
        )

    st.markdown("### 🤖 Model AI Vision")
    st.code(
        f"Model: {CF_MODEL}",
        language="text",
    )

    st.markdown("### 🔐 Xác nhận model Cloudflare")

    if st.button(
        "Tôi đã đọc và đồng ý điều khoản model → gửi agree",
        use_container_width=True,
    ):
        ok, message = cloudflare_agree()

        if ok:
            st.success(message)
        else:
            st.error(message)

    st.markdown("### 🔄 Báo cáo lỗi AI")

    errors = get_reports("Lỗi AI")

    if not errors:
        st.success("Không có báo cáo lỗi AI.")
    else:
        for row in errors:
            with st.container(border=True):
                st.write(f"**ID:** {row['id']}")
                st.error(row["ai_error"] or "Lỗi không xác định.")

                if st.button(
                    "🤖 Phân tích lại",
                    key=f"retry_{row['id']}",
                ):
                    try:
                        with open(row["image_path"], "rb") as file:
                            image_bytes = file.read()

                        prepared = prepare_image(image_bytes)
                        parsed, raw_ai = analyze_image_with_cloudflare(
                            prepared
                        )

                        save_ai_result(
                            row["id"],
                            parsed,
                            raw_ai,
                        )

                        st.success("Đã phân tích lại.")
                        st.rerun()

                    except Exception as exc:
                        st.error(str(exc))

    st.markdown("### 📊 Thống kê")

    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM reports"
    ).fetchone()[0]
    conn.close()

    st.write(f"Tổng số bản ghi: **{total}**")

    st.markdown("### ⚠️ Reset database")

    confirm = st.checkbox(
        "Tôi hiểu thao tác này sẽ xóa toàn bộ báo cáo.",
    )

    if st.button(
        "🗑️ RESET TOÀN BỘ DATABASE",
        disabled=not confirm,
    ):
        conn = get_db()
        rows = conn.execute(
            "SELECT image_path, cleanup_image_path FROM reports"
        ).fetchall()

        for row in rows:
            for path in [
                row["image_path"],
                row["cleanup_image_path"],
            ]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        conn.execute("DELETE FROM reports")
        conn.execute("DELETE FROM user_points")
        conn.execute("DELETE FROM team_points")
        conn.commit()
        conn.close()

        st.success("Đã reset database.")
        st.rerun()


# =========================================================
# 13. SIDEBAR
# =========================================================

def sidebar_menu():
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <div class="sidebar-brand-row">
                    <div class="sidebar-logo">UG</div>
                    <div>
                        <div class="sidebar-brand-title">Urban GreenEye AI</div>
                        <div class="sidebar-brand-sub">Mắt Xanh Đô Thị</div>
                    </div>
                </div>
            </div>

            <div class="sidebar-live">
                <span class="sidebar-live-dot"></span>
                Hệ thống AI đang hoạt động
            </div>

            <div class="sidebar-section-title">Điều hướng</div>
            """,
            unsafe_allow_html=True,
        )

        page = st.radio(
            "MENU",
            [
                "▣  Gửi báo cáo",
                "◆  Đội dọn dẹp nhận nhiệm vụ",
                "✓  Danh sách đã dọn",
                "★  Bảng xếp hạng tích điểm",
                "!  Báo cáo Spam & Xóa",
                "⚙  Reset & Cài đặt AI",
            ],
            label_visibility="collapsed",
        )

        st.markdown(
            """
            <div class="sidebar-section-title">Hệ thống</div>
            <div class="sidebar-info">
                <b>🤖 AI Vision</b><br>
                Tự động phân tích ảnh ngay sau khi gửi.<br><br>
                <b>Bản đồ</b><br>
                Hiển thị vị trí báo cáo và nhiệm vụ dọn dẹp.<br><br>
                <b>Bảo vệ dữ liệu</b><br>
                Báo cáo Spam tự động xóa sau 7 ngày.
            </div>

            <div class="sidebar-info" style="text-align:center; margin-top:10px;">
                <b>URBAN GREENEYE</b><br>
                Clean City • Green Future
            </div>
            """,
            unsafe_allow_html=True,
        )

    return page

# =========================================================
# 14. MAIN
# =========================================================

show_header()
show_stats()

page = sidebar_menu()

# Đổi mục = xóa toàn bộ quyền tạm thời. Admin phải nhập PIN lại ở mục mới.
_previous_page = st.session_state.get("current_menu")
if _previous_page is None:
    st.session_state["current_menu"] = page
elif _previous_page != page:
    for _key in (
        "admin_unlocked",
        "admin_full_access",
        "team_unlocked",
        "staff_unlocked",
    ):
        st.session_state[_key] = False
    # Không giữ lại PIN đã nhập khi chuyển mục.
    for _key in ("admin_pin_input", "team_pin_input", "staff_pin_input"):
        st.session_state.pop(_key, None)
    st.session_state["current_menu"] = page

if page == "▣  Gửi báo cáo":
    page_report()

elif page == "◆  Đội dọn dẹp nhận nhiệm vụ":
    page_cleanup_team()

elif page == "✓  Danh sách đã dọn":
    page_completed()

elif page == "★  Bảng xếp hạng tích điểm":
    page_leaderboard()

elif page == "!  Báo cáo Spam & Xóa":
    page_spam()

elif page == "⚙  Reset & Cài đặt AI":
    page_admin()
