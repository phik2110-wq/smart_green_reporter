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
CF_AUTH_TOKEN = get_setting("CLOUDFLARE_AUTH_TOKEN")
ADMIN_PIN = get_setting("ADMIN_PIN", "1234")
TEAM_PIN = get_setting("TEAM_PIN", "1234")
STAFF_PIN = get_setting("STAFF_PIN", "1234")


os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANUP_DIR, exist_ok=True)


# =========================================================
# 2. CSS
# =========================================================

st.markdown(
    """
<style>
.stApp {
    background: #edf7ef;
}

.block-container {
    max-width: 1450px;
    padding-top: 1rem;
    padding-bottom: 2rem;
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #dff3e3 0%, #c9e9cf 100%);
}

[data-testid="stSidebar"] * {
    color: #164b2a !important;
}

.hero {
    background: linear-gradient(135deg, #0d6b35, #35a853);
    color: white;
    border-radius: 24px;
    padding: 30px;
    margin-bottom: 20px;
    box-shadow: 0 10px 30px rgba(20, 100, 45, .16);
}

.hero h1 {
    margin: 0;
    font-size: 2.2rem;
}

.hero p {
    margin: 8px 0 0;
    opacity: .92;
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
    color: #176b35;
}

.stat-label {
    color: #5c7863;
    font-size: .9rem;
}

.report-title {
    color: #125c2e;
    font-weight: 800;
    font-size: 1.15rem;
}

.status {
    display: inline-block;
    padding: 5px 12px;
    border-radius: 999px;
    background: #e3f4e7;
    color: #176b35;
    font-weight: 700;
    font-size: .85rem;
}

div[data-testid="stFileUploader"] {
    border: 1px dashed #75b982;
    border-radius: 16px;
    padding: 8px;
    background: #f7fcf8;
}

.stButton > button {
    border-radius: 12px;
    font-weight: 700;
}

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
}
</style>
""",
    unsafe_allow_html=True,
)


# =========================================================
# 3. DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_points (
            name TEXT PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS team_points (
            name TEXT PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at)")

    # Tương thích với DB cũ
    columns = {row["name"] for row in cur.execute("PRAGMA table_info(reports)").fetchall()}

    extra_columns = {
        "latitude": "REAL",
        "longitude": "REAL",
        "ai_error": "TEXT",
        "cleaned_at": "TEXT",
    }

    for column, dtype in extra_columns.items():
        if column not in columns:
            cur.execute(f"ALTER TABLE reports ADD COLUMN {column} {dtype}")

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
        return False, "Thiếu CLOUDFLARE_ACCOUNT_ID hoặc CLOUDFLARE_AUTH_TOKEN."

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
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("AI không trả về JSON hợp lệ.")

    return json.loads(match.group(0))


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


