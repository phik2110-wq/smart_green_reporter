import ast
import datetime
from io import BytesIO
import base64
import json
import os
import re
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


# =========================================================
# LƯU TRỮ BỀN VỮNG: SUPABASE POSTGRES + STORAGE
# Không dùng SQLite/local files khi chạy trên Streamlit Community Cloud.
# =========================================================
SUPABASE_URL = get_setting("SUPABASE_URL").rstrip("/")
# Ưu tiên key server-side có quyền cao. Không tự động dùng anon/publishable key
# cho Storage vì key đó thường bị RLS chặn upload.
SUPABASE_SERVICE_ROLE_KEY = get_setting("SUPABASE_SERVICE_ROLE_KEY").strip()
SUPABASE_SECRET_KEY = get_setting("SUPABASE_SECRET_KEY").strip()
SUPABASE_PUBLIC_KEY = get_setting("SUPABASE_KEY").strip()
SUPABASE_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY or SUPABASE_PUBLIC_KEY
SUPABASE_BUCKET = get_setting("SUPABASE_BUCKET", "urban-greeneye")


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


.cleanup-report-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    background: linear-gradient(90deg, #eaf8ee, #f8fffa);
    border: 1px solid #b8dec1;
    border-radius: 13px;
    padding: 10px 12px;
    margin-bottom: 12px;
    font-weight: 900;
}
.cleanup-status-pill {
    background: #dff3e4;
    border: 1px solid #9dd2aa;
    border-radius: 999px;
    padding: 4px 9px;
    font-size: .72rem;
    white-space: nowrap;
}
.cleanup-status-pill.active { background: #fff3cd; border-color: #e7c766; }
.ranking-panel {
    background: linear-gradient(135deg, #e8f7ec, #ffffff);
    border: 1px solid #b8dec1;
    border-radius: 18px;
    padding: 16px 18px;
    margin-bottom: 10px;
}
.ranking-panel-title { font-size: 1.15rem; font-weight: 900; }
.ranking-panel-sub { font-size: .78rem; margin-top: 3px; opacity: .68; }
.ranking-row {
    display: grid;
    grid-template-columns: 42px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    background: #ffffff;
    border: 1px solid #d2e8d6;
    border-radius: 14px;
    padding: 10px 12px;
    margin: 7px 0;
    box-shadow: 0 2px 8px rgba(30,100,45,.045);
}
.ranking-rank { text-align: center; font-size: 1.18rem; }
.rank-number {
    display: inline-flex; width: 28px; height: 28px; align-items: center;
    justify-content: center; border-radius: 50%; background: #edf5ef;
    font-size: .78rem; font-weight: 900;
}
.ranking-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 800; }
.ranking-points { font-weight: 900; white-space: nowrap; font-size: .95rem; }
.ranking-points small { font-size: .72rem; opacity: .65; font-weight: 700; }

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
    .cleanup-report-head { padding: 8px 9px; font-size: .88rem; }
    .cleanup-status-pill { font-size: .64rem; padding: 3px 7px; }
    .ranking-panel { padding: 13px 12px; }
    .ranking-row { grid-template-columns: 34px minmax(0, 1fr) auto; padding: 9px 8px; }

}

