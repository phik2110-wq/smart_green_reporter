from datetime import datetime, timedelta
from io import BytesIO
import base64
import json
import os
import sqlite3

import folium
from PIL import Image
import requests
import streamlit as st
from streamlit_folium import st_folium

# ============================================================
# CẤU HÌNH HỆ THỐNG & ĐỌC MÃ PIN TỪ SECRETS WEB
# ============================================================

st.set_page_config(
    page_title="Urban GreenEye AI - Quản lý Ô nhiễm Đô thị",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp { background-color: #f4f9f4; }
    [data-testid="stSidebar"] {
        background-color: #e8f5e9 !important;
        border-right: 2px solid #c8e6c9;
    }
    h1 { color: #1b5e20 !important; font-weight: 700 !important; }
    h2, h3 { color: #2e7d32 !important; }
    .stButton>button {
        background-color: #4caf50 !important;
        color: white !important;
        border-radius: 8px !important;
        border: none !important;
        font-weight: bold !important;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #388e3c !important;
        transform: translateY(-2px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.15);
    }
    .streamlit-expanderHeader {
        background-color: #ffffff !important;
        border-radius: 8px !important;
        border: 1px solid #c8e6c9 !important;
    }
    .stAlert { border-radius: 10px !important; }
    </style>
""",
    unsafe_allow_html=True,
)

DB_FILE = "reports.db"
UPLOAD_DIR = "uploaded_images"
CLEANUP_DIR = "cleanup_images"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANUP_DIR, exist_ok=True)

CF_ACCOUNT_ID = st.secrets.get(
    "CLOUDFLARE_ACCOUNT_ID", os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
)
CF_AUTH_TOKEN = st.secrets.get(
    "CLOUDFLARE_AUTH_TOKEN", os.getenv("CLOUDFLARE_AUTH_TOKEN", "")
)
CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"

ADMIN_PIN = str(st.secrets.get("ADMIN_PIN", os.getenv("ADMIN_PIN", "9999")))
TEAM_PIN = str(st.secrets.get("TEAM_PIN", os.getenv("TEAM_PIN", "5555")))
STAFF_PIN = str(st.secrets.get("STAFF_PIN", os.getenv("STAFF_PIN", "1234")))


# ============================================================
# CƠ SỞ DỮ LIỆU SQLITE3
# ============================================================


def get_conn():
  conn = sqlite3.connect(DB_FILE, check_same_thread=False)
  conn.row_factory = sqlite3.Row
  return conn


def init_db():
  conn = get_conn()
  conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_name TEXT DEFAULT 'Vô danh',
            image_path TEXT,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            status TEXT DEFAULT 'Đã nhận',
            ai_result TEXT DEFAULT '',
            ai_raw_json TEXT DEFAULT '',
            ai_analyzed INTEGER DEFAULT 0,
            assigned_team TEXT DEFAULT '',
            cleanup_image_path TEXT DEFAULT '',
            cleanup_note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
  conn.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            username TEXT PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
    """)
  conn.execute("""
        CREATE TABLE IF NOT EXISTS team_points (
            team_name TEXT PRIMARY KEY,
            points INTEGER DEFAULT 0,
            tasks_completed INTEGER DEFAULT 0
        )
    """)
  conn.commit()
  conn.close()


def add_column_if_missing(column_name, column_type, default_sql=None):
  conn = get_conn()
  columns = [
      r["name"] for r in conn.execute("PRAGMA table_info(reports)").fetchall()
  ]
  if column_name not in columns:
    sql = f"ALTER TABLE reports ADD COLUMN {column_name} {column_type}"
    if default_sql:
      sql += f" DEFAULT {default_sql}"
    conn.execute(sql)
    conn.commit()
  conn.close()


def migrate_db():
  init_db()
  add_column_if_missing("reporter_name", "TEXT", "'Vô danh'")
  add_column_if_missing("assigned_team", "TEXT", "''")
  add_column_if_missing("cleanup_image_path", "TEXT", "''")
  add_column_if_missing("cleanup_note", "TEXT", "''")


migrate_db()


# ============================================================
# HÀM XỬ LÝ ĐIỂM SỐ & DATABASE
# ============================================================


def add_user_points(username, pts):
  if not username or username == "Vô danh":
    return
  conn = get_conn()
  conn.execute(
      """
        INSERT INTO user_points (username, points) VALUES (?, ?)
        ON CONFLICT(username) DO UPDATE SET points = points + ?
    """,
      (username, pts, pts),
  )
  conn.commit()
  conn.close()


def add_team_points(team_name, pts):
  if not team_name:
    return
  conn = get_conn()
  conn.execute(
      """
        INSERT INTO team_points (team_name, points, tasks_completed) VALUES (?, ?, 1)
        ON CONFLICT(team_name) DO UPDATE SET points = points + ?, tasks_completed = tasks_completed + 1
    """,
      (team_name, pts, pts),
  )
  conn.commit()
  conn.close()


def delete_reports_by_ids(report_ids):
  conn = get_conn()
  for r_id in report_ids:
    row = conn.execute(
        "SELECT image_path, cleanup_image_path FROM reports WHERE id = ?",
        (r_id,),
    ).fetchone()
    if row:
      for img_p in [row["image_path"], row["cleanup_image_path"]]:
        if img_p and os.path.isfile(img_p):
          try:
            os.remove(img_p)
          except OSError:
            pass
    conn.execute("DELETE FROM reports WHERE id = ?", (r_id,))
  conn.commit()
  conn.close()


def reset_database_to_one():
  conn = get_conn()
  rows = conn.execute(
      "SELECT image_path, cleanup_image_path FROM reports"
  ).fetchall()
  for r in rows:
    for img_p in [r["image_path"], r["cleanup_image_path"]]:
      if img_p and os.path.isfile(img_p):
        try:
          os.remove(img_p)
        except OSError:
          pass

  conn.execute("DELETE FROM reports")
  conn.execute("DELETE FROM sqlite_sequence WHERE name='reports'")
  conn.execute("DELETE FROM user_points")
  conn.execute("DELETE FROM team_points")
  conn.commit()
  conn.close()


# ============================================================
# ENGINE CLOUDFLARE AI
# ============================================================


def cloudflare_configured():
  return bool(CF_ACCOUNT_ID and CF_AUTH_TOKEN)


def cloudflare_url():
  return f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"


def prepare_image(image_path, max_side=1024, quality=82):
  img = Image.open(image_path).convert("RGB")
  img.thumbnail((max_side, max_side))
  buffer = BytesIO()
  img.save(buffer, format="JPEG", quality=quality, optimize=True)
  return buffer.getvalue()


def parse_ai_text(raw_input):
    """
    Chuẩn hóa kết quả AI về một dict thống nhất.
    AI phải phân biệt:
    - Người/khuôn mặt/ảnh chân dung -> không phải rác
    - Rác thực tế -> có rác
    """

    if isinstance(raw_input, dict):
        data = raw_input

    else:
        text = str(raw_input or "").strip()

        # Thử lấy JSON trong kết quả AI
        try:
            data = json.loads(text)
        except Exception:
            data = None

        if not isinstance(data, dict):
            start = text.find("{")
            end = text.rfind("}")

            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except Exception:
                    data = None

        # Nếu AI không trả JSON
        if not isinstance(data, dict):
            upper = text.upper()

            negative_words = [
                "KHÔNG CÓ RÁC",
                "KHÔNG CÓ RÁC THẢI",
                "ẢNH CHÂN DUNG",
                "KHUÔN MẶT",
                "GƯƠNG MẶT",
                "SELFIE",
                "PORTRAIT",
                "PERSON ONLY",
                "NO WASTE",
                "NO TRASH",
                "CLEAN",
                "KHÔNG PHẢI RÁC"
            ]

            positive_words = [
                "CÓ RÁC",
                "RÁC THẢI",
                "BÃI RÁC",
                "CHẤT THẢI",
                "RÁC SINH HOẠT",
                "TRASH",
                "GARBAGE",
                "WASTE",
                "LITTER",
                "DUMP"
            ]

            has_negative = any(x in upper for x in negative_words)
            has_positive = any(x in upper for x in positive_words)

            contains_waste = has_positive and not has_negative

            data = {
                "contains_waste": contains_waste,
                "is_waste_amount_sufficient": contains_waste,
                "severity": "Trung bình" if contains_waste else "Không có",
                "waste_type": "Rác sinh hoạt" if contains_waste else "",
                "visual_evidence": text[:500],
                "spam_reason": "" if contains_waste else text[:300],
                "dispatch_plan": ""
            }

    return {
        "contains_waste": bool(data.get("contains_waste", False)),
        "is_waste_amount_sufficient": bool(
            data.get("is_waste_amount_sufficient", False)
        ),
        "severity": str(data.get("severity", "Không xác định")),
        "waste_type": str(data.get("waste_type", "")),
        "visual_evidence": str(data.get("visual_evidence", "")),
        "spam_reason": str(data.get("spam_reason", "")),
        "dispatch_plan": str(data.get("dispatch_plan", ""))
    }
def analyze_image_with_cloudflare(image_path):
    try:
        if not cloudflare_configured():
            return {
                "success": False,
                "error": "Chưa cấu hình Cloudflare Workers AI."
            }

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        image_data_uri = (
            f"data:image/jpeg;base64,{image_base64}"
        )

        prompt = """
Bạn là AI kiểm định ảnh phản ánh môi trường đô thị.

NHIỆM VỤ:
Phân tích chính xác hình ảnh và xác định hình ảnh có thực sự chứa
RÁC THẢI / CHẤT THẢI / BÃI RÁC / RÁC BỊ ĐỔ BỎ hay không.

QUY TẮC RẤT QUAN TRỌNG:

1. NGƯỜI KHÔNG PHẢI LÀ RÁC.
2. KHUÔN MẶT KHÔNG PHẢI LÀ RÁC.
3. ẢNH CHÂN DUNG KHÔNG PHẢI LÀ RÁC.
4. ẢNH SELFIE KHÔNG PHẢI LÀ RÁC.
5. Quần áo, tóc, cơ thể người không phải là rác.
6. Nếu có người nhỏ ở phía xa nhưng cảnh vật có rác thực tế,
   vẫn phải nhận diện rác.
7. Không được từ chối ảnh chỉ vì trong ảnh có người.
8. Chỉ kết luận CÓ RÁC khi nhìn thấy vật liệu/rác thải thực tế.
9. Nếu không chắc chắn có rác thì kết luận KHÔNG CÓ RÁC.
10. Không được suy đoán rác không nhìn thấy trong ảnh.

ĐÁNH GIÁ:
- contains_waste: true/false
- is_waste_amount_sufficient: true/false
- severity: Mức độ rác
- waste_type: Loại rác
- visual_evidence: Những gì thực sự nhìn thấy
- spam_reason: Lý do nếu không phải phản ánh rác hợp lệ
- dispatch_plan: Tự đề xuất phương án xử lý dựa trên loại,
  số lượng và quy mô rác thực tế nhìn thấy.

QUAN TRỌNG:
dispatch_plan phải do bạn tự quyết định dựa trên hình ảnh.
Không sử dụng một phương án cố định cho mọi trường hợp.

CHỈ TRẢ VỀ JSON:

{
  "contains_waste": true,
  "is_waste_amount_sufficient": true,
  "severity": "Nhẹ/Trung bình/Nặng",
  "waste_type": "...",
  "visual_evidence": "...",
  "spam_reason": "...",
  "dispatch_plan": "..."
}
"""

        payload = {
            "prompt": prompt,
            "image": image_data_uri,
            "max_tokens": 500,
            "temperature": 0.05
        }

        url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/"
            f"@cf/meta/llama-3.2-11b-vision-instruct"
        )

        headers = {
            "Authorization": f"Bearer {CLOUDFLARE_AUTH_TOKEN}",
            "Content-Type": "application/json"
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=90
        )

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"Cloudflare HTTP {response.status_code}: "
                          f"{response.text[:1000]}"
            }

        result = response.json()

        if not result.get("success", False):
            return {
                "success": False,
                "error": json.dumps(result, ensure_ascii=False)
            }

        raw_response = result.get("result", {}).get("response", "")

        parsed = parse_ai_text(raw_response)

        return {
            "success": True,
            "parsed": parsed,
            "raw": raw_response
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
def save_ai_result(report_id, analysis):

    conn = get_conn()

    # AI lỗi -> KHÔNG được coi là spam
    if not analysis.get("success"):
        conn.execute("""
            UPDATE reports
            SET status = ?,
                ai_result = ?,
                ai_raw_json = ?,
                ai_analyzed = 0
            WHERE id = ?
        """, (
            "Lỗi AI",
            f"❌ AI chưa phân tích được: {analysis.get('error', 'Unknown error')}",
            json.dumps(analysis, ensure_ascii=False),
            report_id
        ))

        conn.commit()
        conn.close()
        return False

    parsed = analysis.get("parsed", {})

    contains_waste = bool(
        parsed.get("contains_waste", False)
    )

    sufficient = bool(
        parsed.get("is_waste_amount_sufficient", False)
    )

    if contains_waste and sufficient:

        status = "Đã duyệt"

        ai_result = (
            f"✅ Có rác\n"
            f"Loại: {parsed.get('waste_type', 'Không xác định')}\n"
            f"Mức độ: {parsed.get('severity', 'Không xác định')}\n\n"
            f"📷 Bằng chứng hình ảnh:\n"
            f"{parsed.get('visual_evidence', '')}\n\n"
            f"🚛 Phương án xử lý do AI đề xuất:\n"
            f"{parsed.get('dispatch_plan', '')}"
        )

    else:

        status = "Spam/Từ chối"

        ai_result = (
            "❌ Không phải phản ánh rác hợp lệ.\n\n"
            f"Lý do AI:\n"
            f"{parsed.get('spam_reason', '')}\n\n"
            f"Bằng chứng:\n"
            f"{parsed.get('visual_evidence', '')}"
        )

    conn.execute("""
        UPDATE reports
        SET status = ?,
            ai_result = ?,
            ai_raw_json = ?,
            ai_analyzed = 1
        WHERE id = ?
    """, (
        status,
        ai_result,
        json.dumps(parsed, ensure_ascii=False),
        report_id
    ))

    conn.commit()
    conn.close()

    return True
# ============================================================
# GIAO DIỆN CHÍNH
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
    ],
)

