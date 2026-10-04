from datetime import datetime, timedelta
from io import BytesIO
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

# ------------------------------------------------------------
# TRANG TRÍ GIAO DIỆN TƯƠI XANH (CUSTOM CSS)
# ------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Nền chính và font chữ */
    .stApp {
        background-color: #f4f9f4;
    }
    
    /* Sidebar xanh tươi mát */
    [data-testid="stSidebar"] {
        background-color: #e8f5e9 !important;
        border-right: 2px solid #c8e6c9;
    }
    
    /* Tiêu đề chính */
    h1 {
        color: #1b5e20 !important;
        font-weight: 700 !important;
    }
    h2, h3 {
        color: #2e7d32 !important;
    }
    
    /* Tùy chỉnh các nút bấm */
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
    
    /* Khung Expander & Cards */
    .streamlit-expanderHeader {
        background-color: #ffffff !important;
        border-radius: 8px !important;
        border: 1px solid #c8e6c9 !important;
    }
    
    /* Thông báo Info / Success / Warning */
    .stAlert {
        border-radius: 10px !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

DB_FILE = "reports.db"
UPLOAD_DIR = "uploaded_images"
CLEANUP_DIR = "cleanup_images"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANUP_DIR, exist_ok=True)

# LẤY THÔNG TIN API & MÃ PIN TỪ STREAMLIT SECRETS
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
  if isinstance(raw_input, dict):
    return {
        "contains_waste": raw_input.get("contains_waste", True),
        "severity": raw_input.get("severity", "Trung bình"),
        "waste_type": raw_input.get("waste_type", "Rác sinh hoạt"),
        "dispatch_plan": raw_input.get(
            "dispatch_plan",
            "Cần 2 công nhân thu gom thủ công và 1 xe đẩy rác.",
        ),
    }

  text = str(raw_input or "").strip()
  cleaned = text.replace("```json", "").replace("```", "").strip()

  start_idx = cleaned.find("{")
  end_idx = cleaned.rfind("}")

  if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
    json_str = cleaned[start_idx : end_idx + 1]
    try:
      data = json.loads(json_str)
      if isinstance(data, dict):
        return data
    except Exception:
      pass

  upper = text.upper()
  has_no_waste = any(
      k in upper
      for k in [
          "KHÔNG PHẢI RÁC",
          "KHÔNG CÓ RÁC",
          "KHONG PHAI RAC",
          "NO WASTE",
          "CLEAN",
          "PERSON",
          "FACE",
      ]
  )

  return {
      "contains_waste": not has_no_waste,
      "severity": "Nặng" if "NẶNG" in upper else "Trung bình",
      "waste_type": "Rác sinh hoạt",
      "dispatch_plan": "Bố trí 2 công nhân và xe gom rác chuyên dụng.",
  }