</style>
""",
    unsafe_allow_html=True,
)

# =========================================================
# 3. DATABASE + STORAGE (SUPABASE)
# =========================================================

def supabase_configured():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def supabase_server_key_configured():
    return bool(SUPABASE_URL and (SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY))


def _supabase_headers(extra=None):
    if not supabase_configured():
        raise RuntimeError(
            "Chưa cấu hình SUPABASE_URL và SUPABASE_SERVICE_ROLE_KEY "
            "(hoặc SUPABASE_SECRET_KEY)."
        )
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _supabase_data_url(table: str):
    return f"{SUPABASE_URL}/rest/v1/{table}"


def _supabase_request(method, url, **kwargs):
    headers = kwargs.pop("headers", {})
    merged = _supabase_headers(headers)
    try:
        response = requests.request(
            method,
            url,
            headers=merged,
            timeout=30,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Không kết nối được Supabase: {exc}") from exc

    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(
            f"Supabase HTTP {response.status_code}: "
            f"{json.dumps(detail, ensure_ascii=False)[:3000] if not isinstance(detail, str) else detail[:3000]}"
        )

    if not response.content:
        return None
    try:
        return response.json()
    except Exception:
        return response.text


def _supabase_select(table, params=None):
    return _supabase_request(
        "GET",
        _supabase_data_url(table),
        params=params or {"select": "*"},
    ) or []


def _supabase_insert(table, payload, return_rows=True):
    headers = {"Prefer": "return=representation" if return_rows else "return=minimal"}
    data = _supabase_request(
        "POST",
        _supabase_data_url(table),
        headers=headers,
        json=payload,
    )
    return data or []


def _supabase_update(table, filters, payload):
    params = {f"{key}": f"eq.{value}" for key, value in filters.items()}
    params["select"] = "*"
    return _supabase_request(
        "PATCH",
        _supabase_data_url(table),
        params=params,
        headers={"Prefer": "return=representation"},
        json=payload,
    ) or []


def _supabase_delete_rows(table, filters):
    params = {f"{key}": f"eq.{value}" for key, value in filters.items()}
    return _supabase_request(
        "DELETE",
        _supabase_data_url(table),
        params=params,
        headers={"Prefer": "return=representation"},
    ) or []


def storage_upload(image_bytes: bytes, path: str):
    """Upload ảnh vào Supabase Storage. Ưu tiên server-side key để tránh lỗi RLS."""
    if not supabase_configured():
        raise RuntimeError("Chưa cấu hình Supabase Storage.")
    if not supabase_server_key_configured():
        raise RuntimeError(
            "Supabase đang dùng key công khai/anon nên Storage bị RLS chặn. "
            "Hãy thêm SUPABASE_SECRET_KEY (sb_secret_...) hoặc "
            "SUPABASE_SERVICE_ROLE_KEY vào Streamlit Secrets."
        )

    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{path.lstrip('/')}"
    headers = _supabase_headers({
        "Content-Type": "image/jpeg",
        "x-upsert": "false",
        "Cache-Control": "31536000",
    })
    try:
        response = requests.post(
            url,
            headers=headers,
            data=image_bytes,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Không tải ảnh lên Supabase Storage: {exc}") from exc

    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        detail_text = json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail
        if response.status_code in (401, 403) and "row-level security" in detail_text.lower():
            raise RuntimeError(
                "Supabase Storage từ chối upload (RLS). "
                "Hãy dùng SUPABASE_SECRET_KEY (sb_secret_...) hoặc SUPABASE_SERVICE_ROLE_KEY, "
                "hoặc chạy policy Storage trong file Urban_GreenEye_supabase_schema.sql. "
                f"Chi tiết: {detail_text[:1800]}"
            )
        raise RuntimeError(
            f"Storage upload HTTP {response.status_code}: {detail_text[:2500]}"
        )

    return path.lstrip("/")


def storage_public_url(path: str):
    if not path:
        return ""
    if str(path).startswith("http://") or str(path).startswith("https://"):
        return str(path)
    from urllib.parse import quote
    encoded_path = "/".join(quote(part, safe="") for part in str(path).strip("/").split("/"))
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{encoded_path}"


def storage_delete(path: str):
    """Xóa object bằng Storage API, không xóa trực tiếp bảng storage.objects."""
    if not path or str(path).startswith("http://") or str(path).startswith("https://"):
        return
    clean_path = str(path).strip("/")
    if not clean_path:
        return

    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{clean_path}"
    try:
        response = requests.delete(
            url,
            headers=_supabase_headers(),
            timeout=30,
        )
        if not response.ok and response.status_code != 404:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise RuntimeError(
                f"Storage delete HTTP {response.status_code}: "
                f"{json.dumps(detail, ensure_ascii=False)[:2000] if not isinstance(detail, str) else detail[:2000]}"
            )
    except requests.RequestException as exc:
        raise RuntimeError(f"Không xóa được ảnh trên Supabase Storage: {exc}") from exc


def show_stored_image(path, caption=None):
    """Hiển thị ảnh từ Supabase URL; fallback local chỉ để tương thích dữ liệu cũ."""
    if not path:
        return False
    path = str(path)
    if path.startswith("http://") or path.startswith("https://"):
        st.image(path, caption=caption, use_container_width=True)
        return True
    if os.path.exists(path):
        st.image(path, caption=caption, use_container_width=True)
        return True
    if supabase_configured():
        try:
            st.image(storage_public_url(path), caption=caption, use_container_width=True)
            return True
        except Exception:
            pass
    return False


def download_stored_image(path: str):
    if not path:
        raise FileNotFoundError("Không có đường dẫn ảnh.")
    path = str(path)
    if path.startswith("http://") or path.startswith("https://"):
        response = requests.get(path, timeout=30)
        response.raise_for_status()
        return response.content
    if os.path.exists(path):
        return open(path, "rb").read()
    if not supabase_configured():
        raise FileNotFoundError("Ảnh không còn trên máy và Supabase chưa được cấu hình.")
    response = requests.get(storage_public_url(path), timeout=30)
    response.raise_for_status()
    return response.content


def cleanup_expired_spam():
    if not supabase_configured():
        return

    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=SPAM_RETENTION_DAYS)
    ).isoformat()

    rows = _supabase_select(
        "reports",
        {
            "select": "id,image_path,cleanup_image_path,created_at",
            "status": "eq.Spam/Từ chối",
            "created_at": f"lt.{cutoff}",
        },
    )

    for row in rows:
        for path in [row.get("image_path"), row.get("cleanup_image_path")]:
            if path:
                try:
                    storage_delete(path)
                except Exception:
                    pass
        _supabase_delete_rows("reports", {"id": row.get("id")})


def init_db():
    """Supabase schema được tạo một lần bằng SQL trong Dashboard."""
    if not supabase_configured():
        st.error(
            "🔴 Chưa cấu hình lưu trữ bền vững. Hãy thêm SUPABASE_URL và "
            "SUPABASE_SERVICE_ROLE_KEY vào Streamlit Secrets."
        )
        st.stop()
    # Không tạo bảng bằng SQL từ ứng dụng. Chỉ kiểm tra kết nối và schema.
    try:
        _supabase_select("reports", {"select": "id", "limit": "1"})
    except Exception as exc:
        st.error(
            "❌ Supabase chưa sẵn sàng. Hãy chạy SQL schema được cung cấp kèm app "
            f"trong Supabase SQL Editor. Chi tiết: {exc}"
        )
        st.stop()


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
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    report_id = str(uuid.uuid4())
    row = {
        "id": report_id,
        "reporter_name": reporter_name,
        "image_path": image_path,
        "description": description,
        "location": location,
        "latitude": latitude,
        "longitude": longitude,
        "status": "Đang xử lý",
        "created_at": created_at,
    }
    _supabase_insert("reports", row, return_rows=False)
    return report_id


def save_ai_result(report_id, parsed, raw_data):
    valid = (
        parsed.get("contains_waste", False)
        and parsed.get("is_waste_amount_sufficient", False)
    )

    status = "Đã duyệt" if valid else "Spam/Từ chối"
    ai_result = parsed.get("natural_report", "")

    _supabase_update(
        "reports",
        {"id": report_id},
        {
            "status": status,
            "ai_result": ai_result,
            "ai_raw_json": json.dumps(parsed, ensure_ascii=False, indent=2),
            "ai_analyzed": True,
            "ai_error": None,
        },
    )
    return status


def save_ai_error(report_id, error_text):
    _supabase_update(
        "reports",
        {"id": report_id},
        {
            "status": "Lỗi AI",
            "ai_analyzed": False,
            "ai_error": str(error_text),
        },
    )


def get_report(report_id):
    rows = _supabase_select(
        "reports",
        {"select": "*", "id": f"eq.{report_id}", "limit": "1"},
    )
    return rows[0] if rows else None


def get_reports(status=None):
    params = {
        "select": "*",
        "order": "created_at.desc",
    }
    if status:
        params["status"] = f"eq.{status}"
    return _supabase_select("reports", params)


def report_code(report_id):
    """Mã báo cáo ngắn, đẹp; UUID thật vẫn giữ nguyên trong database."""
    raw = re.sub(r"[^A-Fa-f0-9]", "", str(report_id or "")).upper()
    return f"QL-{raw[:6]}" if raw else "QL-000000"


def report_title(row):
    return report_code(row.get("id"))


def delete_report(report_id):
    row = get_report(report_id)
    if not row:
        return

    for path in [row.get("image_path"), row.get("cleanup_image_path")]:
        if path:
            try:
                storage_delete(path)
            except Exception:
                pass

    _supabase_delete_rows("reports", {"id": report_id})


def _add_points(table_name, name, points):
    name = (name or "").strip()
    if not name:
        return

    existing = _supabase_select(
        table_name,
        {"select": "name,points", "name": f"eq.{name}", "limit": "1"},
    )
    if existing:
        current = int(existing[0].get("points") or 0)
        _supabase_update(
            table_name,
            {"name": name},
            {"points": current + int(points)},
        )
    else:
        _supabase_insert(
            table_name,
            {"name": name, "points": int(points)},
            return_rows=False,
        )


def add_team_points(name, points):
    _add_points("team_points", name, points)


def add_user_points(name, points):
    _add_points("user_points", name, points)


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
    rows = _supabase_select("reports", {"select": "status"})
    total = len(rows)
    approved = sum(1 for row in rows if row.get("status") == "Đã duyệt")
    pending = sum(1 for row in rows if row.get("status") in ("Đang xử lý", "Lỗi AI"))
    cleaned = sum(1 for row in rows if row.get("status") == "Đã dọn")

    stats = [
        ("📋", total, "Tổng báo cáo"),
        ("🤖", approved, "AI xác nhận"),
        ("⏳", pending, "Đang xử lý"),
        ("🧹", cleaned, "Đã dọn"),
    ]

    cards = "".join(
        f"""<div class=\"stat-card\">\n            <div class=\"stat-icon\">{icon}</div>\n            <div class=\"stat-number\">{number}</div>\n            <div class=\"stat-label\">{label}</div>\n        </div>"""
        for icon, number, label in stats
    )

    st.markdown(f'<div class="stats-grid">{cards}</div>', unsafe_allow_html=True)


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
        f'<div class="report-title">📋 Báo cáo #{report_title(row)}</div>',
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

    if not show_stored_image(row["image_path"]):
        st.caption("Không tải được ảnh hiện trường.")

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

            report_id = str(uuid.uuid4())
            image_path = f"reports/{report_id}.jpg"
            storage_upload(prepared, image_path)

            try:
                # Chỉ sau khi ảnh đã nằm trên Storage bền vững mới ghi bản ghi DB.
                report_id = insert_report(
                    reporter_name=name.strip() or "Ẩn danh",
                    image_path=image_path,
                    description=description.strip(),
                    location=location.strip(),
                    latitude=latitude,
                    longitude=longitude,
                )
            except Exception:
                try:
                    storage_delete(image_path)
                except Exception:
                    pass
                raise

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

    pin = st.text_input("Mã PIN đội dọn dẹp", type="password", key="team_pin_input")
    if st.button("→ Vào khu vực đội dọn dẹp", key="team_login", use_container_width=True):
        if unlock_with_pin(pin, "team_unlocked"):
            st.success("Đã mở quyền truy cập.")
        else:
            st.error("Mã PIN không đúng.")

    if not st.session_state.get("team_unlocked", False) and not st.session_state.get("admin_full_access", False):
        st.info("Nhập mã PIN rồi bấm nút để vào.")
        return

    team_name = st.text_input("Tên đội / thành viên", placeholder="Ví dụ: Đội Môi Trường A3", key="cleanup_team_name")

    approved = get_reports("Đã duyệt")
    st.markdown("## 📌 Nhiệm vụ chờ nhận")
    st.caption(f"{len(approved)} nhiệm vụ đang chờ đội dọn dẹp nhận.")

    if approved:
        for row in approved:
            code = report_title(row)
            with st.container(border=True):
                st.markdown(
                    f"<div class='cleanup-report-head'><span>📋 BÁO CÁO #{code}</span><span class='cleanup-status-pill'>CHỜ NHẬN</span></div>",
                    unsafe_allow_html=True,
                )
                cols = st.columns([0.95, 1.05])
                with cols[0]:
                    st.markdown(f"**📍 Địa điểm**  \\n{row['location'] or 'Chưa có địa chỉ'}")
                    st.markdown(f"**📝 Nội dung**  \\n{row['description'] or 'Không có mô tả'}")
                    if row.get("ai_raw_json"):
                        try:
                            ai = json.loads(row["ai_raw_json"])
                            st.markdown(f"🗑️ **Loại rác:** {ai.get('waste_type', '') or 'Chưa xác định'}")
                            st.markdown(f"⚠️ **Mức độ:** {ai.get('severity', '') or 'Chưa xác định'}")
                            st.markdown(f"🚚 **Phương án:** {ai.get('dispatch_plan', '') or 'AI chưa đề xuất'}")
                        except Exception:
                            pass
                    if st.button("📌 Nhận nhiệm vụ", key=f"assign_{row['id']}", use_container_width=True):
                        if not team_name.strip():
                            st.error("Nhập tên đội trước.")
                        else:
                            changed = _supabase_update("reports", {"id": row["id"], "status": "Đã duyệt"}, {"assigned_team": team_name.strip(), "status": "Đang dọn"})
                            if changed:
                                st.success(f"Đã nhận nhiệm vụ #{code}.")
                            st.rerun()
                with cols[1]:
                    if not show_stored_image(row["image_path"], "Ảnh hiện trường"):
                        st.caption("Không tải được ảnh hiện trường.")
                st.markdown("#### 📍 Vị trí trên bản đồ")
                show_task_map(row)
    else:
        st.info("Hiện chưa có nhiệm vụ mới đang chờ nhận.")

    active = get_reports("Đang dọn")
    st.markdown("## 🧹 Báo cáo sau dọn dẹp")
    st.caption("Mỗi báo cáo được đặt trong một khung riêng để không bị rối khi có nhiều nhiệm vụ.")

    if not active:
        st.info("Chưa có nhiệm vụ đang dọn. Hãy bấm 'Nhận nhiệm vụ' ở phía trên.")
        return

    st.success(f"Bạn đang có {len(active)} nhiệm vụ cần hoàn thành.")
    for row in active:
        code = report_title(row)
        with st.container(border=True):
            st.markdown(
                f"<div class='cleanup-report-head'><span>🧹 BÁO CÁO #{code}</span><span class='cleanup-status-pill active'>ĐANG DỌN</span></div>",
                unsafe_allow_html=True,
            )
            st.caption(f"👷 Đội phụ trách: {row['assigned_team'] or 'Chưa xác định'}  •  📍 {row['location'] or 'Chưa có vị trí'}")
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**📷 Ảnh hiện trường**")
                show_stored_image(row["image_path"], "Ảnh trước khi dọn")
            with cols[1]:
                st.markdown("**📤 Ảnh sau khi dọn**")
                cleanup_photo = st.file_uploader("Tải ảnh hiện trường sau khi dọn", type=["jpg", "jpeg", "png", "webp"], key=f"cleanup_photo_{row['id']}")
                cleanup_note = st.text_area("📝 Ghi chú hoàn thành", placeholder="Ví dụ: Đã thu gom toàn bộ rác và vệ sinh khu vực.", key=f"cleanup_note_{row['id']}")
                if st.button("✅ Xác nhận đã dọn xong", key=f"finish_{row['id']}", type="primary", use_container_width=True):
                    if not cleanup_photo:
                        st.error("Cần tải ảnh sau khi dọn.")
                    else:
                        try:
                            prepared = prepare_image(cleanup_photo.getvalue())
                            cleanup_path = f"cleanup/{row['id']}_{uuid.uuid4().hex}.jpg"
                            storage_upload(prepared, cleanup_path)
                            updated = _supabase_update("reports", {"id": row["id"]}, {"status": "Đã dọn", "cleanup_image_path": cleanup_path, "cleanup_note": cleanup_note.strip(), "cleaned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
                            if not updated:
                                storage_delete(cleanup_path)
                                raise RuntimeError("Không cập nhật được trạng thái báo cáo trên Supabase.")
                            add_team_points(row["assigned_team"] or team_name, 10)
                            st.success(f"Đã hoàn thành báo cáo #{code}.")
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
                if not show_stored_image(row["image_path"], "Ảnh trước khi dọn"):
                    st.caption("Không tải được ảnh trước khi dọn.")

            with cols[1]:
                st.markdown("**Ảnh sau khi dọn**")
                if not show_stored_image(row["cleanup_image_path"], "Ảnh sau khi dọn"):
                    st.caption("Không tải được ảnh sau khi dọn.")

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
    try:
        return _supabase_select(
            table_name,
            {"select": "name,points", "order": "points.desc,name.asc", "limit": "50"},
        )
    except Exception:
        return []


def page_leaderboard():
    st.subheader("🏆 Bảng xếp hạng tích điểm")
    st.caption("Điểm được lưu trực tiếp trên Supabase nên không mất khi Streamlit ngủ hoặc khởi động lại.")
    users = _read_points_table("user_points")
    teams = _read_points_table("team_points")

    def render_ranking(title, icon, rows):
        st.markdown(f"<div class='ranking-panel'><div class='ranking-panel-title'>{icon} {title}</div><div class='ranking-panel-sub'>Top {min(len(rows), 10)} thành tích cao nhất</div></div>", unsafe_allow_html=True)
        if not rows:
            st.info("Chưa có dữ liệu điểm.")
            return
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows[:10], 1):
            medal = medals[index-1] if index <= 3 else f"<span class='rank-number'>{index}</span>"
            name = str(row.get("name") or "Không tên")
            points = int(row.get("points") or 0)
            st.markdown(f"<div class='ranking-row'><div class='ranking-rank'>{medal}</div><div class='ranking-name'>{name}</div><div class='ranking-points'>{points:,} <small>điểm</small></div></div>", unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        render_ranking("Người dân", "👤", users)
    with right:
        render_ranking("Đội dọn dẹp", "🧹", teams)


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

            if not show_stored_image(row["image_path"]):
                st.caption("Không tải được ảnh.")

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
                        image_bytes = download_stored_image(row["image_path"])
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

    total = len(_supabase_select("reports", {"select": "id"}))
    st.write(f"Tổng số bản ghi: **{total}**")

    st.markdown("### ⚠️ Reset database")

    confirm = st.checkbox(
        "Tôi hiểu thao tác này sẽ xóa toàn bộ báo cáo.",
    )

    if st.button(
        "🗑️ RESET TOÀN BỘ DATABASE",
        disabled=not confirm,
    ):
        rows = _supabase_select("reports", {"select": "id,image_path,cleanup_image_path"})

        for row in rows:
            for path in [row.get("image_path"), row.get("cleanup_image_path")]:
                if path:
                    try:
                        storage_delete(path)
                    except Exception:
                        pass

        # Xóa từng ID thực tế để reset chắc chắn.
        for row in rows:
            _supabase_delete_rows("reports", {"id": row["id"]})
        for row in _supabase_select("user_points", {"select": "name"}):
            _supabase_delete_rows("user_points", {"name": row["name"]})
        for row in _supabase_select("team_points", {"select": "name"}):
            _supabase_delete_rows("team_points", {"name": row["name"]})

        st.success("Đã reset database và Storage.")
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