if "last_menu" not in st.session_state:
  st.session_state["last_menu"] = menu

if st.session_state["last_menu"] != menu:
  st.session_state["team_auth_ok"] = False
  st.session_state["staff_auth_ok"] = False
  st.session_state["admin_auth_ok"] = False
  st.session_state["last_menu"] = menu

st.title("🌳 Urban GreenEye AI - Quản lý Ô nhiễm Đô thị")

# ------------------------------------------------------------
# 1. GỬI BÁO CÁO (BẮT BUỘC NHẬP + AI PHÂN TÍCH THỰC TẾ)
# ------------------------------------------------------------
if menu == "📷 Gửi báo cáo":
  st.header("📷 Gửi báo cáo điểm rác")

  reporter_name = st.text_input(
      "👤 Tên/Mã người báo cáo (*) (Đúng tên để tích điểm):", placeholder="Nguyễn Văn A"
  )
  uploaded_file = st.file_uploader(
      "📷 Tải ảnh hiện trường (*) :", type=["jpg", "jpeg", "png", "webp"]
  )

  st.write("📍 **Chọn vị trí trên bản đồ (*):**")
  m = folium.Map(location=[10.762622, 106.660172], zoom_start=12)
  m.add_child(folium.LatLngPopup())
  map_data = st_folium(m, height=280, use_container_width=True)

  selected_location = ""
  if map_data and map_data.get("last_clicked"):
    lat, lng = map_data["last_clicked"]["lat"], map_data["last_clicked"]["lng"]
    selected_location = f"Tọa độ: {lat:.6f}, {lng:.6f}"
    st.success(f"📌 Đã chọn: {selected_location}")

  manual_location = st.text_input(
      "Nhập địa chỉ cụ thể (* nếu không chọn bản đồ):",
      placeholder="Số nhà, tên đường...",
  )
  description = st.text_area("Mô tả thêm (Không bắt buộc):")
  final_location = manual_location.strip() or selected_location

  if st.button("🚀 Gửi báo cáo", type="primary", use_container_width=True):
    # RÀNG BUỘC BẮT BUỘC NHẬP
    if (
        not reporter_name.strip()
        or not uploaded_file
        or not final_location.strip()
    ):
      st.error(
          "⚠️ **Không thể gửi!** Vui lòng nhập đầy đủ các thông tin bắt buộc (*):"
          " Tên người báo cáo, Ảnh hiện trường và Vị trí."
      )
      st.stop()

    image = Image.open(uploaded_file).convert("RGB")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = os.path.join(UPLOAD_DIR, f"{timestamp}.jpg")
    image.save(image_path, format="JPEG", quality=90)

    # LƯU CHUỖI TẠM THỜI (KHÔNG GÁN "CÓ RÁC" MẶC ĐỊNH HỆ THỐNG)
    conn = get_conn()
    cursor = conn.execute(
        """
            INSERT INTO reports (reporter_name, image_path, description, location, status, ai_result)
            VALUES (?, ?, ?, ?, 'Đang xử lý', '⏳ Hệ thống AI đang kiểm định hình ảnh...')
        """,
        (
            reporter_name.strip(),
            image_path,
            description.strip(),
            final_location,
        ),
    )
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()

    if cloudflare_configured():

    with st.spinner("🤖 AI Vision đang tự động kiểm định hình ảnh..."):

        analysis = analyze_image_with_cloudflare(
            image_path
        )

        success = save_ai_result(
            report_id,
            analysis
        )

    if success:
        st.success(
            f"✅ Đã gửi và AI đã kiểm định xong Báo cáo #{report_id}!"
        )
    else:
        st.warning(
            f"⚠️ Báo cáo #{report_id} đã được lưu, "
            "nhưng AI chưa phân tích được. Báo cáo sẽ được giữ lại."
        )

