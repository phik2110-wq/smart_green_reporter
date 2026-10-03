import os
import sqlite3
import json
import base64
from datetime import datetime, timedelta
from io import BytesIO

import requests
from PIL import Image
import streamlit as st

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

CF_ACCOUNT_ID = st.secrets.get("CLOUDFLARE_ACCOUNT_ID", os.getenv("CLOUDFLARE_ACCOUNT_ID", ""))
CF_AUTH_TOKEN = st.secrets.get("CLOUDFLARE_AUTH_TOKEN", os.getenv("CLOUDFLARE_AUTH_TOKEN", ""))
CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
STAFF_PIN = st.secrets.get("STAFF_PIN", os.getenv("STAFF_PIN", "1234"))

# ============================================================
# CSS
# ============================================================

st.markdown("""
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
""", unsafe_allow_html=True)

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
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(reports)").fetchall()]
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

    rows = conn.execute("""
        SELECT id, image_path
        FROM reports
        WHERE status = 'Spam/Từ chối'
        AND datetime(created_at) < datetime(?)
    """, (cutoff.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()

    for row in rows:
        path = row["image_path"]
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    conn.execute("""
        DELETE FROM reports
        WHERE status = 'Spam/Từ chối'
        AND datetime(created_at) < datetime(?)
    """, (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))
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
    """
    Đọc ảnh, thu nhỏ và chuyển thành JPEG bytes.
    Không thay đổi file gốc của báo cáo.
    """
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((max_side, max_side))

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def parse_ai_text(text):
    """
    Cố gắng đọc JSON nếu model trả JSON.
    Nếu model trả văn bản thường, vẫn xử lý bằng từ khóa.
    """
    text = (text or "").strip()

    # Loại bỏ markdown code fence nếu có
    cleaned = text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    upper = text.upper()

    is_waste = not any(
        key in upper
        for key in [
            "KHÔNG PHẢI RÁC",
            "KHONG PHAI RAC",
            "SPAM",
            "KHÔNG CÓ RÁC",
            "KHONG CO RAC",
        ]
    )

    if "NẶNG" in upper or "NANG" in upper:
        severity = "Nặng"
    elif "TRUNG BÌNH" in upper or "TRUNG BINH" in upper:
        severity = "Trung bình"
    else:
        severity = "Nhẹ"

    return {
        "contains_waste": is_waste,
        "severity": severity,
        "description": text,
        "raw_text": text,
    }


def analyze_image_with_cloudflare(image_path):
    """
    Gọi Cloudflare Workers AI Vision.
    Chỉ được gọi khi admin bấm nút phân tích.
    """
    if not cloudflare_configured():
        return {
            "success": False,
            "error": "Chưa cấu hình Cloudflare API.",
        }

    try:
        image_bytes = prepare_image(image_path)

        # Cloudflare REST API nhận image dưới dạng mảng byte cho model này.
        image_array = list(image_bytes)

        prompt = """
Bạn là AI kiểm tra báo cáo môi trường đô thị.

Hãy phân tích DUY NHẤT hình ảnh được cung cấp.

Trả về JSON hợp lệ, không Markdown, theo đúng mẫu:
{
  "contains_waste": true,
  "is_waste_amount_sufficient": true,
  "severity": "Nhẹ",
  "waste_type": "Rác sinh hoạt",
  "description": "Mô tả ngắn gọn hiện trạng",
  "spam_reason": ""
}

Quy tắc:
- contains_waste = true nếu ảnh thực sự có rác/chất thải/bãi rác hoặc điểm xả rác.
- contains_waste = false nếu không có rác, ảnh không liên quan, ảnh quá khó xác định hoặc chỉ là ảnh thông thường.
- is_waste_amount_sufficient = true nếu lượng rác/điểm ô nhiễm đủ rõ để coi là một báo cáo môi trường.
- severity chỉ được là: "Nhẹ", "Trung bình", "Nặng".
- waste_type có thể là: "Rác sinh hoạt", "Xà bần", "Rác nhựa", "Rác cồng kềnh", "Rác hỗn hợp", "Khác".
- Nếu không phải báo cáo rác hợp lệ, ghi lý do ngắn vào spam_reason.
- Không suy đoán địa điểm hoặc thông tin không nhìn thấy trong ảnh.
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
                "error": f"Cloudflare trả về dữ liệu không phải JSON: {response.text[:500]}",
            }

        if not response.ok or not data.get("success", False):
            return {
                "success": False,
                "error": json.dumps(
                    data.get("errors", data),
                    ensure_ascii=False,
                ),
            }

        result = data.get("result", {})
        text = result.get("response", "")

        if not text:
            text = result.get("text", "")

        parsed = parse_ai_text(text)

        return {
            "success": True,
            "parsed": parsed,
            "raw": data,
            "text": text,
        }

    except requests.RequestException as e:
        return {
            "success": False,
            "error": f"Lỗi kết nối Cloudflare: {e}",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Lỗi xử lý ảnh/AI: {e}",
        }


def save_ai_result(report_id, analysis):
    conn = get_conn()

    if not analysis["success"]:
        conn.execute("""
            UPDATE reports
            SET ai_analyzed = 0,
                ai_result = ?
            WHERE id = ?
        """, (analysis["error"], report_id))
        conn.commit()
        conn.close()
        return

    parsed = analysis["parsed"]

    contains_waste = bool(parsed.get("contains_waste", False))
    enough = bool(parsed.get("is_waste_amount_sufficient", False))

    if not contains_waste or not enough:
        status = "Spam/Từ chối"
    else:
        status = "Đã duyệt"

    conn.execute("""
        UPDATE reports
        SET status = ?,
            ai_result = ?,
            ai_raw_json = ?,
            ai_analyzed = 1
        WHERE id = ?
    """, (
        status,
        analysis["text"],
        json.dumps(analysis["raw"], ensure_ascii=False),
        report_id,
    ))

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
    st.sidebar.error("☁️ Cloudflare AI: Chưa cấu hình")

st.sidebar.caption(f"Model: `{CF_MODEL}`")
st.sidebar.caption("AI tự động phân tích ngay sau khi người dân gửi ảnh.")


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">🌿 Urban GreenEye AI</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="subtitle">Hệ thống tiếp nhận và phân tích báo cáo rác thải bằng Cloudflare Vision AI.</div>',
    unsafe_allow_html=True,
)


# ============================================================
# 1. GỬI BÁO CÁO
# ============================================================

if menu == "📷 Gửi báo cáo":

    st.header("📷 Gửi báo cáo điểm xả rác")

    st.info(
        "Ảnh được lưu vào hệ thống trước, sau đó Cloudflare Vision AI "
        "tự động phân tích ngay. Người dân không cần bấm nút phân tích."
    )

    with st.form("report_form", clear_on_submit=True):

        uploaded_file = st.file_uploader(
            "Ảnh hiện trạng",
            type=["jpg", "jpeg", "png", "webp"],
        )

        location = st.text_input(
            "Địa điểm",
            placeholder="Ví dụ: Công viên, đường, khu dân cư..."
        )

        description = st.text_area(
            "Mô tả",
            placeholder="Ví dụ: Có nhiều túi rác bên lề đường..."
        )

        submitted = st.form_submit_button(
            "🚀 Gửi báo cáo ngay",
            use_container_width=True,
        )

    if submitted:

        if uploaded_file is None:
            st.warning("⚠️ Vui lòng chọn ảnh.")
            st.stop()

        if not location.strip():
            st.warning("⚠️ Vui lòng nhập địa điểm.")
            st.stop()

        try:
            # Đọc và kiểm tra ảnh trước khi lưu
            image = Image.open(uploaded_file).convert("RGB")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{timestamp}.jpg"
            image_path = os.path.join(UPLOAD_DIR, filename)

            image.save(
                image_path,
                format="JPEG",
                quality=90,
            )

            # ========================================================
            # 1. LƯU BÁO CÁO NGAY VÀO DATABASE
            # ========================================================
            # Ảnh được lưu trước. Sau khi INSERT thành công,
            # hệ thống tự động gọi AI - người dân không cần bấm thêm nút nào.
            conn = get_conn()
            cursor = conn.execute("""
                INSERT INTO reports (
                    image_path,
                    description,
                    location,
                    status,
                    ai_result,
                    ai_analyzed
                )
                VALUES (?, ?, ?, 'Đang phân tích', 'AI đang phân tích...', 0)
            """, (
                image_path,
                description.strip(),
                location.strip(),
            ))

            report_id = cursor.lastrowid
            conn.commit()
            conn.close()

            st.success(
                f"🎉 Đã tiếp nhận báo cáo **#{report_id}** và lưu ảnh thành công!"
            )

            # ========================================================
            # 2. TỰ ĐỘNG PHÂN TÍCH AI
            # ========================================================
            # Không cần admin gọi. Ngay sau khi lưu DB, Cloudflare Vision
            # được gọi tự động.
            if not cloudflare_configured():
                conn = get_conn()
                conn.execute("""
                    UPDATE reports
                    SET status = 'Chờ AI',
                        ai_result = ?
                    WHERE id = ?
                """, (
                    "Chưa thể phân tích tự động: chưa cấu hình Cloudflare AI.",
                    report_id,
                ))
                conn.commit()
                conn.close()

                st.warning(
                    "⚠️ Ảnh đã lưu nhưng Cloudflare AI chưa được cấu hình. "
                    "Hãy cấu hình API để AI tự động phân tích."
                )
            else:
                with st.spinner("🤖 AI đang tự động phân tích ảnh..."):
                    analysis = analyze_image_with_cloudflare(image_path)
                    save_ai_result(report_id, analysis)

                if analysis["success"]:
                    parsed = analysis["parsed"]
                    contains_waste = bool(parsed.get("contains_waste", False))
                    enough = bool(
                        parsed.get("is_waste_amount_sufficient", False)
                    )

                    if contains_waste and enough:
                        st.success(
                            "🤖 AI đã phân tích xong: báo cáo hợp lệ và đã được duyệt."
                        )
                    else:
                        st.warning(
                            "🤖 AI đã phân tích: báo cáo không đủ điều kiện "
                            "và đã được đưa vào Spam/Từ chối."
                        )

                    st.info(
                        f"**Kết quả AI:** "
                        f"{analysis.get('text', 'Không có kết quả')}"
                    )
                else:
                    st.error(
                        "⚠️ Ảnh đã được lưu nhưng AI phân tích bị lỗi. "
                        "Báo cáo được giữ lại để có thể xử lý lại."
                    )
                    st.caption(analysis.get("error", "Lỗi không xác định."))

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

    pending = conn.execute("""
        SELECT *
        FROM reports
        WHERE status = 'Chờ AI'
        ORDER BY id DESC
    """).fetchall()

    all_rows = conn.execute("""
        SELECT *
        FROM reports
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    c1, c2, c3 = st.columns(3)
    c1.metric("Tổng báo cáo", len(all_rows))
    c2.metric("Đang chờ AI", len(pending))
    c3.metric(
        "Đã duyệt",
        sum(1 for r in all_rows if r["status"] == "Đã duyệt")
    )

    st.divider()

    if pending:
        st.subheader("🤖 Báo cáo chưa được AI phân tích")

        if not cloudflare_configured():
            st.error(
                "Chưa cấu hình Cloudflare. Hãy vào mục 'Cài đặt AI'."
            )
        else:

            if st.button(
                f"🤖 Phân tích tất cả {len(pending)} báo cáo đang chờ",
                type="primary",
                use_container_width=True,
            ):
                progress = st.progress(0)
                success_count = 0
                error_count = 0

                for index, row in enumerate(pending, start=1):

                    if not row["image_path"] or not os.path.isfile(row["image_path"]):
                        conn = get_conn()
                        conn.execute("""
                            UPDATE reports
                            SET ai_result = ?
                            WHERE id = ?
                        """, ("Không tìm thấy file ảnh.", row["id"]))
                        conn.commit()
                        conn.close()
                        error_count += 1
                    else:
                        result = analyze_image_with_cloudflare(row["image_path"])

                        if result["success"]:
                            save_ai_result(row["id"], result)
                            success_count += 1
                        else:
                            save_ai_result(row["id"], result)
                            error_count += 1

                    progress.progress(index / len(pending))

                st.success(
                    f"Hoàn tất: {success_count} thành công, {error_count} lỗi."
                )
                st.rerun()

    else:
        st.info("Không có báo cáo đang chờ AI.")

    st.divider()
    st.subheader("📚 Danh sách báo cáo")

    if not all_rows:
        st.info("Chưa có báo cáo.")
        st.stop()

    for row in all_rows:

        status = row["status"]

        if status == "Spam/Từ chối":
            status_icon = "🚫"
        elif status == "Đã duyệt":
            status_icon = "✅"
        else:
            status_icon = "⏳"

        with st.expander(
            f"{status_icon} Báo cáo #{row['id']} — {row['location']} — {status}"
        ):

            left, right = st.columns([1, 1])

            with left:
                if row["image_path"] and os.path.isfile(row["image_path"]):
                    st.image(
                        row["image_path"],
                        caption=f"Báo cáo #{row['id']}",
                        use_container_width=True,
                    )
                else:
                    st.warning("Không tìm thấy ảnh.")

            with right:
                st.write(f"**Địa điểm:** {row['location']}")
                st.write(
                    f"**Mô tả:** {row['description'] or 'Không có'}"
                )
                st.write(f"**Trạng thái:** `{status}`")
                st.write(f"**Ngày gửi:** {row['created_at']}")

                if row["ai_result"]:
                    st.write("### 🤖 Kết quả AI")
                    st.info(row["ai_result"])

                if (
                    status == "Chờ AI"
                    and cloudflare_configured()
                    and row["image_path"]
                    and os.path.isfile(row["image_path"])
                ):
                    if st.button(
                        f"🤖 Phân tích #{row['id']}",
                        key=f"analyze_{row['id']}",
                        use_container_width=True,
                    ):
                        with st.spinner("Cloudflare Vision AI đang phân tích..."):
                            result = analyze_image_with_cloudflare(
                                row["image_path"]
                            )
                            save_ai_result(row["id"], result)

                        if result["success"]:
                            st.success("Đã phân tích xong.")
                        else:
                            st.error(result["error"])

                        st.rerun()


# ============================================================
# 3. QUẢN LÝ SPAM
# ============================================================

elif menu == "🗑️ Quản lý Spam":

    st.header("🗑️ Quản lý Spam / Từ chối")

    pin = st.text_input(
        "🔐 Mã quản trị",
        type="password",
        key="spam_pin",
    )

    if pin != STAFF_PIN:
        st.warning("Nhập đúng mã quản trị.")
        st.stop()

    conn = get_conn()
    spam_rows = conn.execute("""
        SELECT *
        FROM reports
        WHERE status = 'Spam/Từ chối'
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    st.info(
        "Báo cáo bị AI xác định là không hợp lệ sẽ nằm ở đây và "
        "tự động bị xóa cả ảnh + bản ghi sau 7 ngày."
    )

    if not spam_rows:
        st.success("Không có báo cáo spam.")
    else:

        st.metric("Spam hiện tại", len(spam_rows))

        for row in spam_rows:

            with st.expander(
                f"🚫 Spam #{row['id']} — {row['location']}"
            ):

                c1, c2 = st.columns([1, 2])

                with c1:
                    if row["image_path"] and os.path.isfile(row["image_path"]):
                        st.image(
                            row["image_path"],
                            use_container_width=True,
                        )

                with c2:
                    st.write(f"**Địa điểm:** {row['location']}")
                    st.write(f"**Ngày tạo:** {row['created_at']}")
                    st.write("**Lý do / kết quả AI:**")
                    st.write(row["ai_result"] or "Không có")

                    if st.button(
                        f"🗑️ Xóa ngay #{row['id']}",
                        key=f"delete_spam_{row['id']}",
                    ):
                        path = row["image_path"]

                        if path and os.path.isfile(path):
                            try:
                                os.remove(path)
                            except OSError:
                                pass

                        conn = get_conn()
                        conn.execute(
                            "DELETE FROM reports WHERE id = ?",
                            (row["id"],),
                        )
                        conn.commit()
                        conn.close()

                        st.success("Đã xóa vĩnh viễn.")
                        st.rerun()


# ============================================================
# 4. CÀI ĐẶT AI
# ============================================================

elif menu == "⚙️ Cài đặt AI":

    st.header("⚙️ Cài đặt Cloudflare AI")

    st.write("**Model đang dùng:**")
    st.code(CF_MODEL)

    if cloudflare_configured():
        st.success("✅ Account ID và API Token đã được cấu hình.")
    else:
        st.error(
            "❌ Chưa cấu hình Cloudflare Account ID / API Token."
        )

    st.markdown("""
### Cấu hình Streamlit Secrets

Tạo file:

`.streamlit/secrets.toml`

và đặt:

```toml
CLOUDFLARE_ACCOUNT_ID = "YOUR_ACCOUNT_ID"
CLOUDFLARE_AUTH_TOKEN = "YOUR_API_TOKEN"
STAFF_PIN = "YOUR_ADMIN_PIN"
```

### Kích hoạt model lần đầu

Cloudflare yêu cầu chấp nhận Meta License/AUP trước lần sử dụng đầu tiên.
Nhấn nút bên dưới một lần sau khi đã cấu hình API.
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
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM reports"
    ).fetchone()["n"]
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

    st.caption(
        "Lưu ý: Workers AI có hạn mức miễn phí theo Neurons/ngày; "
        "đây không phải API miễn phí vô hạn."
    )
