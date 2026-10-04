import os
import json
import base64
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO

import requests
import folium
import streamlit as st
from PIL import Image
from streamlit_folium import st_folium


# ============================================================
# 1. CẤU HÌNH TRANG
# ============================================================

st.set_page_config(
    page_title="Urban GreenEye AI",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>

    .stApp {
        .stApp {
    background: linear-gradient(
        135deg,
        #e8f5e9 0%,
        #f5fbf5 50%,
        #e3f2fd 100%
    );
}

/* Khung nội dung chính */
[data-testid="stMainBlockContainer"] {
    background: #ffffff !important;
    border: 2px solid #a5d6a7 !important;
    border-radius: 20px !important;
    padding: 28px !important;
    box-shadow: 0 6px 20px rgba(46, 125, 50, 0.10) !important;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #e8f5e9 !important;
    border-right: 2px solid #a5d6a7 !important;
}

/* Nút */
.stButton > button {
    border-radius: 10px !important;
    font-weight: 700 !important;
}

    .stAlert {
        border-radius: 10px !important;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 2. CẤU HÌNH HỆ THỐNG
# ============================================================

DB_FILE = "reports.db"

UPLOAD_DIR = "uploaded_images"
CLEANUP_DIR = "cleanup_images"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANUP_DIR, exist_ok=True)


# ============================================================
# 3. CLOUDFLARE
# ============================================================

CF_ACCOUNT_ID = st.secrets.get(
    "CLOUDFLARE_ACCOUNT_ID",
    os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
)

CF_AUTH_TOKEN = st.secrets.get(
    "CLOUDFLARE_AUTH_TOKEN",
    os.getenv("CLOUDFLARE_AUTH_TOKEN", "")
)

CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"


# ============================================================
# 4. PIN
# ============================================================

ADMIN_PIN = str(
    st.secrets.get(
        "ADMIN_PIN",
        os.getenv("ADMIN_PIN", "9999")
    )
)

TEAM_PIN = str(
    st.secrets.get(
        "TEAM_PIN",
        os.getenv("TEAM_PIN", "5555")
    )
)

STAFF_PIN = str(
    st.secrets.get(
        "STAFF_PIN",
        os.getenv("STAFF_PIN", "1234")
    )
)


# ============================================================
# 5. DATABASE
# ============================================================

def get_conn():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_name TEXT DEFAULT 'Vô danh',
            image_path TEXT DEFAULT '',
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            latitude REAL,
            longitude REAL,
            status TEXT DEFAULT 'Đã nhận',
            ai_result TEXT DEFAULT '',
            ai_raw_json TEXT DEFAULT '',
            ai_analyzed INTEGER DEFAULT 0,
            ai_waste_type TEXT DEFAULT '',
            ai_severity TEXT DEFAULT '',
            ai_visual_evidence TEXT DEFAULT '',
            ai_spam_reason TEXT DEFAULT '',
            ai_dispatch_plan TEXT DEFAULT '',
            assigned_team TEXT DEFAULT '',
            cleanup_image_path TEXT DEFAULT '',
            cleanup_note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_points (
            username TEXT PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_points (
            team_name TEXT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            tasks_completed INTEGER DEFAULT 0
        )
        """
    )

    conn.commit()
    conn.close()


def add_column_if_missing(column_name, column_type, default_sql=None):
    conn = get_conn()
    columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(reports)").fetchall()
    ]

    if column_name not in columns:
        sql = f"ALTER TABLE reports ADD COLUMN {column_name} {column_type}"
        if default_sql is not None:
            sql += f" DEFAULT {default_sql}"
        conn.execute(sql)
        conn.commit()

    conn.close()


def migrate_db():
    init_db()
    columns = [
        ("reporter_name", "TEXT", "'Vô danh'"),
        ("latitude", "REAL", None),
        ("longitude", "REAL", None),
        ("ai_raw_json", "TEXT", "''"),
        ("ai_analyzed", "INTEGER", "0"),
        ("ai_waste_type", "TEXT", "''"),
        ("ai_severity", "TEXT", "''"),
        ("ai_visual_evidence", "TEXT", "''"),
        ("ai_spam_reason", "TEXT", "''"),
        ("ai_dispatch_plan", "TEXT", "''"),
        ("assigned_team", "TEXT", "''"),
        ("cleanup_image_path", "TEXT", "''"),
        ("cleanup_note", "TEXT", "''"),
    ]

    for column_name, column_type, default_sql in columns:
        add_column_if_missing(column_name, column_type, default_sql)


migrate_db()


# ============================================================
# 6. XÓA SPAM CŨ HƠN 7 NGÀY
# ============================================================

def auto_delete_old_spam():
    cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()

    rows = conn.execute(
        """
        SELECT id, image_path, cleanup_image_path
        FROM reports
        WHERE status = 'Spam/Từ chối' AND created_at <= ?
        """,
        (cutoff,)
    ).fetchall()

    deleted = 0
    for row in rows:
        paths = [row["image_path"], row["cleanup_image_path"]]
        for path in paths:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        conn.execute("DELETE FROM reports WHERE id = ?", (row["id"],))
        deleted += 1

    conn.commit()
    conn.close()
    return deleted


auto_delete_old_spam()


# ============================================================
# 7. QUẢN LÝ ĐIỂM
# ============================================================

def add_user_points(username, points):
    if not username or not username.strip():
        return

    username = username.strip()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO user_points (username, points)
        VALUES (?, ?)
        ON CONFLICT(username)
        DO UPDATE SET points = points + excluded.points
        """,
        (username, points)
    )
    conn.commit()
    conn.close()


def add_team_points(team_name, points):
    if not team_name or not team_name.strip():
        return

    team_name = team_name.strip()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO team_points (team_name, points, tasks_completed)
        VALUES (?, ?, 1)
        ON CONFLICT(team_name)
        DO UPDATE SET
            points = points + excluded.points,
            tasks_completed = tasks_completed + 1
        """,
        (team_name, points)
    )
    conn.commit()
    conn.close()


# ============================================================
# 8. XÓA BÁO CÁO
# ============================================================

def delete_reports_by_ids(report_ids):
    conn = get_conn()
    for report_id in report_ids:
        row = conn.execute(
            "SELECT image_path, cleanup_image_path FROM reports WHERE id = ?",
            (report_id,)
        ).fetchone()

        if row:
            for path in [row["image_path"], row["cleanup_image_path"]]:
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))

    conn.commit()
    conn.close()


# ============================================================
# 9. RESET DATABASE
# ============================================================

def reset_database():
    conn = get_conn()
    rows = conn.execute("SELECT image_path, cleanup_image_path FROM reports").fetchall()

    for row in rows:
        for path in [row["image_path"], row["cleanup_image_path"]]:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    conn.execute("DELETE FROM reports")
    conn.execute("DELETE FROM user_points")
    conn.execute("DELETE FROM team_points")

    try:
        conn.execute("DELETE FROM sqlite_sequence WHERE name='reports'")
    except sqlite3.Error:
        pass

    conn.commit()
    conn.close()


# ============================================================
# 10. CLOUDFLARE FUNCTIONS
# ============================================================

def cloudflare_configured():
    return bool(CF_ACCOUNT_ID.strip() and CF_AUTH_TOKEN.strip())


def cloudflare_url():
    return (
        "https://api.cloudflare.com/client/v4/"
        f"accounts/{CF_ACCOUNT_ID}/ai/run/"
        f"{CF_MODEL}"
    )


def agree_cloudflare_model():
    if not cloudflare_configured():
        return {"success": False, "error": "Chưa cấu hình Cloudflare."}

    try:
        response = requests.post(
            cloudflare_url(),
            headers={
                "Authorization": f"Bearer {CF_AUTH_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"prompt": "agree"},
            timeout=30,
        )

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:1000]}"
            }

        result = response.json()
        return {
            "success": bool(result.get("success", False)),
            "result": result
        }
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


# ============================================================
# 11. CHUẨN HÓA ẢNH
# ============================================================

def prepare_image(image_path, max_side=1024, quality=82):
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# ============================================================
# 12. PARSE BOOLEAN
# ============================================================

def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in ["true", "1", "yes", "y", "có", "co", "đúng", "dung"]:
        return True
    if text in ["false", "0", "no", "n", "không", "khong", "sai"]:
        return False

    return default


# ============================================================
# 13. PARSE KẾT QUẢ AI
# ============================================================

def parse_ai_text(raw_input):
    if isinstance(raw_input, dict):
        data = raw_input
    else:
        text = str(raw_input or "").strip()
        text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        data = None

        try:
            data = json.loads(text)
        except Exception:
            pass

        if not isinstance(data, dict):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except Exception:
                    pass

        if not isinstance(data, dict):
            upper = text.upper()
            negative_words = [
                "KHÔNG CÓ RÁC", "KHÔNG CÓ RÁC THẢI", "ẢNH CHÂN DUNG",
                "KHUÔN MẶT", "GƯƠNG MẶT", "SELFIE", "PORTRAIT", "NO WASTE", "NO TRASH"
            ]
            positive_words = [
                "RÁC THẢI", "RÁC SINH HOẠT", "BÃI RÁC", "CHẤT THẢI",
                "TRASH", "GARBAGE", "WASTE", "LITTER"
            ]

            has_negative = any(word in upper for word in negative_words)
            has_positive = any(word in upper for word in positive_words)
            contains_waste = has_positive and not has_negative

            data = {
                "contains_waste": contains_waste,
                "is_waste_amount_sufficient": contains_waste,
                "natural_report": text,
                "waste_type": "",
                "severity": "",
                "visual_evidence": text,
                "spam_reason": text if not contains_waste else "",
                "dispatch_plan": "",
            }

    contains_waste = parse_bool(data.get("contains_waste", False))
    sufficient = parse_bool(data.get("is_waste_amount_sufficient", contains_waste), contains_waste)

    return {
        "contains_waste": contains_waste,
        "is_waste_amount_sufficient": sufficient,
        "natural_report": str(data.get("natural_report", "")).strip(),
        "waste_type": str(data.get("waste_type", "")).strip(),
        "severity": str(data.get("severity", "")).strip(),
        "visual_evidence": str(data.get("visual_evidence", "")).strip(),
        "spam_reason": str(data.get("spam_reason", "")).strip(),
        "dispatch_plan": str(data.get("dispatch_plan", "")).strip(),
    }


# ============================================================
# 14. PHÂN TÍCH ẢNH BẰNG CLOUDFLARE
# ============================================================

def analyze_image_with_cloudflare(image_path):
    try:
        if not cloudflare_configured():
            return {
                "success": False,
                "error": "Cloudflare AI chưa được cấu hình."
            }

        image_bytes = prepare_image(image_path)
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        image_data_uri = f"data:image/jpeg;base64,{image_base64}"

        prompt = """
Bạn là AI kiểm định ảnh phản ánh rác thải đô thị.

MỤC TIÊU DUY NHẤT:
Xác định xem trong hình ảnh có RÁC THẢI THỰC TẾ đang nhìn thấy hay không.

QUY TẮC NGHIÊM NGẶT:
1. Người không phải là rác.
2. Khuôn mặt không phải là rác.
3. Ảnh chân dung không phải là ảnh rác.
4. Ảnh selfie không phải là ảnh rác.
5. Ảnh thẻ không phải là ảnh rác.
6. Quần áo đang mặc không phải là rác.
7. Tóc và cơ thể người không phải là rác.
8. Nếu ảnh chủ yếu là một người hoặc khuôn mặt và không nhìn thấy rác thực tế, contains_waste phải là false.
9. Không được suy đoán rằng có rác ngoài khung hình.
10. Không được coi người, khuôn mặt, quần áo hoặc cơ thể là rác.
11. Không được đánh dấu có rác chỉ vì ảnh trông giống một ảnh phản ánh môi trường.
12. Chỉ đánh dấu true khi nhìn thấy vật thể rác/chất thải thực tế.
13. Nếu không chắc chắn thì chọn false.
14. Nếu phía sau người thực sự có rác nhìn thấy được, bỏ qua người và kiểm tra môi trường phía sau.
15. Không được tự tạo ra vật thể không nhìn thấy.

NẾU CÓ RÁC:
- Mô tả loại rác.
- Mô tả những gì thực sự nhìn thấy.
- Ước lượng mức độ.
- Đề xuất phương án xử lý phù hợp với chính hình ảnh.

NẾU KHÔNG CÓ RÁC:
- Nêu rõ rằng ảnh không thể hiện rác thực tế.
- Giải thích ngắn gọn lý do ảnh không hợp lệ.

QUAN TRỌNG:
Không được sử dụng câu điều phối cố định.
dispatch_plan phải được AI tự quyết định dựa trên loại rác và mức độ thực tế trong ảnh.
natural_report phải là nhận xét tự nhiên bằng tiếng Việt, không phải một mẫu câu cố định.

CHỈ TRẢ VỀ JSON.

Cấu trúc:
{
    "contains_waste": true hoặc false,
    "is_waste_amount_sufficient": true hoặc false,
    "natural_report": "Nhận xét tự nhiên bằng tiếng Việt.",
    "waste_type": "Loại rác nếu thực sự nhìn thấy.",
    "severity": "Mức độ nếu thực sự có rác.",
    "visual_evidence": "Những gì thực sự nhìn thấy trong ảnh.",
    "spam_reason": "Lý do không hợp lệ nếu không có rác.",
    "dispatch_plan": "Phương án xử lý do AI tự đề xuất nếu có rác."
}
"""

        response = requests.post(
            cloudflare_url(),
            headers={
                "Authorization": f"Bearer {CF_AUTH_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": prompt,
                "image": image_data_uri,
                "max_tokens": 500,
                "temperature": 0.05,
            },
            timeout=90,
        )

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"Cloudflare HTTP {response.status_code}: {response.text[:2000]}"
            }

        try:
            result = response.json()
        except Exception:
            return {
                "success": False,
                "error": "Cloudflare trả về dữ liệu không phải JSON."
            }

        if not result.get("success", False):
            return {
                "success": False,
                "error": json.dumps(result, ensure_ascii=False)
            }

        raw_response = result.get("result", {}).get("response", "")
        if not raw_response:
            return {
                "success": False,
                "error": "Cloudflare không trả về nội dung phân tích."
            }

        parsed = parse_ai_text(raw_response)
        return {
            "success": True,
            "parsed": parsed,
            "raw": raw_response,
        }

    except requests.Timeout:
        return {"success": False, "error": "Cloudflare quá thời gian chờ 90 giây."}
    except requests.RequestException as e:
        return {"success": False, "error": f"Lỗi kết nối Cloudflare: {e}"}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


# ============================================================
# 15. LƯU KẾT QUẢ AI
# ============================================================

def save_ai_result(report_id, analysis):
    conn = get_conn()

    if not analysis.get("success", False):
        conn.execute(
            """
            UPDATE reports
            SET status = ?, ai_result = ?, ai_raw_json = ?, ai_analyzed = 0
            WHERE id = ?
            """,
            (
                "Lỗi AI",
                "AI chưa thể phân tích hình ảnh.",
                json.dumps(analysis, ensure_ascii=False),
                report_id,
            )
        )
        conn.commit()
        conn.close()
        return False

    parsed = analysis.get("parsed", {})
    contains_waste = bool(parsed.get("contains_waste", False))
    sufficient = bool(parsed.get("is_waste_amount_sufficient", contains_waste))
    natural_report = str(parsed.get("natural_report", "")).strip()
    visual_evidence = str(parsed.get("visual_evidence", "")).strip()
    spam_reason = str(parsed.get("spam_reason", "")).strip()
    waste_type = str(parsed.get("waste_type", "")).strip()
    severity = str(parsed.get("severity", "")).strip()
    dispatch_plan = str(parsed.get("dispatch_plan", "")).strip()

    if not natural_report:
        if contains_waste:
            natural_report = visual_evidence or "AI xác định có dấu hiệu rác thải trong hình ảnh."
        else:
            natural_report = spam_reason or "AI không xác định được rác thải thực tế trong hình ảnh."

    status = "Đã duyệt" if (contains_waste and sufficient) else "Spam/Từ chối"

    conn.execute(
        """
        UPDATE reports
        SET status = ?, ai_result = ?, ai_raw_json = ?, ai_analyzed = 1,
            ai_waste_type = ?, ai_severity = ?, ai_visual_evidence = ?,
            ai_spam_reason = ?, ai_dispatch_plan = ?
        WHERE id = ?
        """,
        (
            status,
            natural_report,
            json.dumps(parsed, ensure_ascii=False),
            waste_type,
            severity,
            visual_evidence,
            spam_reason,
            dispatch_plan,
            report_id,
        )
    )

    conn.commit()
    conn.close()
    return True


# ============================================================
# 16. SESSION STATE
# ============================================================

if "team_auth_ok" not in st.session_state:
    st.session_state["team_auth_ok"] = False

if "staff_auth_ok" not in st.session_state:
    st.session_state["staff_auth_ok"] = False

if "admin_auth_ok" not in st.session_state:
    st.session_state["admin_auth_ok"] = False

if "last_menu" not in st.session_state:
    st.session_state["last_menu"] = ""


# ============================================================
# 17. SIDEBAR
# ============================================================

st.sidebar.title("🌱 Urban GreenEye")

menu = st.sidebar.radio(
    "Điều hướng",
    [
        "📷 Gửi báo cáo",
        "🧹 Đội dọn dẹp nhận nhiệm vụ",
        "✅ Danh sách đã dọn",
        "🏆 Bảng xếp hạng tích điểm",
        "🗑️ Báo cáo Spam & Xóa",
        "⚙️ Reset & Cài đặt AI",
    ]
)

if st.session_state["last_menu"] != menu:
    st.session_state["team_auth_ok"] = False
    st.session_state["staff_auth_ok"] = False
    st.session_state["admin_auth_ok"] = False
    st.session_state["last_menu"] = menu


# ============================================================
# 18. HEADER
# ============================================================

st.title("🌳 Urban GreenEye AI")
st.caption("Hệ thống AI tự động kiểm định và quản lý phản ánh rác thải đô thị")


# ============================================================
# 19. GỬI BÁO CÁO
# ============================================================

if menu == "📷 Gửi báo cáo":

    st.header("📷 Gửi báo cáo điểm rác")
    st.info("Ảnh sẽ được lưu trước, sau đó Cloudflare Vision tự động kiểm định.")

    reporter_name = st.text_input("👤 Tên/Mã người báo cáo (*)", placeholder="Nguyễn Văn A")
    uploaded_file = st.file_uploader("📷 Tải ảnh hiện trường (*)", type=["jpg", "jpeg", "png", "webp"])

    if uploaded_file is not None:
        try:
            preview = Image.open(uploaded_file)
            st.image(preview, caption="Ảnh sẽ được gửi", width=400)
        except Exception:
            st.error("Ảnh không hợp lệ.")

    st.write("📍 **Chọn vị trí trên bản đồ (*)**")
    map_col1, map_col2 = st.columns([3, 1])

    with map_col1:
        m = folium.Map(location=[10.762622, 106.660172], zoom_start=12)
        m.add_child(folium.LatLngPopup())
        map_data = st_folium(m, height=320, use_container_width=True, key="report_map")

    selected_location = ""
    latitude = None
    longitude = None

    if map_data and map_data.get("last_clicked"):
        latitude = float(map_data["last_clicked"]["lat"])
        longitude = float(map_data["last_clicked"]["lng"])
        selected_location = f"Tọa độ: {latitude:.6f}, {longitude:.6f}"
        st.success("📌 Đã chọn vị trí: " + selected_location)

    manual_location = st.text_input("📍 Nhập địa chỉ cụ thể (*) nếu không chọn bản đồ", placeholder="Số nhà, tên đường...")
    final_location = manual_location.strip() or selected_location
    description = st.text_area("📝 Mô tả thêm (không bắt buộc)", placeholder="Ví dụ: Rác xuất hiện bên lề đường...")

    send_report = st.button("🚀 GỬI BÁO CÁO", type="primary", use_container_width=True, key="send_report_button")

    if send_report:
        missing = []
        if not reporter_name.strip():
            missing.append("Tên/Mã người báo cáo")
        if uploaded_file is None:
            missing.append("Ảnh hiện trường")
        if not final_location.strip():
            missing.append("Vị trí")

        if missing:
            st.error("⚠️ Vui lòng bổ sung: " + ", ".join(missing))
            st.stop()

        try:
            image = Image.open(uploaded_file).convert("RGB")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            image_path = os.path.join(UPLOAD_DIR, f"{timestamp}.jpg")
            image.save(image_path, format="JPEG", quality=90)
        except Exception as e:
            st.error(f"❌ Không thể lưu ảnh: {e}")
            st.stop()

        conn = get_conn()
        cursor = conn.execute(
            """
            INSERT INTO reports (
                reporter_name, image_path, description, location, latitude, longitude, status, ai_result, ai_analyzed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reporter_name.strip(),
                image_path,
                description.strip(),
                final_location,
                latitude,
                longitude,
                "Đang xử lý",
                "⏳ AI đang kiểm định hình ảnh...",
                0,
            )
        )
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()

        st.success(f"💾 Đã tiếp nhận báo cáo #{report_id}.")

        if cloudflare_configured():
            with st.spinner("🤖 Cloudflare Vision đang kiểm định hình ảnh..."):
                analysis = analyze_image_with_cloudflare(image_path)
                success = save_ai_result(report_id, analysis)

            if success:
                conn = get_conn()
                result_row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
                conn.close()

                if result_row:
                    if result_row["status"] == "Đã duyệt":
                        st.success(f"✅ Báo cáo #{report_id} đã được AI xác nhận là điểm rác hợp lệ.")
                    else:
                        st.warning(f"🚫 Báo cáo #{report_id} không được xác nhận là điểm rác hợp lệ.")

                    st.markdown("### 🤖 Kết quả AI")
                    st.write(result_row["ai_result"])

                    if result_row["status"] == "Đã duyệt":
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            st.write("**Loại rác**")
                            st.write(result_row["ai_waste_type"] or "AI chưa xác định")
                        with c2:
                            st.write("**Mức độ**")
                            st.write(result_row["ai_severity"] or "AI chưa xác định")
                        with c3:
                            st.write("**Điều phối**")
                            st.write(result_row["ai_dispatch_plan"] or "AI chưa đề xuất")
            else:
                st.error("⚠️ Báo cáo đã lưu nhưng Cloudflare AI chưa phân tích được.")
                st.caption(analysis.get("error", "Không xác định."))
        else:
            conn = get_conn()
            conn.execute(
                "UPDATE reports SET status = ?, ai_result = ? WHERE id = ?",
                ("Chờ AI", "⏳ Cloudflare AI chưa được cấu hình.", report_id)
            )
            conn.commit()
            conn.close()

            st.warning("⚠️ Báo cáo đã lưu nhưng Cloudflare AI chưa được cấu hình.")


# ============================================================
# 20. ĐỘI DỌN DẸP
# ============================================================

elif menu == "🧹 Đội dọn dẹp nhận nhiệm vụ":

    st.header("🧹 Đội dọn dẹp")

    if not st.session_state["team_auth_ok"]:
        st.info("🔒 Khu vực dành cho đội dọn dẹp.")
        pin = st.text_input("🔑 PIN đội dọn dẹp", type="password", key="team_login_pin")

        if st.button("🔓 Xác nhận", key="team_login_button"):
            if pin in [TEAM_PIN, ADMIN_PIN]:
                st.session_state["team_auth_ok"] = True
                st.success("✅ Đăng nhập thành công.")
                st.rerun()
            else:
                st.error("❌ PIN không chính xác.")
        st.stop()

    conn = get_conn()
    pending_tasks = conn.execute(
        "SELECT * FROM reports WHERE status IN ('Đã duyệt', 'Đang dọn') ORDER BY id DESC"
    ).fetchall()
    conn.close()

    if not pending_tasks:
        st.success("🎉 Hiện tại không có điểm rác nào cần xử lý.")
    else:
        task_map = {f"#{task['id']} | {task['location']} | {task['status']}": task for task in pending_tasks}
        selected_label = st.selectbox("📋 Chọn nhiệm vụ", list(task_map.keys()))
        current_task = task_map[selected_label]

        st.markdown("---")

        c1, c2 = st.columns(2)
        with c1:
            if current_task["image_path"] and os.path.isfile(current_task["image_path"]):
                st.image(current_task["image_path"], caption="Ảnh hiện trường", use_container_width=True)

        with c2:
            st.write(f"👤 **Người báo cáo:** {current_task['reporter_name']}")
            st.write(f"📍 **Vị trí:** {current_task['location']}")
            st.write(f"🗑️ **Loại rác:** {current_task['ai_waste_type'] or 'Chưa xác định'}")
            st.write(f"⚠️ **Mức độ:** {current_task['ai_severity'] or 'Chưa xác định'}")
            st.write(f"🔎 **AI nhìn thấy:** {current_task['ai_visual_evidence'] or 'Không có'}")
            st.info(f"🤖 {current_task['ai_dispatch_plan'] or 'AI chưa đưa ra phương án.'}")

        st.markdown("---")

        team_name = st.text_input(
            "🏷️ Tên đội dọn dẹp",
            value=current_task["assigned_team"] or "Đội Môi Trường Số 1",
            key=f"team_name_{current_task['id']}"
        )

        if current_task["status"] == "Đã duyệt":
            if st.button("✋ NHẬN NHIỆM VỤ", type="primary", key=f"take_{current_task['id']}"):
                if not team_name.strip():
                    st.warning("Vui lòng nhập tên đội.")
                    st.stop()

                conn = get_conn()
                conn.execute(
                    "UPDATE reports SET status = 'Đang dọn', assigned_team = ? WHERE id = ?",
                    (team_name.strip(), current_task["id"])
                )
                conn.commit()
                conn.close()

                st.success("✅ Đội đã nhận nhiệm vụ.")
                st.rerun()

        st.subheader("📸 Nộp bằng chứng hoàn thành")
        cleaned_file = st.file_uploader(
            "Ảnh sau khi đã dọn sạch",
            type=["jpg", "jpeg", "png", "webp"],
            key=f"clean_file_{current_task['id']}"
        )
        cleanup_note = st.text_area("📝 Ghi chú thu gom", key=f"cleanup_note_{current_task['id']}")

        if st.button("✅ BÁO CÁO DỌN XONG & TÍCH ĐIỂM", type="primary", key=f"complete_{current_task['id']}"):
            if cleaned_file is None or not team_name.strip():
                st.warning("Vui lòng nhập tên đội và tải ảnh sau khi dọn.")
                st.stop()

            try:
                image = Image.open(cleaned_file).convert("RGB")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                cleanup_path = os.path.join(CLEANUP_DIR, f"clean_{current_task['id']}_{timestamp}.jpg")
                image.save(cleanup_path, format="JPEG", quality=90)
            except Exception as e:
                st.error(f"Không thể lưu ảnh: {e}")
                st.stop()

            conn = get_conn()
            conn.execute(
                """
                UPDATE reports
                SET status = 'Hoàn thành', assigned_team = ?, cleanup_image_path = ?, cleanup_note = ?
                WHERE id = ?
                """,
                (team_name.strip(), cleanup_path, cleanup_note.strip(), current_task["id"])
            )
            conn.commit()
            conn.close()

            add_user_points(current_task["reporter_name"], 10)
            add_team_points(team_name.strip(), 20)

            st.balloons()
            st.success(f"🎉 Đã hoàn thành báo cáo #{current_task['id']}!")
            st.rerun()


# ============================================================
# 21. DANH SÁCH ĐÃ DỌN
# ============================================================

elif menu == "✅ Danh sách đã dọn":

    st.header("✅ Các điểm rác đã dọn sạch")

    conn = get_conn()
    completed = conn.execute("SELECT * FROM reports WHERE status = 'Hoàn thành' ORDER BY id DESC").fetchall()
    conn.close()

    if not completed:
        st.info("Chưa có báo cáo nào hoàn thành.")
    else:
        for report in completed:
            with st.expander(f"✅ Báo cáo #{report['id']} — {report['location']}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.write("📷 **Ảnh trước khi dọn**")
                    if report["image_path"] and os.path.isfile(report["image_path"]):
                        st.image(report["image_path"], use_container_width=True)

                with c2:
                    st.write("✨ **Ảnh sau khi dọn**")
                    if report["cleanup_image_path"] and os.path.isfile(report["cleanup_image_path"]):
                        st.image(report["cleanup_image_path"], use_container_width=True)

                st.write(f"👤 Người báo cáo: **{report['reporter_name']}**")
                st.write(f"🧹 Đội thực hiện: **{report['assigned_team']}**")
                st.write(f"📝 Ghi chú: {report['cleanup_note'] or 'Không có'}")


# ============================================================
# 22. BẢNG XẾP HẠNG
# ============================================================

elif menu == "🏆 Bảng xếp hạng tích điểm":

    st.header("🏆 Bảng xếp hạng đóng góp môi trường")

    conn = get_conn()
    top_users = conn.execute("SELECT * FROM user_points ORDER BY points DESC LIMIT 10").fetchall()
    top_teams = conn.execute("SELECT * FROM team_points ORDER BY points DESC LIMIT 10").fetchall()
    conn.close()

    col_user, col_team = st.columns(2)

    with col_user:
        st.subheader("🥇 Người báo cáo")
        if not top_users:
            st.info("Chưa có điểm.")
        else:
            for index, user in enumerate(top_users, 1):
                st.write(f"**#{index}. {user['username']}** — 🌟 {user['points']} điểm")

    with col_team:
        st.subheader("🚜 Đội dọn dẹp")
        if not top_teams:
            st.info("Chưa có đội.")
        else:
            for index, team in enumerate(top_teams, 1):
                st.write(f"**#{index}. {team['team_name']}** — 🏆 {team['points']} điểm — {team['tasks_completed']} nhiệm vụ")


# ============================================================
# 23. SPAM
# ============================================================

elif menu == "🗑️ Báo cáo Spam & Xóa":

    st.header("🗑️ Báo cáo Spam / Từ chối")

    if not st.session_state["staff_auth_ok"]:
        st.warning("🔒 Khu vực Staff.")
        pin = st.text_input("🔑 PIN Staff", type="password", key="staff_pin")

        if st.button("🔓 Xác nhận", key="staff_login"):
            if pin in [STAFF_PIN, ADMIN_PIN]:
                st.session_state["staff_auth_ok"] = True
                st.success("✅ Xác thực thành công.")
                st.rerun()
            else:
                st.error("❌ PIN không chính xác.")
        st.stop()

    conn = get_conn()
    spam_rows = conn.execute("SELECT * FROM reports WHERE status = 'Spam/Từ chối' ORDER BY id DESC").fetchall()
    conn.close()

    if not spam_rows:
        st.success("🎉 Không có báo cáo spam.")
    else:
        if st.button("🔥 XÓA TẤT CẢ SPAM", type="primary", key="delete_all_spam"):
            delete_reports_by_ids([row["id"] for row in spam_rows])
            st.success("✅ Đã xóa toàn bộ spam.")
            st.rerun()

        st.markdown("---")

        for report in spam_rows:
            with st.expander(f"🚫 Spam #{report['id']} — {report['location']}"):
                c1, c2 = st.columns(2)
                with c1:
                    if report["image_path"] and os.path.isfile(report["image_path"]):
                        st.image(report["image_path"], use_container_width=True)

                with c2:
                    st.write(f"👤 {report['reporter_name']}")
                    st.write(f"📍 {report['location']}")
                    st.error(report["ai_result"] or "Không có nhận xét.")
                    if report["ai_spam_reason"]:
                        st.write(f"**Lý do:** {report['ai_spam_reason']}")

                if st.button(f"🗑️ Xóa #{report['id']}", key=f"delete_spam_{report['id']}"):
                    delete_reports_by_ids([report["id"]])
                    st.success("Đã xóa.")
                    st.rerun()


# ============================================================
# 24. ADMIN
# ============================================================

elif menu == "⚙️ Reset & Cài đặt AI":

    st.header("⚙️ Cài đặt hệ thống")

    if not st.session_state["admin_auth_ok"]:
        st.warning("🔒 Khu vực Admin.")
        pin = st.text_input("🔑 PIN Admin", type="password", key="admin_pin")

        if st.button("🔓 Đăng nhập Admin", key="admin_login"):
            if pin == ADMIN_PIN:
                st.session_state["admin_auth_ok"] = True
                st.success("✅ Đăng nhập thành công.")
                st.rerun()
            else:
                st.error("❌ PIN Admin không đúng.")
        st.stop()

    st.subheader("🤖 Cloudflare AI")

    if cloudflare_configured():
        st.success("✅ Cloudflare đã được cấu hình.")
        st.write(f"Account ID: `{CF_ACCOUNT_ID[:8]}...`")
        st.write(f"Model: `{CF_MODEL}`")
    else:
        st.error("❌ Cloudflare chưa được cấu hình.")
        st.code(
            """
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_AUTH_TOKEN = "..."
ADMIN_PIN = "9999"
TEAM_PIN = "5555"
STAFF_PIN = "1234"
""",
            language="toml"
        )

    st.markdown("---")
    st.subheader("🧪 Kiểm tra Cloudflare")

    if st.button("🔌 Kiểm tra kết nối Cloudflare", key="test_cloudflare"):
        if not cloudflare_configured():
            st.error("Chưa có Account ID hoặc Token.")
        else:
            result = agree_cloudflare_model()
            if result.get("success", False):
                st.success("✅ Cloudflare AI hoạt động và model đã được kích hoạt.")
            else:
                st.error("❌ Không thể kết nối.")
                st.code(result.get("error", "Không xác định"))

    st.markdown("---")
    st.subheader("🚨 Reset dữ liệu")
    st.warning("Thao tác này sẽ xóa báo cáo, ảnh và điểm tích lũy.")

    confirm = st.text_input("Nhập RESET để xác nhận", key="reset_confirm")

    if st.button("💥 RESET TOÀN BỘ", type="primary", key="reset_all"):
        if confirm.strip().upper() == "RESET":
            reset_database()
            st.success("✅ Đã reset hệ thống.")
            st.rerun()
        else:
            st.error("Bạn chưa nhập đúng RESET.")


# ============================================================
# 25. FOOTER
# ============================================================

st.sidebar.markdown("---")
st.sidebar.caption("🌿 Urban GreenEye AI")
st.sidebar.caption("AI Vision • SQLite • Streamlit")