else:

    conn = get_conn()

    conn.execute("""
        UPDATE reports
        SET status = ?,
            ai_result = ?
        WHERE id = ?
    """, (
        "Chờ AI",
        "⏳ Chờ cấu hình Cloudflare AI.",
        report_id
    ))

    conn.commit()
    conn.close()

    st.warning(
        f"⚠️ Báo cáo #{report_id} đã được lưu nhưng "
        "Cloudflare AI chưa được cấu hình."
    )

st.rerun()

# ------------------------------------------------------------
# 2. ĐỘI DỌN DẸP NHẬN NHIỆM VỤ
# ------------------------------------------------------------
elif menu == "🧹 Đội dọn dẹp nhận nhiệm vụ":
  st.header("🧹 Đội dọn dẹp tiếp nhận & Báo cáo kết quả")

  if not st.session_state.get("team_auth_ok", False):
    st.info("🔒 Mục này dành riêng cho Đội dọn dẹp.")
    pin_input = st.text_input(
        "🔑 Nhập Mã PIN Đội dọn dẹp (TEAM_PIN):", type="password"
    )
    if st.button("🔓 Xác nhận truy cập"):
      if pin_input in [TEAM_PIN, ADMIN_PIN]:
        st.session_state["team_auth_ok"] = True
        st.success("✅ Xác thực thành công!")
        st.rerun()
      else:
        st.error("❌ Mã PIN không chính xác!")
    st.stop()

  conn = get_conn()
  pending_tasks = conn.execute(
      "SELECT * FROM reports WHERE status IN ('Đã duyệt', 'Đang dọn') ORDER BY"
      " id DESC"
  ).fetchall()
  conn.close()

  if not pending_tasks:
    st.success("🎉 Hiện tại không có điểm rác nào cần dọn!")
  else:
    st.subheader("1️⃣ Chọn báo cáo điểm rác:")
    task_map = {
        f"Báo cáo #{t['id']} - {t['location']} (Trạng thái: {t['status']})": t
        for t in pending_tasks
    }
    selected_task_label = st.selectbox(
        "Danh sách điểm rác chờ xử lý:", list(task_map.keys())
    )
    current_task = task_map[selected_task_label]

    st.markdown("---")
    st.subheader("2️⃣ Chi tiết yêu cầu lực lượng và vật dụng AI điều phối:")
    col_img, col_info = st.columns([1, 1])
    with col_img:
      if current_task["image_path"] and os.path.isfile(
          current_task["image_path"]
      ):
        st.image(
            current_task["image_path"],
            caption="Ảnh hiện trường rác",
            use_container_width=True,
        )
    with col_info:
      st.write(f"👤 **Người báo cáo:** {current_task['reporter_name']}")
      st.write(f"📍 **Vị trí:** {current_task['location']}")
      st.info(current_task["ai_result"])

    st.markdown("---")
    st.subheader("3️⃣ Đội dọn dẹp đảm nhận & Hoàn tất:")

    team_name = st.text_input(
        "🏷️ **Nhập Tên Đội dọn rác của bạn:**",
        value=current_task["assigned_team"] or "Đội Môi Trường Số 1",
    )

    if current_task["status"] == "Đã duyệt":
      if st.button("✋ Nhận điểm rác này (Đang dọn dẹp)"):
        if not team_name.strip():
          st.warning("Vui lòng nhập tên đội!")
          st.stop()
        conn = get_conn()
        conn.execute(
            "UPDATE reports SET status = 'Đang dọn', assigned_team = ? WHERE"
            " id = ?",
            (team_name.strip(), current_task["id"]),
        )
        conn.commit()
        conn.close()
        st.success(f"Đội '{team_name}' đã nhận nhiệm vụ!")
        st.rerun()

    st.write("📸 **Nộp bằng chứng hoàn thành:**")
    cleaned_file = st.file_uploader(
        "Tải ảnh ĐÃ DỌN SẠCH RÁC:", type=["jpg", "jpeg", "png"]
    )
    cleanup_note = st.text_area("Ghi chú thu gom:")

    if st.button("✅ Báo cáo dọn xong & Tích điểm", type="primary"):
      if not cleaned_file or not team_name.strip():
        st.warning("Vui lòng nhập Tên đội và tải ảnh chứng minh đã dọn sạch!")
        st.stop()

      img = Image.open(cleaned_file).convert("RGB")
      timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
      c_path = os.path.join(
          CLEANUP_DIR, f"clean_{current_task['id']}_{timestamp}.jpg"
      )
      img.save(c_path, format="JPEG", quality=90)

      conn = get_conn()
      conn.execute(
          """
                UPDATE reports 
                SET status = 'Hoàn thành', assigned_team = ?, cleanup_image_path = ?, cleanup_note = ?
                WHERE id = ?
            """,
          (team_name.strip(), c_path, cleanup_note, current_task["id"]),
      )
      conn.commit()
      conn.close()

      add_user_points(current_task["reporter_name"], 10)
      add_team_points(team_name.strip(), 20)

      st.balloons()
      st.success(f"🎉 Hoàn tất điểm rác #{current_task['id']}!")
      st.rerun()

    with st.popover(f"🗑️ Xóa báo cáo #{current_task['id']}"):
      pin_del_task = st.text_input(
          "Nhập PIN Nhân Viên / Admin:", type="password", key="pin_task"
      )
      if st.button("Xác nhận xóa"):
        if pin_del_task in [STAFF_PIN, ADMIN_PIN]:
          delete_reports_by_ids([current_task["id"]])
          st.success("Đã xóa báo cáo!")
          st.rerun()
        else:
          st.error("PIN không đúng!")