def analyze_image_with_cloudflare(image_bytes: bytes):
    if not cloudflare_configured():
        raise RuntimeError(
            "Chưa cấu hình CLOUDFLARE_ACCOUNT_ID và CLOUDFLARE_AUTH_TOKEN."
        )

    prompt = """
Bạn là AI chuyên phân tích ảnh phản ánh rác thải đô thị.

NHIỆM VỤ:
- Kiểm tra ảnh có thực sự có rác thải nhìn thấy rõ hay không.
- Chỉ công nhận là báo cáo rác khi có bằng chứng trực quan rõ ràng.
- Nếu ảnh là chân dung, selfie, ảnh người, ảnh nhóm người, ảnh thẻ,
  ảnh khuôn mặt hoặc chỉ có người mà không có rác rõ ràng: contains_waste=false.
- Người xuất hiện nhỏ ở nền ảnh không phải là rác.
- Nếu không chắc chắn: contains_waste=false.
- Không suy đoán những thứ không nhìn thấy.
- Không được dùng câu xử lý cố định. dispatch_plan phải do AI tự tạo
  dựa trên rác thực tế trong ảnh.
- Nếu không có rác, dispatch_plan phải là chuỗi rỗng.
- is_waste_amount_sufficient chỉ true khi lượng rác nhìn thấy đủ để
  trở thành một phản ánh môi trường hợp lệ.
- Trả lời hoàn toàn bằng tiếng Việt.
- CHỈ trả về một JSON object, không markdown, không giải thích bên ngoài JSON.

JSON bắt buộc:
{
  "contains_waste": true,
  "is_waste_amount_sufficient": true,
  "natural_report": "Nhận xét tự nhiên bằng tiếng Việt.",
  "waste_type": "Loại rác nếu có, nếu không có thì chuỗi rỗng.",
  "severity": "Mức độ nếu có, nếu không có thì chuỗi rỗng.",
  "visual_evidence": "Những gì thực sự nhìn thấy trong ảnh.",
  "spam_reason": "Lý do không hợp lệ nếu không có rác, nếu hợp lệ thì chuỗi rỗng.",
  "dispatch_plan": "Phương án xử lý do AI tự đề xuất nếu có rác."
}
"""

    payload = {
        "prompt": prompt,
        "image": image_to_data_uri(image_bytes),
        "max_tokens": 500,
        "temperature": 0,
    }

    response = requests.post(
        cloudflare_url(),
        headers={
            "Authorization": f"Bearer {CF_AUTH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )

    if not response.ok:
        raise RuntimeError(
            f"Cloudflare HTTP {response.status_code}: {response.text[:1000]}"
        )

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[:1500])

    result = data.get("result")

    if isinstance(result, dict):
        text = (
            result.get("response")
            or result.get("text")
            or result.get("output")
            or json.dumps(result, ensure_ascii=False)
        )
    else:
        text = str(result)

    parsed = extract_json_object(text)

    parsed["contains_waste"] = normalize_bool(parsed.get("contains_waste"))
    parsed["is_waste_amount_sufficient"] = normalize_bool(
        parsed.get("is_waste_amount_sufficient")
    )

    for key in [
        "natural_report",
        "waste_type",
        "severity",
        "visual_evidence",
        "spam_reason",
        "dispatch_plan",
    ]:
        parsed[key] = str(parsed.get(key, "") or "").strip()

    return parsed, data


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
    report_id = str(uuid.uuid4())
    created_at = datetime.datetime.now().isoformat(timespec="seconds")

    conn = get_db()
    conn.execute(
        """
        INSERT INTO reports (
            id, reporter_name, image_path, description, location,
            latitude, longitude, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Đang xử lý', ?)
        """,
        (
            report_id,
            reporter_name,
            image_path,
            description,
            location,
            latitude,
            longitude,
            created_at,
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

    cols = st.columns(4)

    stats = [
        ("📋", total, "Tổng báo cáo"),
        ("🤖", approved, "AI xác nhận"),
        ("⏳", pending, "Đang xử lý"),
        ("🧹", cleaned, "Đã dọn"),
    ]

    for col, (icon, number, label) in zip(cols, stats):
        with col:
            st.markdown(
                f"""
                <div class="stat-card">
                    <div>{icon}</div>
                    <div class="stat-number">{number}</div>
                    <div class="stat-label">{label}</div>
                </div>
                """,
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
        f'<div class="report-title">📋 Báo cáo {row["id"][:8]}</div>',
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

            # AI chạy ngay sau khi báo cáo được lưu.
            if not cloudflare_configured():
                save_ai_error(
                    report_id,
                    "Chưa cấu hình Cloudflare Workers AI.",
                )
                st.warning(
                    "Đã lưu báo cáo nhưng chưa thể phân tích AI vì "
                    "chưa cấu hình Cloudflare."
                )
                return

            with st.spinner("🤖 AI đang phân tích ảnh..."):
                parsed, raw_ai = analyze_image_with_cloudflare(prepared)

            status = save_ai_result(
                report_id,
                parsed,
                raw_ai,
            )

            if status == "Đã duyệt":
                add_user_points(name.strip() or "Ẩn danh", 5)
                st.success(
                    "✅ Báo cáo hợp lệ và đã được AI xác nhận."
                )
            else:
                st.warning(
                    "⚠️ AI xác định báo cáo không hợp lệ / không đủ "
                    "bằng chứng về rác."
                )

            st.json(parsed)

        except Exception as exc:
            st.error(f"Không thể xử lý báo cáo: {exc}")


# =========================================================
# 8. ĐỘI DỌN DẸP
# =========================================================

def page_cleanup_team():
    st.subheader("🧹 Đội dọn dẹp nhận nhiệm vụ")

    pin = st.text_input(
        "Mã PIN đội dọn dẹp",
        type="password",
    )

    if pin != TEAM_PIN:
        st.info("Nhập đúng PIN để xem nhiệm vụ.")
        return

    team_name = st.text_input(
        "Tên đội / thành viên",
        placeholder="Ví dụ: Đội Môi Trường A3",
    )

    approved = get_reports("Đã duyệt")

    if not approved:
        st.success("Hiện chưa có nhiệm vụ đã được AI xác nhận.")
        return

    st.markdown("### 📌 Nhiệm vụ chờ nhận")

    for row in approved:
        with st.container(border=True):
            cols = st.columns([1, 1])

            with cols[0]:
                st.markdown(f"**Báo cáo:** `{row['id'][:8]}`")
                st.write(f"📍 {row['location'] or 'Chưa có địa chỉ'}")
                st.write(f"📝 {row['description'] or 'Không có mô tả'}")

                if row["ai_raw_json"]:
                    try:
                        ai = json.loads(row["ai_raw_json"])
                        st.write(
                            f"🗑️ **Loại rác:** {ai.get('waste_type', '')}"
                        )
                        st.write(
                            f"⚠️ **Mức độ:** {ai.get('severity', '')}"
                        )
                        st.write(
                            f"🚚 **Phương án:** {ai.get('dispatch_plan', '')}"
                        )
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
                            WHERE id = ?
                            """,
                            (team_name.strip(), row["id"]),
                        )
                        conn.commit()
                        conn.close()
                        st.rerun()

            with cols[1]:
                if row["image_path"] and os.path.exists(row["image_path"]):
                    st.image(
                        row["image_path"],
                        use_container_width=True,
                    )

            st.markdown("#### 📍 Vị trí trên bản đồ")
            show_task_map(row)

    active = get_reports("Đang dọn")

    if active:
        st.markdown("### 🧹 Nhiệm vụ đang dọn")

    for row in active:
        with st.container(border=True):
            st.markdown(
                f"**Đội:** {row['assigned_team'] or 'Chưa xác định'}"
            )

            if row["image_path"] and os.path.exists(row["image_path"]):
                st.image(
                    row["image_path"],
                    caption="Ảnh trước khi dọn",
                    use_container_width=True,
                )

            st.markdown("#### 📍 Vị trí nhiệm vụ")
            show_task_map(row)

            cleanup_photo = st.file_uploader(
                "Ảnh sau khi dọn",
                type=["jpg", "jpeg", "png", "webp"],
                key=f"cleanup_photo_{row['id']}",
            )

            cleanup_note = st.text_area(
                "Ghi chú hoàn thành",
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
                    continue

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

                    st.success("Đã ghi nhận hoàn thành nhiệm vụ.")
                    st.rerun()

                except Exception as exc:
                    st.error(f"Lỗi: {exc}")


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

def page_leaderboard():
    st.subheader("🏆 Bảng xếp hạng tích điểm")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 👤 Người dân")

        conn = get_db()
        users = conn.execute(
            """
            SELECT name, points
            FROM user_points
            ORDER BY points DESC, name ASC
            LIMIT 50
            """
        ).fetchall()
        conn.close()

        for index, row in enumerate(users, 1):
            st.write(
                f"**{index}. {row['name']}** — {row['points']} điểm"
            )

    with col2:
        st.markdown("### 🧹 Đội dọn dẹp")

        conn = get_db()
        teams = conn.execute(
            """
            SELECT name, points
            FROM team_points
            ORDER BY points DESC, name ASC
            LIMIT 50
            """
        ).fetchall()
        conn.close()

        for index, row in enumerate(teams, 1):
            st.write(
                f"**{index}. {row['name']}** — {row['points']} điểm"
            )


# =========================================================
# 11. SPAM
# =========================================================

def page_spam():
    st.subheader("🛡️ Báo cáo Spam & Xóa")

    pin = st.text_input(
        "PIN nhân viên",
        type="password",
    )

    if pin != STAFF_PIN:
        st.info("Nhập đúng PIN để xem báo cáo Spam.")
        return

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
    )

    if pin != ADMIN_PIN:
        st.info("Nhập đúng PIN quản trị viên.")
        return

    if cloudflare_configured():
        st.success("🟢 Cloudflare Workers AI đã được cấu hình.")
    else:
        st.error(
            "🔴 Chưa có CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_AUTH_TOKEN."
        )

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
            <div style="
                font-size:1.25rem;
                font-weight:800;
                margin-bottom:12px;
            ">
                🌱 Urban GreenEye AI
            </div>
            """,
            unsafe_allow_html=True,
        )

        page = st.radio(
            "MENU",
            [
                "📷 Gửi báo cáo",
                "🧹 Đội dọn dẹp nhận nhiệm vụ",
                "✅ Danh sách đã dọn",
                "🏆 Bảng xếp hạng tích điểm",
                "🛡️ Báo cáo Spam & Xóa",
                "⚙️ Reset & Cài đặt AI",
            ],
        )

        st.markdown("---")
        st.caption(
            "AI phân tích ảnh tự động sau khi người dân gửi báo cáo."
        )

    return page


# =========================================================
# 14. MAIN
# =========================================================

show_header()
show_stats()

page = sidebar_menu()

if page == "📷 Gửi báo cáo":
    page_report()

elif page == "🧹 Đội dọn dẹp nhận nhiệm vụ":
    page_cleanup_team()

elif page == "✅ Danh sách đã dọn":
    page_completed()

elif page == "🏆 Bảng xếp hạng tích điểm":
    page_leaderboard()

elif page == "🛡️ Báo cáo Spam & Xóa":
    page_spam()

elif page == "⚙️ Reset & Cài đặt AI":
    page_admin()
