import base64
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
# URBAN GREENEYE AI
# Cloudflare Workers AI - Llama 3.2 11B Vision Instruct
# ============================================================

st.set_page_config(
    page_title="Urban GreenEye AI",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_FILE = "reports.db"
UPLOAD_DIR = "uploaded_images"
os.makedirs(UPLOAD_DIR, exist_ok=True)

CF_ACCOUNT_ID = st.secrets.get(
    "CLOUDFLARE_ACCOUNT_ID", os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
)
CF_AUTH_TOKEN = st.secrets.get(
    "CLOUDFLARE_AUTH_TOKEN", os.getenv("CLOUDFLARE_AUTH_TOKEN", "")
)
CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
STAFF_PIN = st.secrets.get("STAFF_PIN", os.getenv("STAFF_PIN", "1234"))

# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>
.main-title {
    font-size: 38px;
    font-weight: 800;
    margin-bottom: 0;
}
.subtitle {
    color: #6b7280;
    margin-bottom: 20px;
}
.card {
    padding: 18px;
    border-radius: 14px;
    border: 1px solid #e5e7eb;
    background: #ffffff;
    margin-bottom: 12px;
}
.status-wait {
    color: #b45309;
    font-weight: 700;
}
.status-ok {
    color: #15803d;
    font-weight: 700;
}
.status-spam {
    color: #b91c1c;
    font-weight: 700;
}
.small {
    color: #6b7280;
    font-size: 13px;
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# DATABASE
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
            image_path TEXT,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            status TEXT DEFAULT 'Chờ AI',
            ai_result TEXT DEFAULT '',
            ai_raw_json TEXT DEFAULT '',
            ai_analyzed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
  add_column_if_missing("ai_raw_json", "TEXT", "''")
  add_column_if_missing("ai_analyzed", "INTEGER", "0")


def cleanup_old_spam():
  """Xóa cả file ảnh và bản ghi spam cũ hơn 7 ngày."""
  conn = get_conn()
  cutoff = datetime.now() - timedelta(days=7)

  rows = conn.execute(
      """
        SELECT id, image_path
        FROM reports
        WHERE status = 'Spam/Từ chối'
        AND datetime(created_at) < datetime(?)
    """,
      (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
  ).fetchall()

  for row in rows:
    path = row["image_path"]
    if path and os.path.isfile(path):
      try:
        os.remove(path)
      except OSError:
        pass

  conn.execute(
      """
        DELETE FROM reports
        WHERE status = 'Spam/Từ chối'
        AND datetime(created_at) < datetime(?)
    """,
      (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
  )
  conn.commit()
  conn.close()


migrate_db()
cleanup_old_spam()

# ============================================================
# CLOUDFLARE AI
# ============================================================


def cloudflare_configured():
  return bool(CF_ACCOUNT_ID and CF_AUTH_TOKEN)


def cloudflare_url():
  return (
      f"https://api.cloudflare.com/client/v4/accounts/"
      f"{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
  )


def agree_to_meta_license():
  """Kích hoạt model lần đầu theo yêu cầu của Cloudflare/Meta."""
  if not cloudflare_configured():
    return False, "Chưa cấu hình CLOUDFLARE_ACCOUNT_ID hoặc CLOUDFLARE_AUTH_TOKEN."

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
      return True, "Model đã được kích hoạt."
    return False, json.dumps(data.get("errors", data), ensure_ascii=False)

  except requests.RequestException as e:
    return False, f"Lỗi kết nối Cloudflare: {e}"


def prepare_image(image_path, max_side=1024, quality=82):
  img = Image.open(image_path).convert("RGB")
  img.thumbnail((max_side, max_side))

  buffer = BytesIO()
  img.save(buffer, format="JPEG", quality=quality, optimize=True)
  return buffer.getvalue()


def parse_ai_text(text):
  """Parser được tối ưu để tránh tuyệt đối việc hiểu nhầm rác thực tế thành spam."""
  text = (text or "").strip()
  cleaned = text.replace("```json", "").replace("```", "").strip()

  try:
    data = json.loads(cleaned)
    if isinstance(data, dict):
      # Nếu chứa bất kỳ thông tin về rác thì mặc định là hợp lệ
      c_waste = data.get("contains_waste", True)
      return {
          "contains_waste": c_waste,
          "is_waste_amount_sufficient": c_waste,
          "severity": data.get("severity", "Trung bình"),
          "waste_type": data.get("waste_type", "Rác sinh hoạt"),
          "description": data.get("description", text),
          "spam_reason": data.get("spam_reason", ""),
      }
  except Exception:
    pass

  upper = text.upper()

  # Đánh giá theo từ khóa loại trừ
  is_spam = any(
      key in upper
      for key in [
          "KHÔNG PHẢI RÁC",
          "KHONG PHAI RAC",
          "SPAM",
          "KHÔNG CÓ RÁC",
          "KHONG CO RAC",
          "NO WASTE",
          "CLEAN",
      ]
  )

  is_waste = not is_spam

  if "NẶNG" in upper or "NANG" in upper or "HIGH" in upper:
    severity = "Nặng"
  elif "TRUNG BÌNH" in upper or "MEDIUM" in upper:
    severity = "Trung bình"
  else:
    severity = "Nhẹ"

  return {
      "contains_waste": is_waste,
      "is_waste_amount_sufficient": is_waste,
      "severity": severity,
      "waste_type": "Rác hỗn hợp",
      "description": text,
      "spam_reason": "" if is_waste else "AI không phát hiện rác trong ảnh",
  }


def analyze_image_with_cloudflare(image_path):
  if not cloudflare_configured():
    return {"success": False, "error": "Chưa cấu hình Cloudflare API."}

  try:
    image_bytes = prepare_image(image_path)
    image_array = list(image_bytes)

    # PROMPT ĐƯỢC TỐI ƯU RÕ RÀNG VÀ CHÍNH XÁC NƠI CÓ RÁC THỰC TẾ
    prompt = """
Bạn là AI chuyên phân tích ảnh báo cáo môi trường đô thị.

NHIỆM VỤ:
Kiểm tra xem trong bức ảnh có xuất hiện RÁC THẢI, RÁC SINH HOẠT, XÀ BẦN, TÚI NAYLON, BÃI RÁC TỰ PHÁT, hoặc BẤT KỲ CHẤT THẢI NÀO GÂY MẤT MỸ QUAN hay không.

QUY TẮC ĐÁNH GIÁ:
- Nếu nhìn thấy BẤT KỲ đống rác, túi rác, bao bì xả bừa bãi ven đường/công viên/khu dân cư -> ĐẶT `contains_waste`: true và `is_waste_amount_sufficient`: true.
- Chỉ đặt `contains_waste`: false NẾU VÀ CHỈ NẾU ảnh hoàn toàn sạch sẽ, ảnh chân dung người, ảnh phong cảnh không rác, hoặc ảnh không rõ ràng.

Trả về duy nhất JSON hợp lệ, KHÔNG dùng Markdown codeblock:
{
  "contains_waste": true,
  "is_waste_amount_sufficient": true,
  "severity": "Nhẹ" hoặc "Trung bình" hoặc "Nặng",
  "waste_type": "Rác sinh hoạt" hoặc "Xà bần" hoặc "Rác nhựa" hoặc "Rác cồng kềnh" hoặc "Khác",
  "description": "Mô tả ngắn hiện trạng rác",
  "spam_reason": ""
}
"""

    payload = {
        "prompt": prompt,
        "image": image_array,
        "max_tokens": 350,
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

    try:
      data = response.json()
    except Exception:
      return {
          "success": False,
          "error": (
              f"Cloudflare trả về dữ liệu không phải JSON: {response.text[:500]}"
          ),
      }

    if not response.ok or not data.get("success", False):
      return {
          "success": False,
          "error": json.dumps(data.get("errors", data), ensure_ascii=False),
      }

    result = data.get("result", {})
    text = result.get("response", "") or result.get("text", "")
    parsed = parse_ai_text(text)

    return {"success": True, "parsed": parsed, "raw": data, "text": text}

  except requests.RequestException as e:
    return {"success": False, "error": f"Lỗi kết nối Cloudflare: {e}"}
  except Exception as e:
    return {"success": False, "error": f"Lỗi xử lý ảnh/AI: {e}"}


def save_ai_result(report_id, analysis):
  conn = get_conn()

  if not analysis["success"]:
    conn.execute(
        """
            UPDATE reports
            SET ai_analyzed = 0,
                ai_result = ?
            WHERE id = ?
        """,
        (analysis["error"], report_id),
    )
    conn.commit()
    conn.close()
    return

  parsed = analysis["parsed"]
  contains_waste = bool(parsed.get("contains_waste", False))
  enough = bool(parsed.get("is_waste_amount_sufficient", False))

  # Nếu AI xác nhận là rác -> ĐÃ DUYỆT tự động
  if contains_waste or enough:
    status = "Đã duyệt"
  else:
    status = "Spam/Từ chối"

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
          analysis["text"],
          json.dumps(analysis["raw"], ensure_ascii=False),
          report_id,
      ),
  )

  conn.commit()
  conn.close()


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("🌿 Urban GreenEye")
st.sidebar.caption("AI giám sát điểm ô nhiễm đô thị")

menu = st.sidebar.radio(
    "Chức năng",
    [
        "📷 Gửi báo cáo",
        "📋 Quản lý & AI",
        "🗑️ Quản lý Spam",
        "⚙️ Cài đặt AI",
    ],
)

st.sidebar.divider()

if cloudflare_configured():
  st.sidebar.success("☁️ Cloudflare AI: Đã cấu hình")
else:
  st.sidebar.error("☁️️ Cloudflare AI: Chưa cấu hình")

st.sidebar.caption(f"Model: `{CF_MODEL}`")


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">🌿 Urban GreenEye AI</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="subtitle">Hệ thống tiếp nhận và phân tích báo cáo rác thải bằng'
    ' Cloudflare Vision AI.</div>',
    unsafe_allow_html=True,
)


# ============================================================
# 1. GỬI BÁO CÁO (TÍCH HỢP BẢN ĐỒ)
# ============================================================

if menu == "📷 Gửi báo cáo":

  st.header("📷 Gửi báo cáo điểm xả rác")

  uploaded_file = st.file_uploader(
      "Ảnh hiện trạng",
      type=["jpg", "jpeg", "png", "webp"],
  )

  st.write("📍 **Chọn vị trí trên bản đồ (Click để chọn vị trí rác thải):**")

  # Bản đồ Folium khởi tạo tại Việt Nam
  m = folium.Map(location=[10.762622, 106.660172], zoom_start=12)
  m.add_child(folium.LatLngPopup())

  map_data = st_folium(m, height=300, use_container_width=True)

  selected_location = ""
  if map_data and map_data.get("last_clicked"):
    lat = map_data["last_clicked"]["lat"]
    lng = map_data["last_clicked"]["lng"]
    selected_location = f"Tọa độ: {lat:.6f}, {lng:.6f}"
    st.success(f"📌 Vị trí đã chọn: {selected_location}")
  else:
    st.warning("👉 Hãy nhấp trực tiếp vào bản đồ phía trên để chọn vị trí.")

  manual_location = st.text_input(
      "Hoặc nhập địa chỉ cụ thể:",
      placeholder="Ví dụ: Tọa độ chọn trên bản đồ hoặc Tên đường, Phường/Xã...",
  )

  description = st.text_area(
      "Mô tả chi tiết",
      placeholder="Ví dụ: Đống rác sinh hoạt gây ô nhiễm bốc mùi...",
  )

  final_location = manual_location.strip() or selected_location

  if st.button("🚀 Gửi báo cáo ngay", type="primary", use_container_width=True):

    if uploaded_file is None:
      st.warning("⚠️ Vui lòng chọn ảnh.")
      st.stop()

    if not final_location:
      st.warning(
          "⚠️ Vui lòng click chọn vị trí trên bản đồ hoặc nhập địa chỉ!"
      )
      st.stop()

    try:
      image = Image.open(uploaded_file).convert("RGB")
      timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
      filename = f"{timestamp}.jpg"
      image_path = os.path.join(UPLOAD_DIR, filename)
      image.save(image_path, format="JPEG", quality=90)

      conn = get_conn()
      cursor = conn.execute(
          """
                INSERT INTO reports (
                    image_path,
                    description,
                    location,
                    status,
                    ai_result,
                    ai_analyzed
                )
                VALUES (?, ?, ?, 'Đang phân tích', 'AI đang phân tích...', 0)
            """,
          (
              image_path,
              description.strip(),
              final_location,
          ),
      )

      report_id = cursor.lastrowid
      conn.commit()
      conn.close()

      st.success(
          f"🎉 Đã tiếp nhận báo cáo **#{report_id}** và lưu ảnh thành công!"
      )

      # TỰ ĐỘNG PHÂN TÍCH AI NGẦM
      if cloudflare_configured():
        with st.spinner("🤖 AI đang tự động phân tích ảnh..."):
          analysis = analyze_image_with_cloudflare(image_path)
          save_ai_result(report_id, analysis)

        if analysis["success"]:
          st.success("🤖 AI đã tự động phân tích và xử lý báo cáo!")
        else:
          st.warning("⚠️ Báo cáo đã lưu nhưng AI gặp sự cố phân tích.")

      st.rerun()

    except Exception as e:
      st.error(f"Không thể lưu báo cáo: {e}")


# ============================================================
# 2. QUẢN LÝ & AI
# ============================================================

elif menu == "📋 Quản lý & AI":

  st.header("📋 Quản lý báo cáo")

  pin = st.text_input(
      "🔐 Mã quản trị",
      type="password",
      key="admin_pin",
  )

  if pin != STAFF_PIN:
    st.warning("Nhập đúng mã quản trị để mở khu vực xử lý AI.")
    st.stop()

  conn = get_conn()
  all_rows = conn.execute(
      "SELECT * FROM reports ORDER BY id DESC"
  ).fetchall()
  conn.close()

  if not all_rows:
    st.info("Chưa có báo cáo nào.")
    st.stop()

  for row in all_rows:
    status = row["status"]
    status_icon = (
        "🚫" if status == "Spam/Từ chối" else ("✅" if status == "Đã duyệt" else "⏳")
    )

    with st.expander(
        f"{status_icon} Báo cáo #{row['id']} — {row['location']} — {status}"
    ):
      c1, c2 = st.columns([1, 1])
      with c1:
        if row["image_path"] and os.path.isfile(row["image_path"]):
          st.image(
              row["image_path"],
              caption=f"Báo cáo #{row['id']}",
              use_container_width=True,
          )
      with c2:
        st.write(f"**Địa điểm:** {row['location']}")
        st.write(f"**Mô tả:** {row['description'] or 'Không có'}")
        st.write(f"**Trạng thái:** `{status}`")
        st.write(f"**Ngày gửi:** {row['created_at']}")
        if row["ai_result"]:
          st.info(f"**Kết quả AI:**\n{row['ai_result']}")


# ============================================================
# 3. QUẢN LÝ SPAM
# ============================================================

elif menu == "🗑️ Quản lý Spam":

  st.header("🗑️️ Quản lý Spam / Từ chối")

  pin = st.text_input(
      "🔐 Mã quản trị",
      type="password",
      key="spam_pin",
  )

  if pin != STAFF_PIN:
    st.warning("Nhập đúng mã quản trị.")
    st.stop()

  conn = get_conn()
  spam_rows = conn.execute(
      "SELECT * FROM reports WHERE status = 'Spam/Từ chối' ORDER BY id DESC"
  ).fetchall()
  conn.close()

  if not spam_rows:
    st.success("Không có báo cáo spam.")
  else:
    for row in spam_rows:
      with st.expander(f"🚫 Spam #{row['id']} — {row['location']}"):
        c1, c2 = st.columns([1, 2])
        with c1:
          if row["image_path"] and os.path.isfile(row["image_path"]):
            st.image(row["image_path"], use_container_width=True)
        with c2:
          st.write(f"**Địa điểm:** {row['location']}")
          st.write(f"**Lý do:** {row['ai_result']}")


# ============================================================
# 4. CÀI ĐẶT AI (ĐÃ ĐƯỢC BẢO VỆ BẰNG MÃ PIN)
# ============================================================

elif menu == "⚙️ Cài đặt AI":

  st.header("⚙️ Cài đặt Cloudflare AI")

  # BỔ SUNG KHÓA MÃ PIN NHƯ PHẦN QUẢN LÝ
  pin = st.text_input(
      "🔐 Mã quản trị Cài đặt",
      type="password",
      key="settings_pin",
  )

  if pin != STAFF_PIN:
    st.warning("Vui lòng nhập đúng mã quản trị để truy cập Cài đặt AI.")
    st.stop()

  st.success("🔓 Đã xác thực thành công mã quản trị!")

  st.write("**Model đang dùng:**")
  st.code(CF_MODEL)

  if cloudflare_configured():
    st.success("✅ Account ID và API Token đã được cấu hình.")
  else:
    st.error("❌ Chưa cấu hình Cloudflare Account ID / API Token.")

  st.markdown("""
### Kích hoạt model lần đầu
Nếu chưa kích hoạt thỏa thuận Meta License, hãy bấm nút bên dưới một lần:
""")

  if st.button(
      "☁️ Kích hoạt Cloudflare Vision AI",
      type="primary",
      use_container_width=True,
  ):
    with st.spinner("Đang kích hoạt model..."):
      ok, message = agree_to_meta_license()

    if ok:
      st.success(message)
    else:
      st.error(message)

  st.divider()
  st.subheader("📊 Trạng thái hệ thống")

  conn = get_conn()
  total = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
  waiting = conn.execute(
      "SELECT COUNT(*) AS n FROM reports WHERE status='Chờ AI'"
  ).fetchone()["n"]
  approved = conn.execute(
      "SELECT COUNT(*) AS n FROM reports WHERE status='Đã duyệt'"
  ).fetchone()["n"]
  spam = conn.execute(
      "SELECT COUNT(*) AS n FROM reports WHERE status='Spam/Từ chối'"
  ).fetchone()["n"]
  conn.close()

  a, b, c, d = st.columns(4)
  a.metric("Tổng", total)
  b.metric("Chờ AI", waiting)
  c.metric("Đã duyệt", approved)
  d.metric("Spam", spam)