# ------------------------------------------------------------
# 3. DANH SÁCH BÁO CÁO ĐÃ DỌN
# ------------------------------------------------------------
elif menu == "✅ Danh sách đã dọn":
  st.header("✅ Danh sách các điểm rác đã dọn sạch")

  conn = get_conn()
  completed = conn.execute(
      "SELECT * FROM reports WHERE status = 'Hoàn thành' ORDER BY id DESC"
  ).fetchall()
  conn.close()

  if not completed:
    st.info("Chưa có báo cáo nào hoàn thành.")
  else:
    selected_done_del = []
    for c in completed:
      col_chk, col_content = st.columns([0.08, 0.92])
      with col_chk:
        if st.checkbox("", key=f"chk_done_{c['id']}"):
          selected_done_del.append(c["id"])

      with col_content:
        with st.expander(
            f"✅ Báo cáo #{c['id']} — {c['location']} | Đội dọn:"
            f" {c['assigned_team']}"
        ):
          c1, c2 = st.columns(2)
          with c1:
            st.write("📷 **Ảnh hiện trạng rác ban đầu:**")
            if c["image_path"] and os.path.isfile(c["image_path"]):
              st.image(c["image_path"], use_container_width=True)
          with c2:
            st.write("✨ **Ảnh bằng chứng ĐÃ DỌN SẠCH:**")
            if c["cleanup_image_path"] and os.path.isfile(
                c["cleanup_image_path"]
            ):
              st.image(c["cleanup_image_path"], use_container_width=True)

          st.write(f"👤 **Người báo cáo:** {c['reporter_name']} (+10 điểm)")
          st.write(f"🧹 **Đội thực hiện:** {c['assigned_team']} (+20 điểm)")
          st.write(f"📝 **Ghi chú dọn:** {c['cleanup_note'] or 'Không có'}")

          with st.popover(f"🗑️ Xóa báo cáo #{c['id']}"):
            pin_del_done = st.text_input(
                "Mã PIN Nhân Viên / Admin:",
                type="password",
                key=f"pin_done_{c['id']}",
            )
            if st.button("Xóa", key=f"btn_done_{c['id']}"):
              if pin_del_done in [STAFF_PIN, ADMIN_PIN]:
                delete_reports_by_ids([c["id"]])
                st.success("Đã xóa!")
                st.rerun()
              else:
                st.error("PIN sai!")