def analyze_image_with_cloudflare(image_path):
  if not cloudflare_configured():
    return {"success": False, "error": "Chưa cấu hình Cloudflare API."}

  try:
    image_bytes = prepare_image(image_path)
    image_array = list(image_bytes)

    # Prompt nâng cấp phân tích kỹ lưỡng & phân bố lực lượng tự nhiên
    prompt = """
Bạn là AI chuyên gia kiểm định môi trường đô thị. Hãy phân tích hình ảnh được cung cấp:

RÀNG BUỘC KIỂM ĐỊNH TỰ ĐỘNG:
- Nếu hình ảnh là mặt người, ảnh chân dung cá nhân, vật dụng cá nhân, hoặc KHÔNG PHẢI BÃI RÁC/ĐIỂM Ô NHIỄM THỰC TẾ -> BẮT BUỘC đặt "contains_waste": false.
- Nếu là bãi rác, điểm tập kết rác thải tự phát hoặc ô nhiễm ngoài trời -> Đặt "contains_waste": true.

Nếu "contains_waste": true, hãy phân tích kỹ:
- "waste_type": Phân loại rác rõ ràng (Ví dụ: Rác sinh hoạt, Xà bần/Đất đá, Rác cồng kềnh, Rác nhựa/Bao bì, Rác nguy hại/y tế...).
- "severity": Mức độ ô nhiễm ("Nhẹ", "Trung bình", hoặc "Nặng").
- "dispatch_plan": Đề xuất chính xác và tự nhiên lực lượng & dụng cụ cần thiết để dọn dẹp.

BẮT BUỘC TRẢ VỀ JSON THUẦN TÚY KHÔNG KÈM LỜI DẪN THEO ĐÚNG MẪU:
{
  "contains_waste": true,
  "severity": "Nhẹ" hoặc "Trung bình" hoặc "Nặng",
  "waste_type": "Tên loại rác phân tích được",
  "dispatch_plan": "Mô tả tự nhiên lực lượng và phương tiện (Ví dụ: Cần 2 công nhân + xe đẩy rác HOẶC Cần 1 xe cuốc + 1 xe tải 5 tấn + 4 công nhân)"
}
"""

    payload = {
        "prompt": prompt,
        "image": image_array,
        "max_tokens": 300,
        "temperature": 0.1,
    }

    response = requests.post(
        cloudflare_url(),
        headers={
            "Authorization": f"Bearer {CF_AUTH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )

    data = response.json()
    if not response.ok or not data.get("success", False):
      return {
          "success": False,
          "error": json.dumps(data.get("errors", data), ensure_ascii=False),
      }

    result = data.get("result", {})
    raw_response = result.get("response", "") or result.get("text", "")
    parsed = parse_ai_text(raw_response)

    return {"success": True, "parsed": parsed, "raw": data}

  except Exception as e:
    return {"success": False, "error": f"Lỗi xử lý AI: {str(e)}"}


def save_ai_result(report_id, analysis):
  conn = get_conn()

  if not analysis["success"]:
    conn.execute(
        "UPDATE reports SET ai_analyzed = 0, ai_result = ? WHERE id = ?",
        (f"❌ Lỗi: {analysis['error']}", report_id),
    )
    conn.commit()
    conn.close()
    return

  parsed = analysis["parsed"]
  raw_waste = str(parsed.get("contains_waste", "")).lower()
  is_waste = parsed.get("contains_waste") is True or raw_waste == "true"

  status = "Đã duyệt" if is_waste else "Spam/Từ chối"

  if is_waste:
    display_text = (
        f"🔴 **Phát hiện rác:** CÓ RÁC\n"
        f"🏷️ **Loại rác:** {parsed.get('waste_type', 'Rác sinh hoạt')}\n"
        f"⚠️ **Mức độ:** {parsed.get('severity', 'Trung bình')}\n"
        f"🚚 **AI Điều phối lực lượng và vật dụng:** {parsed.get('dispatch_plan', 'Bố trí 2 công nhân và xe gom rác chuyên dụng.')}"
    )
  else:
    display_text = (
        "🟢 **Phát hiện rác:** KHÔNG CÓ RÁC (Ảnh chân dung/không thuộc điểm"
        " rác)\n❌ **Kết luận:** Báo cáo bị từ chối bởi hệ thống kiểm định AI."
    )

  conn.execute(
      """
        UPDATE reports
        SET status = ?,
            ai_result = ?,
            ai_raw_json = ?,
            ai_analyzed = 1
        WHERE id = ?
    """,
      (
          status,
          display_text,
          json.dumps(analysis["raw"], ensure_ascii=False),
          report_id,
      ),
  )
  conn.commit()
  conn.close()


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

# TỰ ĐỘNG KHÓA VÀ YÊU CẦU MÃ PIN KHI ĐỔI MENU
if "last_menu" not in st.session_state:
  st.session_state["last_menu"] = menu

if st.session_state["last_menu"] != menu:
  st.session_state["team_auth_ok"] = False
  st.session_state["staff_auth_ok"] = False
  st.session_state["admin_auth_ok"] = False
  st.session_state["last_menu"] = menu

st.title("🌳 Urban GreenEye AI - Quản lý Ô nhiễm Đô thị")

# ------------------------------------------------------------
# 1. GỬI BÁO CÁO (YÊU CẦU BẮT BUỘC NHẬP THÔNG TIN)
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
    # KIỂM TRA ĐIỀU KIỆN BẮT BUỘC
    if (
        not reporter_name.strip()
        or not uploaded_file
        or not final_location.strip()
    ):
      st.error(
          "⚠️ **Không thể gửi!** Vui lòng nhập đầy đủ các thông tin bắt buộc (*):"
          " Tên người báo cáo, Ảnh hiện trường và Vị trí (chọn trên bản đồ hoặc"
          " nhập địa chỉ)."
      )
      st.stop()

    image = Image.open(uploaded_file).convert("RGB")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = os.path.join(UPLOAD_DIR, f"{timestamp}.jpg")
    image.save(image_path, format="JPEG", quality=90)

    conn = get_conn()
    cursor = conn.execute(
        """
            INSERT INTO reports (reporter_name, image_path, description, location, status, ai_result)
            VALUES (?, ?, ?, ?, 'Đã nhận', 'Đã tiếp nhận. AI đang phân tích và điều phối...')
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
      with st.spinner(
          "🤖 **AI đang phân tích, kiểm định báo cáo & điều phối lực"
          " lượng...**"
      ):
        analysis = analyze_image_with_cloudflare(image_path)
        save_ai_result(report_id, analysis)

    st.success(
        f"✅ **Đã tiếp nhận & AI duyệt xong Báo cáo #{report_id}!** Chờ đội dọn"
        " đến xử lý."
    )
    st.rerun()

# ------------------------------------------------------------
# 2. ĐỘI DỌN DẸP NHẬN NHIỆM VỤ (YÊU CẦU MÃ PIN KHI VÀO)
# ------------------------------------------------------------
elif menu == "🧹 Đội dọn dẹp nhận nhiệm vụ":
  st.header("🧹 Đội dọn dẹp tiếp nhận & Báo cáo kết quả")

  if not st.session_state.get("team_auth_ok", False):
    st.info(
        "🔒 Mục này dành riêng cho Đội dọn dẹp. Vui lòng nhập Mã PIN để truy"
        " cập."
    )
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

  # NỘI DUNG SAU KHÍ ĐÃ NHẬP ĐÚNG PIN
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
        st.success(
            f"Đội '{team_name}' đã nhận nhiệm vụ cho điểm"
            f" #{current_task['id']}!"
        )
        st.rerun()

    st.write("📸 **Nộp bằng chứng hoàn thành:**")
    cleaned_file = st.file_uploader(
        "Tải ảnh ĐÃ DỌN SẠCH RÁC:", type=["jpg", "jpeg", "png"]
    )
    cleanup_note = st.text_area("Ghi chú thu gom (Khối lượng rác, xe chở...):")

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
      st.success(
          f"🎉 Hoàn tất điểm rác #{current_task['id']}! Cộng +10đ cho"
          f" '{current_task['reporter_name']}' và +20đ cho Đội '{team_name}'!"
      )
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

    if selected_done_del:
      st.markdown("---")
      with st.popover(f"❌ Xóa {len(selected_done_del)} báo cáo đã dọn chọn"):
        pin_done_m = st.text_input(
            "Mã PIN xóa loạt:", type="password", key="pin_done_m"
        )
        if st.button("Xác nhận xóa loạt"):
          if pin_done_m in [STAFF_PIN, ADMIN_PIN]:
            delete_reports_by_ids(selected_done_del)
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
# 5. BÁO CÁO SPAM & XÓA (BẢO MẬT MÃ PIN STAFF)
# ------------------------------------------------------------
elif menu == "🗑️ Báo cáo Spam & Xóa":
  st.header("🗑 Danh sách Báo cáo Spam/Từ chối")

  # YÊU CẦU MÃ PIN STAFF HOẶC ADMIN KHI TRUY CẬP
  if not st.session_state.get("staff_auth_ok", False):
    st.warning(
        "🔒 Khu vực quản lý Spam cần xác minh. Vui lòng nhập Mã PIN Nhân viên"
        " (Staff)."
    )
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

  # NỘI DUNG SAU KHÍ ĐÃ NHẬP ĐÚNG PIN STAFF
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
# 6. RESET & CÀI ĐẶT AI (YÊU CẦU MÃ PIN ADMIN KHI VÀO)
# ------------------------------------------------------------
elif menu == "⚙️ Reset & Cài đặt AI":
  st.header("⚙️ Cấu hình Hệ thống & AI")

  if not st.session_state.get("admin_auth_ok", False):
    st.warning(
        "🔒 Khu vực này chỉ dành cho Quản trị viên (Admin). Vui lòng nhập Mã"
        " PIN ADMIN."
    )
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

  # NỘI DUNG TRANG CÀI ĐẶT SAU KHI VÀO THÀNH CÔNG
  st.subheader("🤖 Trạng thái kết nối Cloudflare AI")
  if cloudflare_configured():
    st.success("✅ Cloudflare AI đã kết nối thành công!")
    st.write(
        "• **Account ID:**"
        f" `{CF_ACCOUNT_ID[:6]}...{CF_ACCOUNT_ID[-4:] if len(CF_ACCOUNT_ID) > 10 else ''}`"
    )
    st.write(f"• **Model:** `{CF_MODEL}`")
  else:
    st.error(
        "❌ Chưa kết nối Cloudflare AI. Vui lòng bổ sung `CLOUDFLARE_ACCOUNT_ID`"
        " và `CLOUDFLARE_AUTH_TOKEN` vào Secrets."
    )

  st.markdown("---")

  st.subheader("🔄 Reset dữ liệu & Đếm Báo cáo về 1")
  st.warning(
      "⚠ Hành động này sẽ xóa TOÀN BỘ báo cáo, hình ảnh và bảng xếp hạng tích"
      " điểm!"
  )

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
        st.success(
            "✅ Đã reset toàn bộ hệ thống! Báo cáo tiếp theo sẽ tính từ #1."
        )
        st.rerun()
      else:
        st.error("Bạn nhập từ xác nhận chưa chính xác!")
