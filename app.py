import ast
import datetime
from io import BytesIO
import base64
import hmac
import json
import os
import re
import uuid
import math
from html import escape

import folium
from folium.plugins import HeatMap, MarkerCluster
import requests
import streamlit as st
from PIL import Image, ImageOps
from streamlit_folium import st_folium


# =========================================================
# 1. CẤU HÌNH
# =========================================================

st.set_page_config(
    page_title="Urban GreenEye AI - Mắt Xanh Đô Thị",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
MAX_IMAGE_MB = 10
SPAM_RETENTION_DAYS = 7

DEFAULT_LAT = 10.9570
DEFAULT_LNG = 106.8427
DEPOT = [10.9520, 106.8200]  # Trạm xuất phát xe thu gom


def get_setting(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, default)


SUPABASE_URL = get_setting("SUPABASE_URL").rstrip("/")
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


def _pin_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def unlock_with_pin(pin: str, session_key: str) -> bool:
    if _pin_equal(pin, ADMIN_PIN):
        st.session_state["admin_full_access"] = True
        st.session_state[session_key] = True
        return True
    if session_key == "admin_unlocked":
        ok = _pin_equal(pin, ADMIN_PIN)
    elif session_key == "team_unlocked":
        ok = _pin_equal(pin, TEAM_PIN)
    elif session_key == "staff_unlocked":
        ok = _pin_equal(pin, STAFF_PIN)
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
    color: #ffffff !important;
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

.hero {
    background: linear-gradient(135deg, #dff3e3, #f7fcf8);
    color: #111111 !important;
    border: 1px solid #afd8b7;
    border-radius: 24px;
    padding: 30px;
    margin-bottom: 20px;
    box-shadow: 0 10px 30px rgba(20, 100, 45, .10);
}

.hero h1 {
    margin: 0;
    font-size: 2.2rem;
}

.hero p {
    margin: 8px 0 0;
    opacity: .85;
}

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

.agent-badge-urgent {
    background: #fee2e2;
    color: #b91c1c !important;
    border: 1px solid #f87171;
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 800;
    font-size: 0.78rem;
}
.agent-badge-hazard {
    background: #fef3c7;
    color: #b45309 !important;
    border: 1px solid #fbbf24;
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 800;
    font-size: 0.78rem;
}
.agent-badge-normal {
    background: #e0f2fe;
    color: #0369a1 !important;
    border: 1px solid #7dd3fc;
    border-radius: 8px;
    padding: 3px 8px;
    font-weight: 800;
    font-size: 0.78rem;
}

.stApp div[data-baseweb="input"],
.stApp div[data-baseweb="textarea"],
.stApp input,
.stApp textarea {
    background-color: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: #333333 !important;
}

.stApp button,
.stButton > button,
.stFormSubmitButton > button {
    background-color: #111111 !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: #111111 !important;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 8px 0 18px;
}
.stats-grid .stat-card {
    min-height: 118px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    padding: 12px;
}
.stats-grid .stat-icon { font-size: 1.45rem; margin-bottom: 5px; }
.stats-grid .stat-number { font-size: 1.55rem; }
.stats-grid .stat-label { font-size: .82rem; margin-top: 5px; }

.cleanup-report-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
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
.ranking-name { font-weight: 800; }
.ranking-points { font-weight: 900; font-size: .95rem; }
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
        raise RuntimeError("Chưa cấu hình SUPABASE_URL và SUPABASE_KEY.")
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
        response = requests.request(method, url, headers=merged, timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(f"Không kết nối được Supabase: {exc}") from exc

    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f"Supabase HTTP {response.status_code}: {detail}")

    if not response.content:
        return None
    try:
        return response.json()
    except Exception:
        return response.text

def _supabase_select(table, params=None):
    return _supabase_request("GET", _supabase_data_url(table), params=params or {"select": "*"}) or []

def _supabase_insert(table, payload, return_rows=True):
    headers = {"Prefer": "return=representation" if return_rows else "return=minimal"}
    return _supabase_request("POST", _supabase_data_url(table), headers=headers, json=payload) or []

def _supabase_update(table, filters, payload):
    params = {f"{key}": f"eq.{value}" for key, value in filters.items()}
    params["select"] = "*"
    return _supabase_request("PATCH", _supabase_data_url(table), params=params, headers={"Prefer": "return=representation"}, json=payload) or []

def _supabase_delete_rows(table, filters):
    params = {f"{key}": f"eq.{value}" for key, value in filters.items()}
    return _supabase_request("DELETE", _supabase_data_url(table), params=params, headers={"Prefer": "return=representation"}) or []

def storage_upload(image_bytes: bytes, path: str):
    if not supabase_configured():
        raise RuntimeError("Chưa cấu hình Supabase Storage.")
    if not supabase_server_key_configured():
        raise RuntimeError("Cần cấu hình SUPABASE_SERVICE_ROLE_KEY để upload storage.")

    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{path.lstrip('/')}"
    headers = _supabase_headers({"Content-Type": "image/jpeg", "x-upsert": "false", "Cache-Control": "31536000"})
    response = requests.post(url, headers=headers, data=image_bytes, timeout=60)
    if not response.ok:
        raise RuntimeError(f"Storage upload error: {response.text}")
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
    if not path or str(path).startswith("http://") or str(path).startswith("https://"):
        return
    clean_path = str(path).strip("/")
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{clean_path}"
    requests.delete(url, headers=_supabase_headers(), timeout=30)

def st_image(src, caption=None):
    """Tương thích cả Streamlit cũ (use_column_width) lẫn mới (use_container_width)."""
    try:
        st.image(src, caption=caption, use_container_width=True)
    except TypeError:
        st.image(src, caption=caption, use_column_width=True)

def show_stored_image(path, caption=None):
    if not path:
        return False
    path = str(path)
    if path.startswith("http://") or path.startswith("https://"):
        st_image(path, caption)
        return True
    if os.path.exists(path):
        st_image(path, caption)
        return True
    if supabase_configured():
        try:
            st_image(storage_public_url(path), caption)
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
        with open(path, "rb") as f:
            return f.read()
    response = requests.get(storage_public_url(path), timeout=30)
    response.raise_for_status()
    return response.content

def cleanup_expired_spam():
    if not supabase_configured():
        return
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=SPAM_RETENTION_DAYS)).isoformat()
    rows = _supabase_select("reports", {"select": "id,image_path,cleanup_image_path", "status": "eq.Spam/Từ chối", "created_at": f"lt.{cutoff}"})
    for row in rows:
        for path in [row.get("image_path"), row.get("cleanup_image_path")]:
            if path:
                try:
                    storage_delete(path)
                except Exception:
                    pass
        _supabase_delete_rows("reports", {"id": row.get("id")})