# ------------------------------------------------------------
# 4. BẢNG XẾP HẠNG TÍCH ĐIỂM
# ------------------------------------------------------------
elif menu == "🏆 Bảng xếp hạng tích điểm":
  st.header("🏆 Bảng Xếp Hạng Đóng Góp Môi Trường")

  conn = get_conn()
  top_users = conn.execute(
      "SELECT * FROM user_points ORDER BY points DESC LIMIT 10"
  ).fetchall()
  top_teams = conn.execute(
      "SELECT * FROM team_points ORDER BY points DESC LIMIT 10"
  ).fetchall()
  conn.close()

  col_u, col_t = st.columns(2)

  with col_u:
    st.subheader("🥇 Top Người Báo Cáo Nhiều Nhất")
    if not top_users:
      st.info("Chưa có điểm tích lũy.")
    else:
      for idx, u in enumerate(top_users, 1):
        st.write(f"**#{idx}. {u['username']}** — 🌟 `{u['points']}` điểm")

  with col_t:
    st.subheader("🚜 Top Đội Dọn Rác Xuất Sắc Nhất")
    if not top_teams:
      st.info("Chưa có đội dọn dẹp nào.")
    else:
      for idx, t in enumerate(top_teams, 1):
        st.write(
            f"**#{idx}. {t['team_name']}** — 🏆 `{t['points']}` điểm"
            f" ({t['tasks_completed']} điểm rác)"
        )

