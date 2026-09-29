import os
import sqlite3
from pathlib import Path
import streamlit as st
from PIL import Image
from google import genai

# ---------------------------------------------------------
# 1. Cấu hình trang & Khởi tạo API
# ---------------------------------------------------------
st.set_page_config(
    page_title="Hệ thống Báo cáo Rác thải Thông minh",
    page_icon="🌱",
    layout="wide"
)

# Thử lấy API Key từ secrets, nếu không có thì lấy từ ô nhập liệu ở Sidebar
api_key = st.secrets.get("AQ.Ab8RN6KAHZC8PS9Ol6N78sr5zPH6-W3QHMBkqCJGQq5zqLckag", "")

with st.sidebar:
    st.header("⚙️ Cấu hình Hệ thống")
    if not api_key:
        api_key_input = st.text_input("Mờivui lòng nhập Gemini API Key:", type="password", help="Lấy API Key từ Google AI Studio")
        if api_key_input:
            api_key = api_key_input
    else:
        st.success("🔑 Đã kết nối API Key từ secrets.toml!")

    model_name = st.selectbox("Chọn mô hình AI:", ["gemini-2.5-flash", "gemini-2.5-pro"], index=0)

if not api_key:
    st.warning("⚠️ Vui lòng nhập **Gemini API Key** ở thanh bên trái (Sidebar) để bắt đầu sử dụng ứng dụng!")
    st.stop()

# Khởi tạo client Gemini
try:
    client = genai.Client(api_key=api_key)
except Exception as e:
    st.error(f"Lỗi khởi tạo Gemini Client: {e}")
    st.stop()

# Tạo thư mục lưu ảnh upload
UPLOAD_DIR = Path("uploaded_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# 2. Quản lý Cơ sở dữ liệu SQLite
# ---------------------------------------------------------
DB_FILE = "reports.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT,
            description TEXT,
            image_path TEXT,
            ai_analysis TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_report(location, description, image_path, ai_analysis):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO reports (location, description, image_path, ai_analysis) VALUES (?, ?, ?, ?)",
        (location, description, image_path, ai_analysis)
    )
    conn.commit()
    conn.close()

def get_all_reports():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, location, description, image_path, ai_analysis, created_at FROM reports ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------------------------------------------------
# 3. Giao diện ứng dụng Streamlit
# ---------------------------------------------------------
st.title("🌱 Hệ thống Báo cáo & Xử lý Rác thải Thông minh")
st.write("Gửi hình ảnh phản ánh môi trường để AI phân tích và đề xuất giải pháp xử lý.")

tab1, tab2 = st.tabs(["📝 Gửi báo cáo mới", "📊 Danh sách phản ánh"])

with tab1:
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Thông tin phản ánh")
        location = st.text_input("Địa điểm / Đơn vị phản ánh", placeholder="Ví dụ: Công viên A, Quận 1")
        description = st.text_area("Mô tả chi tiết", placeholder="Mô tả tình trạng rác thải...")
        uploaded_file = st.file_uploader("Tải lên hình ảnh rác thải", type=["jpg", "jpeg", "png"])
        
        if uploaded_file:
            image = Image.open(uploaded_file)
            st.image(image, caption="Ảnh đã chọn", use_container_width=True)

    with col2:
        st.subheader("Phân tích từ AI (Gemini)")
        if st.button("🚀 Gửi & Phân tích bằng AI", type="primary"):
            if not location or not uploaded_file:
                st.warning("Vui lòng nhập địa điểm và tải lên hình ảnh.")
            else:
                with st.spinner("AI đang phân tích hình ảnh..."):
                    try:
                        save_path = UPLOAD_DIR / uploaded_file.name
                        with open(save_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())

                        prompt = (
                            "Bạn là chuyên gia quản lý môi trường. Hãy phân tích hình ảnh này:\n"
                            "1. Xác định loại rác thải có trong hình.\n"
                            "2. Đánh giá mức độ ô nhiễm/nguy hại (Thấp/Trung bình/Cao).\n"
                            "3. Đề xuất phương án xử lý ngắn hạn và dài hạn phù hợp."
                        )
                        
                        response = client.models.generate_content(
                            model=model_name,
                            contents=[image, prompt]
                        )
                        
                        ai_result = response.text
                        save_report(location, description, str(save_path), ai_result)
                        
                        st.success("✅ Đã gửi báo cáo thành công!")
                        st.markdown(ai_result)

                    except Exception as e:
                        st.error(f"Có lỗi xảy ra: {e}")

with tab2:
    st.subheader("📋 Các phản ánh đã ghi nhận")
    reports = get_all_reports()
    
    if not reports:
        st.info("Chưa có báo cáo nào được ghi nhận.")
    else:
        for r in reports:
            report_id, r_loc, r_desc, r_img, r_ai, r_time = r
            with st.expander(f"📍 {r_loc} - [{r_time}]"):
                c_img, c_info = st.columns([1, 2])
                with c_img:
                    if os.path.exists(r_img):
                        st.image(r_img, use_container_width=True)
                with c_info:
                    st.write(f"**Mô tả:** {r_desc}")
                    st.markdown(f"**Kết quả phân tích AI:**\n{r_ai}")