@st.cache_data(ttl=3600, show_spinner=False)
def _cleanup_expired_spam_hourly():
    """Chỉ dọn Spam hết hạn tối đa 1 lần/giờ thay vì mỗi lần rerun."""
    try:
        cleanup_expired_spam()
    except Exception:
        pass
    return True

def init_db():
    if not supabase_configured():
        st.error("🔴 Chưa cấu hình lưu trữ bền vững Supabase.")
        st.stop()
    try:
        _supabase_select("reports", {"select": "id", "limit": "1"})
    except Exception as exc:
        st.error(f"❌ Kết nối Supabase thất bại: {exc}")
        st.stop()

# Chỉ kiểm tra kết nối 1 lần cho mỗi phiên
if not st.session_state.get("db_ready"):
    init_db()
    st.session_state["db_ready"] = True
_cleanup_expired_spam_hourly()


# =========================================================
# 4. CLOUDFLARE WORKERS AI
# =========================================================

def cloudflare_configured():
    return bool(CF_ACCOUNT_ID and CF_AUTH_TOKEN)

def cloudflare_url():
    return f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"

def cloudflare_agree():
    if not cloudflare_configured():
        return False, "Thiếu CLOUDFLARE_ACCOUNT_ID hoặc CLOUDFLARE_AUTH_TOKEN."
    try:
        response = requests.post(
            cloudflare_url(),
            headers={"Authorization": f"Bearer {CF_AUTH_TOKEN}", "Content-Type": "application/json"},
            json={"prompt": "agree"},
            timeout=60,
        )
        data = response.json()
        return (True, "Đã gửi xác nhận model thành công.") if response.ok and data.get("success") else (False, json.dumps(data, ensure_ascii=False))
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
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    output = BytesIO()
    image.save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()

def image_to_data_uri(image_bytes: bytes):
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"

def extract_json_object(text: str):
    if isinstance(text, dict):
        return text
    text = str(text or "").strip()
    if not text:
        raise ValueError("AI không trả về nội dung.")

    # Bỏ hàng rào markdown ```json ... ```
    cleaned = re.sub(r"```(?:json|python)?\s*", "", text, flags=re.I).replace("```", "").strip()
    candidates = [cleaned]

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

    candidates.extend(objects)
    for candidate in candidates:
        try:
            val = json.loads(candidate.strip())
            if isinstance(val, dict):
                return val
        except Exception:
            pass
        try:
            val = ast.literal_eval(candidate.strip())
            if isinstance(val, dict):
                return val
        except Exception:
            pass

    raise ValueError(f"AI không trả về JSON hợp lệ: {text[:1200]}")

def normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower().rstrip(".") in {"true", "1", "yes", "có", "co", "đúng"}
    return False

SEVERITY_LABELS = {
    "3": "Cấp 3 - Khẩn cấp",
    "2": "Cấp 2 - 24 Giờ",
    "1": "Cấp 1 - Thường",
}

def normalize_severity(raw_severity, urgent_flag):
    """Chuẩn hóa mức độ về đúng 1 trong 3 nhãn.
    - Có nguy cơ nghẽn cống / rác độc hại => luôn Cấp 3.
    - Nếu model chép nguyên cả chuỗi mẫu (nhiều cấp cùng lúc) => coi là không rõ => Cấp 1.
    """
    if urgent_flag:
        return SEVERITY_LABELS["3"]
    levels = set(re.findall(r"C[ấa]p\s*([123])", str(raw_severity or ""), flags=re.I))
    if len(levels) == 1:
        return SEVERITY_LABELS[levels.pop()]
    return SEVERITY_LABELS["1"]

def severity_level(severity):
    m = re.search(r"C[ấa]p\s*([123])", str(severity or ""), flags=re.I)
    return int(m.group(1)) if m else 1