# ------------------------------------------------------------
# 5. BÁO CÁO SPAM & XÓA (KHI ẢNH MẶT NGƯỜI SẼ VÀO ĐÂY)
# ------------------------------------------------------------
elif menu == "🗑️ Báo cáo Spam & Xóa":
  st.header("🗑 Danh sách Báo cáo Spam/Từ chối")

  if not st.session_state.get("staff_auth_ok", False):
    st.warning("🔒 Vui lòng nhập Mã PIN Nhân viên (STAFF_PIN).")
    pin_staff_input = st.text_input(
        "🔑 Nhập Mã PIN Staff (STAFF_PIN):", type="password"
    )
    if st.button("🔓 Xác nhận truy cập Staff"):
      if pin_staff_input in [STAFF_PIN, ADMIN_PIN]:
        st.session_state["staff_auth_ok"] = True
        st.success("✅ Xác thực thành công!")
        st.rerun()
      else:
        st.error("❌ Mã PIN Nhân viên không chính xác!")
    st.stop()

  conn = get_conn()
  rows = conn.execute(
      "SELECT * FROM reports WHERE status = 'Spam/Từ chối' ORDER BY id DESC"
  ).fetchall()
  conn.close()

  if not rows:
    st.success("🎉 Không có báo cáo spam nào cần xử lý.")
  else:
    if st.button("🔥 Xóa sạch TẤT CẢ Spam", type="primary"):
      delete_reports_by_ids([r["id"] for r in rows])
      st.success("Đã xóa sạch toàn bộ báo cáo spam!")
      st.rerun()

    st.markdown("---")
    for r in rows:
      with st.expander(f"🚫 Spam #{r['id']} — {r['location']}"):
        st.write(f"📍 **Vị trí:** {r['location']}")
        st.error(r["ai_result"])

        if st.button(f"🗑️ Xóa đơn #{r['id']}", key=f"btn_s_{r['id']}"):
          delete_reports_by_ids([r["id"]])
          st.success(f"Đã xóa báo cáo #{r['id']}!")
          st.rerun()