def _cloudflare_request(prompt: str, image_bytes: bytes):
    payload = {
        "prompt": prompt,
        "image": image_to_data_uri(image_bytes),
        "max_tokens": 600,
        "temperature": 0,
    }
    response = requests.post(
        cloudflare_url(),
        headers={"Authorization": f"Bearer {CF_AUTH_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    try:
        data = response.json()
    except Exception:
        data = {}
    if not response.ok or not data.get("success"):
        raise RuntimeError(f"Cloudflare HTTP {response.status_code}: {json.dumps(data, ensure_ascii=False)[:1500] or response.text[:500]}")
    result = data.get("result", {})
    text = result.get("response") or result.get("text") or result.get("output") or result.get("generated_text") or json.dumps(result, ensure_ascii=False)
    return str(text), data

def _normalize_ai_result(parsed):
    parsed["contains_waste"] = normalize_bool(parsed.get("contains_waste"))
    parsed["is_waste_amount_sufficient"] = normalize_bool(parsed.get("is_waste_amount_sufficient"))
    parsed["is_genuine_scene"] = normalize_bool(parsed.get("is_genuine_scene", True))
    parsed["clogging_drain_risk"] = normalize_bool(parsed.get("clogging_drain_risk", False))
    parsed["hazardous_waste"] = normalize_bool(parsed.get("hazardous_waste", False))

    for key in ["natural_report", "waste_type", "severity", "visual_evidence", "spam_reason", "dispatch_plan", "assigned_unit", "confidence"]:
        parsed[key] = str(parsed.get(key, "") or "").strip()

    parsed["severity"] = normalize_severity(
        parsed.get("severity"),
        parsed["clogging_drain_risk"] or parsed["hazardous_waste"],
    )

    if not parsed["assigned_unit"]:
        parsed["assigned_unit"] = "Đội Vệ Sinh Môi Trường Đô Thị"

    return parsed

def analyze_image_with_cloudflare(image_bytes: bytes):
    """AI Agent: thẩm định gian lận + đánh giá cấp bách + điều phối."""
    prompt = """
Bạn là Hệ thống Tác nhân AI (AI Agent) Giám sát & Điều phối Môi trường Đô thị.
Hãy kiểm duyệt ảnh và trả lời duy nhất 01 JSON OBJECT.

Nhiệm vụ:
1. Thẩm định hiện trường: ảnh chụp màn hình máy tính, ảnh selfie/chân dung, ảnh trong nhà KHÔNG phải rác đô thị. Nếu là ảnh giả lập/chụp lại màn hình thì is_genuine_scene=false và contains_waste=false.
2. Đánh giá cấp bách:
   - clogging_drain_risk=true nếu rác đang che lấp/làm nghẽn cống, hố ga, kênh thoát nước.
   - hazardous_waste=true nếu thấy rác nguy hại như pin, ắc quy, hóa chất, bóng đèn vỡ.
   - severity: "Cấp 3 - Khẩn cấp" chỉ khi có nghẽn cống hoặc rác nguy hại; "Cấp 2 - 24 Giờ" khi rác nhiều, lấn lối đi; còn lại là "Cấp 1 - Thường".
3. Điều phối: gán đơn vị xử lý phù hợp và lập kế hoạch dọn dẹp ngắn gọn.

Mỗi khóa phải có đúng MỘT giá trị (không liệt kê nhiều lựa chọn). Với khóa true/false, hãy tự điền true hoặc false theo ảnh thực tế.
Cấu trúc JSON:
{
  "contains_waste": <true hoặc false>,
  "is_waste_amount_sufficient": <true hoặc false>,
  "is_genuine_scene": <true hoặc false>,
  "clogging_drain_risk": <true hoặc false>,
  "hazardous_waste": <true hoặc false>,
  "confidence": "<phần trăm, ví dụ 80%>",
  "natural_report": "<nhận xét tự nhiên bằng tiếng Việt>",
  "waste_type": "<loại rác: nilon, xà bần, rác cản dòng, pin...>",
  "severity": "<một giá trị duy nhất theo quy tắc trên>",
  "visual_evidence": "<những gì thực sự nhìn thấy trong ảnh>",
  "spam_reason": "<lý do từ chối nếu không hợp lệ, nếu hợp lệ để trống>",
  "dispatch_plan": "<kế hoạch hành động cụ thể cho đội thu gom>",
  "assigned_unit": "<đơn vị phụ trách, ví dụ Đội Vệ Sinh Môi Trường Phường>"
}
Không thêm bất kỳ văn bản nào ngoài JSON.
"""
    text, raw_data = _cloudflare_request(prompt, image_bytes)
    try:
        parsed = extract_json_object(text)
        return _normalize_ai_result(parsed), raw_data
    except Exception:
        repair_prompt = f"""
Hãy xuất lại kết quả dưới dạng JSON object hợp lệ duy nhất, giữ nguyên các khóa:
{text[:1000]}
"""
        repaired_text, repaired_raw = _cloudflare_request(repair_prompt, image_bytes)
        parsed = extract_json_object(repaired_text)
        return _normalize_ai_result(parsed), repaired_raw


# =========================================================
# 5. XỬ LÝ REPORT
# =========================================================

def insert_report(reporter_name, image_path, description, location, latitude, longitude):
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
        and parsed.get("is_genuine_scene", True)
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
    _supabase_update("reports", {"id": report_id}, {"status": "Lỗi AI", "ai_analyzed": False, "ai_error": str(error_text)})

def get_report(report_id):
    rows = _supabase_select("reports", {"select": "*", "id": f"eq.{report_id}", "limit": "1"})
    return rows[0] if rows else None

def get_reports(status=None):
    params = {"select": "*", "order": "created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    return _supabase_select("reports", params)

def report_code(report_id):
    raw = re.sub(r"[^A-Fa-f0-9]", "", str(report_id or "")).upper()
    return f"QL-{raw[:6]}" if raw else "QL-000000"

def report_title(row):
    return report_code(row.get("id"))

def parse_ai_json(row):
    try:
        data = json.loads(row.get("ai_raw_json") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

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

def parse_location_coordinates(location):
    if not location:
        return None, None
    patterns = [
        r"(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)",
        r"lat(?:itude)?\s*[:=]\s*(-?\d+(?:\.\d+)?).*?lon(?:gitude)?\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, location, re.I | re.S)
        if m:
            try:
                return float(m.group(1)), float(m.group(2))
            except Exception:
                pass
    return None, None

def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# =========================================================
# 6. UI HELPERS
# =========================================================

def show_header():
    st.markdown(
        """
        <div class="hero">
            <h1>🌱 Urban GreenEye AI</h1>
            <p>Clean City • Green Future — Hệ sinh thái Multi-Agent AI Giám sát, Thẩm định & Điều phối rác thải đô thị.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

@st.cache_data(ttl=15, show_spinner=False)
def _load_stats_rows():
    return _supabase_select("reports", {"select": "status"})

def show_stats():
    try:
        rows = _load_stats_rows()
    except Exception:
        rows = []
    total = len(rows)
    approved = sum(1 for row in rows if row.get("status") == "Đã duyệt")
    pending = sum(1 for row in rows if row.get("status") in ("Đang xử lý", "Lỗi AI"))
    cleaned = sum(1 for row in rows if row.get("status") == "Đã dọn")

    stats = [
        ("📋", total, "Tổng báo cáo"),
        ("🤖", approved, "AI Agent duyệt"),
        ("⏳", pending, "Đang xử lý"),
        ("🧹", cleaned, "Đã dọn"),
    ]

    cards = "".join(
        f"""<div class="stat-card">
            <div class="stat-icon">{icon}</div>
            <div class="stat-number">{number}</div>
            <div class="stat-label">{label}</div>
        </div>"""
        for icon, number, label in stats
    )
    st.markdown(f'<div class="stats-grid">{cards}</div>', unsafe_allow_html=True)

def show_task_map(row):
    lat = row.get("latitude")
    lng = row.get("longitude")
    if lat is None or lng is None:
        lat, lng = parse_location_coordinates(row.get("location"))
    if lat is None or lng is None:
        st.info("Báo cáo này chưa có tọa độ bản đồ.")
        return

    fmap = folium.Map(location=[lat, lng], zoom_start=16, control_scale=True)
    folium.Marker(
        [lat, lng],
        tooltip="📍 Vị trí nhiệm vụ",
        popup=folium.Popup(f"<b>Vị trí báo cáo</b><br>{escape(str(row.get('location') or 'Không có địa chỉ'))}", max_width=320),
        icon=folium.Icon(color="red", icon="trash", prefix="fa"),
    ).add_to(fmap)

    st_folium(fmap, width=None, height=330, returned_objects=[], key=f"task_map_{row['id']}")


# =========================================================
# 7. TRANG GỬI BÁO CÁO
# =========================================================

def page_report():
    st.subheader("📷 Gửi báo cáo môi trường (Tích hợp AI Agent Thẩm định)")

    with st.container(border=True):
        name = st.text_input("Tên người báo cáo", placeholder="Nhập tên hoặc biệt danh")
        uploaded = st.file_uploader("Ảnh hiện trường", type=["jpg", "jpeg", "png", "webp"], help=f"Tối đa {MAX_IMAGE_MB} MB.")
        description = st.text_area("Mô tả hiện trường", placeholder="Ví dụ: Rác che nắp cống hoặc xà bần lấn chiếm vỉa hè...")

        st.markdown("### 📍 Chọn vị trí trên bản đồ")
        fmap = folium.Map(location=[DEFAULT_LAT, DEFAULT_LNG], zoom_start=13, control_scale=True)
        map_result = st_folium(fmap, width=None, height=340, returned_objects=["last_clicked"], key="citizen_location_map")

        latitude = None
        longitude = None
        if map_result and map_result.get("last_clicked"):
            latitude = map_result["last_clicked"].get("lat")
            longitude = map_result["last_clicked"].get("lng")
            st.success(f"📍 Tọa độ đã chọn: {latitude:.6f}, {longitude:.6f}")

        location = st.text_input("Địa chỉ / ghi chú vị trí (TP. Biên Hòa, Đồng Nai)", placeholder="Ví dụ: Đường Võ Thị Sáu, P. Quyết Thắng...")
        submit = st.button("🚀 Kích hoạt AI Agent Thẩm Định & Điều Phối", type="primary", use_container_width=True)

    if not submit:
        return

    if not uploaded:
        st.error("Vui lòng tải ảnh lên.")
        return

    saved_report_id = None  # chỉ có giá trị khi hồ sơ đã được ghi vào DB
    try:
        raw = uploaded.getvalue()
        prepared = prepare_image(raw)
        image_path = f"reports/{uuid.uuid4()}.jpg"
        storage_upload(prepared, image_path)

        try:
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

        saved_report_id = report_id
        st.success(f"✅ Đã tiếp nhận hồ sơ vào hệ thống! Mã báo cáo: {report_code(report_id)}")

        if not cloudflare_configured():
            save_ai_error(report_id, "Chưa cấu hình Cloudflare Workers AI.")
            st.warning("⚠️ Báo cáo đã lưu nhưng AI chưa được cấu hình.")
            return

        with st.status("🤖 AI Agent đang thẩm định hiện trường & phân cấp khẩn cấp...", expanded=True) as status_box:
            st.write("🔍 **Tác nhân 1 (Validation Agent):** Quét bối cảnh ngoại cảnh, kiểm tra ảnh chụp màn hình/mạng...")
            parsed, raw_ai = analyze_image_with_cloudflare(prepared)

            if not parsed.get("is_genuine_scene", True):
                st.write("❌ **Phát hiện gian lận:** Ảnh không phải cảnh quan thực tế ngoài trời hoặc chụp từ màn hình.")
                save_ai_result(report_id, parsed, raw_ai)
                status_box.update(label="⚠️ AI Agent từ chối báo cáo (Ảnh không hợp lệ)", state="error")
                st.error(f"Từ chối: {parsed.get('spam_reason') or 'Ảnh không đủ điều kiện thẩm định hiện trường.'}")
                return

            st.write("⚡ **Tác nhân 2 (Severity Scorer):** Phân tích nguy cơ nghẽn cống, rác độc hại và mức độ tràn lan...")
            st.write(f"&bull; Nguy cơ nghẽn cống: **{'CÓ' if parsed.get('clogging_drain_risk') else 'Không'}**")
            st.write(f"&bull; Rác độc hại/Pin: **{'CÓ' if parsed.get('hazardous_waste') else 'Không'}**")
            st.write(f"&bull; Mức độ đánh giá: **{parsed.get('severity')}**")

            st.write("📋 **Tác nhân 3 (Dispatcher Agent):** Lập phiếu điều phối công việc chuẩn hóa JSON...")
            status = save_ai_result(report_id, parsed, raw_ai)
            status_box.update(label="✅ AI Agent đã hoàn tất phân tích & điều phối!", state="complete")

        _load_stats_rows.clear()

        if status == "Đã duyệt":
            ticket = {
                "report_code": report_code(report_id),
                "confidence": parsed.get("confidence") or "Không rõ",
                "severity": parsed.get("severity"),
                "assigned_unit": parsed.get("assigned_unit"),
                "dispatch_plan": parsed.get("dispatch_plan"),
            }
            st.json(ticket)
            st.success(f"🤖 Đã phát lệnh điều phối đến: **{parsed.get('assigned_unit')}**.")
        else:
            st.warning(f"⚠️ Báo cáo được chuyển vào mục Spam/Từ chối. Lý do: {parsed.get('spam_reason') or 'AI không xác nhận có đủ rác thải.'}")

    except Exception as exc:
        # Nếu hồ sơ đã lưu mà AI lỗi -> đánh dấu "Lỗi AI" để admin bấm "Phân tích lại"
        if saved_report_id:
            try:
                save_ai_error(saved_report_id, exc)
                _load_stats_rows.clear()
                st.warning("⚠️ Hồ sơ đã được lưu, AI gặp lỗi tạm thời. Quản trị viên có thể phân tích lại sau.")
            except Exception:
                pass
        st.error(f"❌ Không thể xử lý báo cáo: {exc}")


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

    team_name = st.text_input("Tên đội / thành viên", placeholder="Ví dụ: Đội Vệ Sinh Phường Quyết Thắng", key="cleanup_team_name")

    approved = get_reports("Đã duyệt")
    st.markdown("## 📌 Nhiệm vụ chờ nhận")
    st.caption(f"{len(approved)} nhiệm vụ đang chờ đội dọn dẹp nhận.")

    if approved:
        for row in approved:
            code = report_title(row)
            ai_data = parse_ai_json(row)

            severity = ai_data.get("severity") or SEVERITY_LABELS["1"]
            level = severity_level(severity)
            badge_class = "agent-badge-urgent" if level == 3 else ("agent-badge-hazard" if level == 2 else "agent-badge-normal")

            with st.container(border=True):
                st.markdown(
                    f"<div class='cleanup-report-head'><span>📋 BÁO CÁO #{escape(code)}</span>"
                    f"<span><span class='{badge_class}'>{escape(str(severity))}</span> <span class='cleanup-status-pill'>CHỜ NHẬN</span></span></div>",
                    unsafe_allow_html=True,
                )
                cols = st.columns([0.95, 1.05])
                with cols[0]:
                    st.markdown(f"**📍 Địa điểm:** {row.get('location') or 'Chưa có địa chỉ'}")
                    st.markdown(f"**📝 Mô tả:** {row.get('description') or 'Không có mô tả'}")
                    if ai_data:
                        st.markdown(f"🗑️ **Loại rác:** {ai_data.get('waste_type') or 'Chưa rõ'}")
                        st.markdown(f"🏢 **Đơn vị chỉ định:** {ai_data.get('assigned_unit') or 'Đội môi trường'}")
                        st.markdown(f"🚚 **Phương án AI đề xuất:** {ai_data.get('dispatch_plan') or 'AI chưa đề xuất'}")

                    if st.button("📌 Nhận nhiệm vụ này", key=f"assign_{row['id']}", use_container_width=True):
                        if not team_name.strip():
                            st.error("Nhập tên đội trước.")
                        else:
                            changed = _supabase_update("reports", {"id": row["id"], "status": "Đã duyệt"}, {"assigned_team": team_name.strip(), "status": "Đang dọn"})
                            if changed:
                                st.success(f"Đã nhận nhiệm vụ #{code}.")
                            else:
                                st.warning("Nhiệm vụ này đã được đội khác nhận.")
                            _load_stats_rows.clear()
                            st.rerun()
                with cols[1]:
                    if not show_stored_image(row.get("image_path"), "Ảnh hiện trường"):
                        st.caption("Không tải được ảnh hiện trường.")
                show_task_map(row)
    else:
        st.info("Hiện chưa có nhiệm vụ mới đang chờ nhận.")

    active = get_reports("Đang dọn")
    st.markdown("## 🧹 Báo cáo sau dọn dẹp")
    if not active:
        st.info("Chưa có nhiệm vụ đang dọn.")
        return

    st.success(f"Bạn đang có {len(active)} nhiệm vụ cần hoàn thành.")
    for row in active:
        code = report_title(row)
        with st.container(border=True):
            st.markdown(
                f"<div class='cleanup-report-head'><span>🧹 BÁO CÁO #{escape(code)}</span><span class='cleanup-status-pill active'>ĐANG DỌN</span></div>",
                unsafe_allow_html=True,
            )
            st.caption(f"👷 Đội phụ trách: {row.get('assigned_team') or 'Chưa xác định'}  •  📍 {row.get('location') or 'Chưa có vị trí'}")
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**📷 Ảnh hiện trường**")
                show_stored_image(row.get("image_path"), "Ảnh trước khi dọn")
            with cols[1]:
                st.markdown("**📤 Ảnh sau khi dọn**")
                cleanup_photo = st.file_uploader("Tải ảnh hiện trường sau khi dọn", type=["jpg", "jpeg", "png", "webp"], key=f"cleanup_photo_{row['id']}")
                cleanup_note = st.text_area("📝 Ghi chú hoàn thành", placeholder="Ví dụ: Đã khơi thông cống và dọn sạch toàn bộ rác.", key=f"cleanup_note_{row['id']}")
                if st.button("✅ Xác nhận đã dọn xong", key=f"finish_{row['id']}", type="primary", use_container_width=True):
                    if not cleanup_photo:
                        st.error("Cần tải ảnh sau khi dọn.")
                    else:
                        try:
                            prepared = prepare_image(cleanup_photo.getvalue())
                            cleanup_path = f"cleanup/{row['id']}_{uuid.uuid4().hex}.jpg"
                            storage_upload(prepared, cleanup_path)
                            _supabase_update("reports", {"id": row["id"]}, {
                                "status": "Đã dọn",
                                "cleanup_image_path": cleanup_path,
                                "cleanup_note": cleanup_note.strip(),
                                "cleaned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                            })
                            _load_stats_rows.clear()
                            st.success(f"Đã hoàn thành báo cáo #{code}.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Lỗi: {exc}")
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
            st.markdown(f"**📋 Báo cáo #{report_title(row)}**")
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**Ảnh trước khi dọn**")
                show_stored_image(row.get("image_path"), "Ảnh trước khi dọn")
            with cols[1]:
                st.markdown("**Ảnh sau khi dọn**")
                show_stored_image(row.get("cleanup_image_path"), "Ảnh sau khi dọn")

            st.write(f"📍 **Vị trí:** {row.get('location') or 'Chưa có'}")
            st.write(f"🧹 **Đội:** {row.get('assigned_team') or 'Chưa có'}")
            st.write(f"📝 **Ghi chú:** {row.get('cleanup_note') or 'Không có'}")
            show_task_map(row)


# =========================================================
# 10. BẢNG XẾP HẠNG (tính từ bảng reports)
# =========================================================

def page_leaderboard():
    st.subheader("🏆 Bảng xếp hạng tích điểm")
    st.caption("Điểm được tính trực tiếp từ báo cáo trên Supabase: 5 điểm/báo cáo hợp lệ, 10 điểm/nhiệm vụ đã dọn.")
    try:
        reports = get_reports()
    except Exception as exc:
        st.error(f"Lỗi tải dữ liệu: {exc}")
        return

    user_totals = {}
    team_totals = {}
    valid_statuses = {"Đã duyệt", "Đang dọn", "Đã dọn"}
    for report in reports:
        status = str(report.get("status") or "")
        reporter = str(report.get("reporter_name") or "Ẩn danh").strip() or "Ẩn danh"
        if status in valid_statuses:
            user_totals[reporter] = user_totals.get(reporter, 0) + 5
        if status == "Đã dọn":
            team = str(report.get("assigned_team") or "Chưa ghi tên đội").strip() or "Chưa ghi tên đội"
            team_totals[team] = team_totals.get(team, 0) + 10

    users = [{"name": name, "points": points} for name, points in user_totals.items()]
    teams = [{"name": name, "points": points} for name, points in team_totals.items()]
    users.sort(key=lambda x: (-x["points"], x["name"].casefold()))
    teams.sort(key=lambda x: (-x["points"], x["name"].casefold()))

    def render_ranking(title, icon, rows):
        st.markdown(
            f"<div class='ranking-panel'><div class='ranking-panel-title'>{icon} {title}</div>"
            f"<div class='ranking-panel-sub'>Top {min(len(rows), 10)} thành tích cao nhất</div></div>",
            unsafe_allow_html=True,
        )
        if not rows:
            st.info("Chưa có dữ liệu điểm.")
            return
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows[:10], 1):
            medal = medals[index - 1] if index <= 3 else f"<span class='rank-number'>{index}</span>"
            name = escape(str(row.get("name") or "Không tên"))
            points = int(row.get("points") or 0)
            st.markdown(
                f"<div class='ranking-row'><div class='ranking-rank'>{medal}</div>"
                f"<div class='ranking-name'>{name}</div>"
                f"<div class='ranking-points'>{points:,} <small>điểm</small></div></div>",
                unsafe_allow_html=True,
            )

    left, right = st.columns(2)
    with left:
        render_ranking("Người dân", "👤", users)
    with right:
        render_ranking("Đội dọn dẹp", "🧹", teams)


# =========================================================
# 11. BẢN ĐỒ ĐIỂM NÓNG & GỢI Ý LỘ TRÌNH
# =========================================================

ACTIVE_STATUSES = ("Đã duyệt", "Đang dọn")

def page_hotspots():
    st.subheader("🗺️ Bản đồ điểm nóng & Gợi ý lộ trình")
    st.caption("Gom cụm báo cáo và gợi ý thứ tự ghé các điểm rác khẩn cấp (giải thuật láng giềng gần nhất, khoảng cách đường chim bay).")

    try:
        reports = get_reports()
    except Exception as exc:
        st.error(f"Không tải được dữ liệu: {exc}")
        return

    points = []
    urgent_points = []

    for row in reports:
        lat, lng = row.get("latitude"), row.get("longitude")
        if lat is None or lng is None:
            lat, lng = parse_location_coordinates(row.get("location"))
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        points.append((row, lat, lng))

        # Chỉ tính điểm khẩn cấp cho báo cáo còn cần xử lý (không phải Spam / Lỗi AI / Đã dọn)
        if row.get("status") in ACTIVE_STATUSES:
            ai_obj = parse_ai_json(row)
            if ai_obj and (severity_level(ai_obj.get("severity")) == 3
                           or ai_obj.get("clogging_drain_risk") is True
                           or ai_obj.get("hazardous_waste") is True):
                urgent_points.append((row, lat, lng))

    if not points:
        st.warning("Chưa có báo cáo nào có tọa độ hợp lệ.")
        return

    col_ctrl1, col_ctrl2 = st.columns([1.2, 0.8])
    with col_ctrl1:
        calc_route = st.checkbox("🚚 Gợi ý lộ trình thu gom qua các điểm khẩn cấp", value=False)
    with col_ctrl2:
        st.caption(f"Đang có **{len(urgent_points)}** điểm khẩn cấp Cấp 3 cần ưu tiên giải tỏa.")

    avg_lat = sum(p[1] for p in points) / len(points)
    avg_lng = sum(p[2] for p in points) / len(points)
    fmap = folium.Map(location=[avg_lat, avg_lng], zoom_start=13, control_scale=True)

    HeatMap(
        [[lat, lng, 1.0 if row.get("status") != "Đã dọn" else 0.35] for row, lat, lng in points],
        radius=28, blur=20, min_opacity=0.25,
    ).add_to(fmap)
    cluster = MarkerCluster(name="Báo cáo").add_to(fmap)

    for row, lat, lng in points:
        status = str(row.get("status") or "Chưa rõ")
        code = escape(report_title(row))
        location = escape(str(row.get("location") or "Chưa có địa chỉ"))

        color = "green" if status == "Đã dọn" else ("orange" if status == "Đang dọn" else "red")
        severity_label = "Bình thường"

        ai = parse_ai_json(row)
        if ai and severity_level(ai.get("severity")) == 3:
            color = "darkred" if status != "Đã dọn" else color
            severity_label = SEVERITY_LABELS["3"]

        popup = folium.Popup(
            f"<b>Báo cáo #{code}</b><br>Mức độ: <b>{escape(severity_label)}</b><br>"
            f"Trạng thái: {escape(status)}<br>Địa chỉ: {location}",
            max_width=320,
        )
        folium.Marker(
            [lat, lng],
            tooltip=f"#{code} · {severity_label}",
            popup=popup,
            icon=folium.Icon(color=color, icon="trash", prefix="fa"),
        ).add_to(cluster)

    if calc_route:
        if len(urgent_points) < 2:
            st.info("Cần ít nhất 2 điểm khẩn cấp để gợi ý lộ trình.")
        else:
            route_coords = [DEPOT]
            remaining = list(urgent_points)
            curr = DEPOT
            total_km = 0.0
            while remaining:
                best_idx, best_dist = 0, float("inf")
                for idx, item in enumerate(remaining):
                    d = haversine_km(curr[0], curr[1], item[1], item[2])
                    if d < best_dist:
                        best_dist, best_idx = d, idx
                chosen = remaining.pop(best_idx)
                total_km += best_dist
                curr = [chosen[1], chosen[2]]
                route_coords.append(curr)
            total_km += haversine_km(curr[0], curr[1], DEPOT[0], DEPOT[1])
            route_coords.append(DEPOT)

            folium.PolyLine(
                route_coords, color="#0284c7", weight=5, opacity=0.85,
                dash_array="10, 10", tooltip="Lộ trình gợi ý (đường chim bay)",
            ).add_to(fmap)
            folium.Marker(
                DEPOT, tooltip="🏢 Trạm xuất phát xe thu gom",
                icon=folium.Icon(color="blue", icon="truck", prefix="fa"),
            ).add_to(fmap)
            st.success(
                f"✅ Gợi ý ghé {len(urgent_points)} điểm khẩn cấp, tổng khoảng {total_km:.1f} km theo đường chim bay. "
                "Đây là thứ tự tham khảo, chưa tính đường thực tế."
            )

    folium.LayerControl(collapsed=True).add_to(fmap)
    st_folium(fmap, width=None, height=560, returned_objects=[], key="greeneye_hotspots_map")


# =========================================================
# 12. TRỢ LÝ MÔI TRƯỜNG SỐ 24/7
# =========================================================

STATUS_HINTS = {
    "Đang xử lý": "AI đang thẩm định hồ sơ.",
    "Đã duyệt": "Hồ sơ đã được duyệt, đang chờ đội dọn dẹp nhận.",
    "Đang dọn": "Đội dọn dẹp đang xử lý.",
    "Đã dọn": "Đã dọn xong.",
    "Spam/Từ chối": "Hồ sơ bị từ chối khi thẩm định.",
    "Lỗi AI": "AI gặp lỗi, đang chờ quản trị viên phân tích lại.",
}

def lookup_report_by_code(code_text):
    m = re.search(r"QL-?([0-9A-Fa-f]{6})", code_text, re.I)
    if not m:
        return None
    prefix = m.group(1).upper()
    rows = _supabase_select("reports", {"select": "id,status,location,assigned_team"})
    for row in rows:
        if report_code(row.get("id")) == f"QL-{prefix}":
            return row
    return False  # có mã nhưng không tìm thấy

def page_green_assistant():
    st.subheader("💬 Trợ Lý Môi Trường Số 24/7")
    st.caption("Tư vấn phân loại rác tại nguồn và tra cứu tiến độ hồ sơ theo mã QL-XXXXXX. Trợ lý trả lời theo từ khóa, không phải AI tự do.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [
            {"role": "assistant", "content": "Xin chào! Tôi là Trợ Lý Môi Trường Số của TP. Biên Hòa. Tôi có thể giúp gì cho bạn hôm nay?"}
        ]

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_query = st.chat_input("Nhập câu hỏi (VD: Vứt pin cũ ở đâu? Tra cứu QL-1A2B3C)...")
    if not user_query:
        return

    st.session_state.chat_history.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.write(user_query)

    q_lower = user_query.lower()
    if re.search(r"ql-?[0-9a-f]{6}", q_lower):
        try:
            found = lookup_report_by_code(user_query)
        except Exception as exc:
            found = None
            reply = f"⚠️ Chưa tra cứu được lúc này: {exc}"
        else:
            if found:
                status = found.get("status") or "Chưa rõ"
                reply = (
                    f"🔍 **{report_code(found.get('id'))}** — trạng thái: **{status}**. "
                    f"{STATUS_HINTS.get(status, '')}\n\n"
                    f"- Vị trí: {found.get('location') or 'Chưa có'}\n"
                    f"- Đội phụ trách: {found.get('assigned_team') or 'Chưa có'}"
                )
            else:
                reply = "Không tìm thấy hồ sơ với mã này. Bạn kiểm tra lại mã dạng QL-XXXXXX nhé."
    elif "pin" in q_lower or "ắc quy" in q_lower or "điện tử" in q_lower:
        reply = (
            "🔋 **Pin cũ, ắc quy, rác điện tử là rác nguy hại**, không bỏ chung rác sinh hoạt.\n"
            "- Gom riêng, bọc kín, tránh làm vỡ hoặc ngâm nước.\n"
            "- Hãy hỏi UBND phường hoặc đơn vị vệ sinh môi trường tại địa phương về điểm thu gom gần nhất."
        )
    elif "hữu cơ" in q_lower or "thực phẩm" in q_lower or "rau" in q_lower:
        reply = "🥬 **Phân loại rác hữu cơ tại nhà:**\n- Bao gồm: vỏ rau củ, thức ăn thừa, bã trà, lá cây.\n- Khuyến khích: để thùng riêng có nắp đậy hoặc ủ phân vi sinh bón cây."
    elif "tra cứu" in q_lower or "mã" in q_lower:
        reply = "🔍 Hãy gửi mã báo cáo dạng **QL-XXXXXX** (hiện sau khi bạn gửi báo cáo), tôi sẽ tra trạng thái giúp bạn."
    else:
        reply = "Cảm ơn bạn đã đồng hành bảo vệ mỹ quan đô thị Đồng Nai! Bạn có thể hỏi về phân loại rác, rác nguy hại hoặc tra cứu mã QL-XXXXXX."

    st.session_state.chat_history.append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.write(reply)


# =========================================================
# 13. SPAM & ADMIN
# =========================================================

def page_spam():
    st.subheader("🛡️ Báo cáo Spam & Xóa")
    pin = st.text_input("PIN nhân viên", type="password", key="staff_pin_input")
    if st.button("→ Vào khu vực Spam", key="staff_login", use_container_width=True):
        if unlock_with_pin(pin, "staff_unlocked"):
            st.success("Đã mở quyền truy cập.")
        else:
            st.error("Mã PIN không đúng.")

    if not st.session_state.get("staff_unlocked", False) and not st.session_state.get("admin_full_access", False):
        st.info("Nhập PIN rồi bấm nút để vào.")
        return

    rows = get_reports("Spam/Từ chối")
    st.caption(f"Báo cáo Spam/Từ chối sẽ tự động xóa sau {SPAM_RETENTION_DAYS} ngày.")
    if not rows:
        st.success("Không có báo cáo Spam.")
        return

    if st.button("🗑️ Xóa toàn bộ Spam ngay", type="secondary"):
        for row in rows:
            delete_report(row["id"])
        _load_stats_rows.clear()
        st.success("Đã xóa toàn bộ Spam.")
        st.rerun()

    for row in rows:
        with st.container(border=True):
            st.write(f"**ID:** {row['id']}  •  **Người gửi:** {row.get('reporter_name')}")
            if row.get("ai_result"):
                st.write(f"🤖 {row['ai_result']}")
            ai = parse_ai_json(row)
            if ai.get("spam_reason"):
                st.warning(f"**Lý do AI:** {ai['spam_reason']}")
            show_stored_image(row.get("image_path"))
            if st.button("🗑️ Xóa báo cáo này", key=f"delete_spam_{row['id']}"):
                delete_report(row["id"])
                _load_stats_rows.clear()
                st.rerun()

def page_admin():
    st.subheader("⚙️ Reset & Cài đặt AI")
    pin = st.text_input("PIN quản trị viên", type="password", key="admin_pin_input")
    if st.button("→ Vào khu vực quản trị", key="admin_login", use_container_width=True):
        if unlock_with_pin(pin, "admin_unlocked"):
            st.success("Đã mở toàn quyền quản trị.")
        else:
            st.error("Mã PIN không đúng.")

    if not st.session_state.get("admin_unlocked", False) and not st.session_state.get("admin_full_access", False):
        st.info("Nhập PIN rồi bấm nút để vào.")
        return

    st.success("🔐 Quyền quản trị: TOÀN QUYỀN")

    default_pins = [n for n, v in (("ADMIN_PIN", ADMIN_PIN), ("TEAM_PIN", TEAM_PIN), ("STAFF_PIN", STAFF_PIN)) if v == "1234"]
    if default_pins:
        st.warning(f"⚠️ Đang dùng PIN mặc định 1234 cho: {', '.join(default_pins)}. Hãy đặt giá trị riêng trong secrets.")

    if cloudflare_configured():
        st.success("🟢 Cloudflare Workers AI đã được cấu hình.")
    else:
        st.error("🔴 Chưa có CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_AUTH_TOKEN.")

    st.markdown(f"### 🤖 Model AI: `{CF_MODEL}`")
    if st.button("Tôi đã đọc và đồng ý điều khoản model → gửi agree", use_container_width=True):
        ok, msg = cloudflare_agree()
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    st.markdown("### 🔄 Báo cáo lỗi AI")
    errors = get_reports("Lỗi AI")
    if not errors:
        st.success("Không có báo cáo lỗi AI.")
    else:
        for row in errors:
            with st.container(border=True):
                st.write(f"**ID:** {row['id']}  •  **Mã:** {report_code(row['id'])}")
                st.error(row.get("ai_error") or "Lỗi không xác định.")
                if st.button("🤖 Phân tích lại", key=f"retry_{row['id']}"):
                    try:
                        img = download_stored_image(row["image_path"])
                        parsed, raw_ai = analyze_image_with_cloudflare(prepare_image(img))
                        save_ai_result(row["id"], parsed, raw_ai)
                        _load_stats_rows.clear()
                        st.success("Đã phân tích lại.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    st.markdown("### ⚠️ Reset database")
    confirm = st.checkbox("Tôi hiểu thao tác này sẽ xóa toàn bộ báo cáo.")
    if st.button("🗑️ RESET TOÀN BỘ DATABASE", disabled=not confirm):
        rows = _supabase_select("reports", {"select": "id,image_path,cleanup_image_path"})
        for row in rows:
            for p in [row.get("image_path"), row.get("cleanup_image_path")]:
                if p:
                    try:
                        storage_delete(p)
                    except Exception:
                        pass
            _supabase_delete_rows("reports", {"id": row["id"]})
        # Hai bảng điểm cũ không còn được dùng, nhưng vẫn dọn nếu tồn tại
        for table in ("user_points", "team_points"):
            try:
                for r in _supabase_select(table, {"select": "name"}):
                    _supabase_delete_rows(table, {"name": r["name"]})
            except Exception:
                pass
        _load_stats_rows.clear()
        st.success("Đã reset database và Storage.")
        st.rerun()


# =========================================================
# 14. SIDEBAR MENU & ROUTING
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
                        <div class="sidebar-brand-sub">Mắt Xanh Đô Thị (Đồng Nai)</div>
                    </div>
                </div>
            </div>

            <div class="sidebar-live">
                <span class="sidebar-live-dot"></span>
                Hệ thống Multi-Agent đang hoạt động
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
                "🗺  Bản đồ điểm nóng & Lộ trình",
                "💬  Trợ lý Môi trường Số 24/7",
                "★  Bảng xếp hạng tích điểm",
                "!  Báo cáo Spam & Xóa",
                "⚙  Reset & Cài đặt AI",
            ],
            label_visibility="collapsed",
        )

        st.markdown(
            """
            <div class="sidebar-section-title">Hệ thống AI Agent</div>
            <div class="sidebar-info">
                <b>1. Validation Agent</b><br>
                Chống gian lận & ảnh chụp màn hình.<br><br>
                <b>2. Severity Agent</b><br>
                Đánh giá rác độc & nguy cơ nghẽn cống.<br><br>
                <b>3. Dispatcher Agent</b><br>
                Lập phiếu xử lý & gợi ý lộ trình.
            </div>
            """,
            unsafe_allow_html=True,
        )

    return page

show_header()
show_stats()
page = sidebar_menu()

_previous_page = st.session_state.get("current_menu")
if _previous_page is None:
    st.session_state["current_menu"] = page
elif _previous_page != page:
    for _key in ("admin_unlocked", "admin_full_access", "team_unlocked", "staff_unlocked"):
        st.session_state[_key] = False
    for _key in ("admin_pin_input", "team_pin_input", "staff_pin_input"):
        st.session_state.pop(_key, None)
    st.session_state["current_menu"] = page

if page == "▣  Gửi báo cáo":
    page_report()
elif page == "◆  Đội dọn dẹp nhận nhiệm vụ":
    page_cleanup_team()
elif page == "✓  Danh sách đã dọn":
    page_completed()
elif page == "🗺  Bản đồ điểm nóng & Lộ trình":
    page_hotspots()
elif page == "💬  Trợ lý Môi trường Số 24/7":
    page_green_assistant()
elif page == "★  Bảng xếp hạng tích điểm":
    page_leaderboard()
elif page == "!  Báo cáo Spam & Xóa":
    page_spam()
elif page == "⚙  Reset & Cài đặt AI":
    page_admin()