# ------------------------------------------------------------
# 6. RESET & CÀI ĐẶT AI
# ------------------------------------------------------------
elif menu == "⚙️ Reset & Cài đặt AI":
  st.header("⚙️ Cấu hình Hệ thống & AI")

  if not st.session_state.get("admin_auth_ok", False):
    st.warning("🔒 Vui lòng nhập Mã PIN ADMIN.")
    pin_admin_input = st.text_input(
        "🔑 Nhập Mã PIN ADMIN (ADMIN_PIN):", type="password"
    )
    if st.button("🔓 Xác nhận đăng nhập Admin"):
      if pin_admin_input == ADMIN_PIN:
        st.session_state["admin_auth_ok"] = True
        st.success("✅ Xác thực thành công!")
        st.rerun()
      else:
        st.error("❌ Mã PIN ADMIN không chính xác!")
    st.stop()

  st.subheader("🤖 Trạng thái kết nối Cloudflare AI")
  if cloudflare_configured():
    st.success("✅ Cloudflare AI đã kết nối thành công!")
    st.write(f"• **Account ID:** `{CF_ACCOUNT_ID[:6]}...`")
    st.write(f"• **Model:** `{CF_MODEL}`")
  else:
    st.error("❌ Chưa kết nối Cloudflare AI.")

  st.markdown("---")
  st.subheader("🔄 Reset dữ liệu & Đếm Báo cáo về 1")

  with st.popover("🚨 BẮT ĐẦU RESET HỆ THỐNG VỀ 1"):
    confirm_text = st.text_input(
        "Nhập 'RESET' để xác nhận xóa:", key="txt_confirm_reset"
    )
    if st.button(
        "💥 XÁC NHẬN RESET TOÀN BỘ VỀ 1",
        type="primary",
        key="btn_reset_confirm",
    ):
      if confirm_text.strip().upper() == "RESET":
        reset_database_to_one()
        st.success("✅ Đã reset toàn bộ hệ thống về ID #1!")
        st.rerun()
      else:
        st.error("Bạn nhập từ xác nhận chưa chính xác!")
