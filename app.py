import os
import sqlite3
from pathlib import Path
import streamlit as st
from PIL import Image
import folium
from streamlit_folium import st_folium
from google import genai

# =========================================================
# 1. CẤU HÌNH TRANG & CSS TRANG TRÍ MÔI TRƯỜNG
# =========================================================
st.set_page_config(
    page_title="Smart Green Reporter - Báo Cáo Môi Trường",
    page_icon="🌱",
    layout="wide"
)

# Tùy chỉnh Giao diện Màu xanh Môi trường (Green Theme)
st.markdown("""
    <style>
    /* Màu nền & Phông chữ tổng thể */
    .main {
        background-color: #f4f9f4;
    }
    
    /* Banner Header */
    .header-banner {
        background: linear-gradient(135deg, #2e7d32 0%, #66bb6a 100%);
        padding: 24px;
        border-radius: 15px;
        color: white;
        text-align: center;
        margin-bottom: 25px;
        box-shadow: 0 4px 12px rgba(46, 125, 50, 0.15);
    }
    .header-banner h1 {
        color: white !important;
        font-weight: 700;
        margin-bottom: 8px;
    }
    .header-banner p {
        font-size: 1.1rem;
        opacity: 0.95;
    }

    /* Style cho các Tab */
    .stTabs [data-baseweb="tab-list"] {
        gap: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #e8f5e9;
        border-radius: 8px 8px 0 0;
        color: #2e7d32;
        font-weight: 600;
        padding: 10px 20px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #2e7d32 !important;
        color: white !important;
    }

    /* Card container */
    .css-card {
        background-color: white;
        padding: 20px;
        border-radius: 12px;
        border-left: 5px solid #2e7d32;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        margin-bottom: 15px;
    }
    
    /* Ẩn Sidebar không cần thiết */
    [data-testid="stSidebar"] {
        display: none;
    }
    </style>
""", unsafe_allow_html=True)

# Lấy API Key tự động từ Streamlit Secrets
api_key = st.secrets.get("GEMINI_API_KEY")
model_name = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")

if not api_key:
    st.error("⚠️ Hệ thống chưa đọc được GEMINI_API_KEY từ Secrets! Hãy kiểm tra lại cấu hình Advanced Settings trên Streamlit Cloud.")
    st.stop()

# Khởi tạo Gemini Client
client = genai.Client(api_key=api_key)

UPLOAD_DIR = Path("uploaded_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# =========================================================
# 2. CƠ SỞ DỮ LIỆU SQLITE
# =========================================================
DB_FILE = "reports.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_name TEXT,
            latitude REAL,
            longitude REAL,
            description TEXT,
            image_path TEXT,
            ai_analysis TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_report(location_name, lat, lng, description, image_path, ai_analysis):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO reports (location_name, latitude, longitude, description, image_path, ai_analysis) VALUES (?, ?, ?, ?, ?, ?)",
        (location_name, lat, lng, description, image_path, ai_analysis)
    )
    conn.commit()
    conn.close()

def get_all_reports():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, location_name, latitude, longitude, description, image_path, ai_analysis, created_at FROM reports ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

# =========================================================
# 3. GIAO DIỆN ỨNG DỤNG
# =========================================================

# Banner Môi trường
st.markdown("""
    <div class="header-banner">
        <h1>🌱 Smart Green Reporter</h1>
        <p>Hệ thống Chung tay Báo cáo & Phân tích Môi trường Thông minh bằng AI</p>
    </div>
""", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["📝 Gửi Báo Cáo Phản Ánh", "🗺️ Bản Đồ & Danh Sách Báo Cáo"])

with tab1:
    col1, col2 = st.columns([1, 1], gap="large")
    
    with col1:
        st.markdown("### 📍 1. Chọn vị trí phản ánh")
        st.caption("Click vào bản đồ bên dưới để chọn tọa độ chính xác:")
        
        # Tọa độ mặc định (TP.HCM)
        default_lat, default_lng = 10.7769, 106.7009
        
        m = folium.Map(location=[default_lat, default_lng], zoom_start=13)
        folium.TileLayer('OpenStreetMap').add_to(m)
        
        map_data = st_folium(m, height=280, width="100%", key="input_map")
        
        selected_lat, selected_lng = default_lat, default_lng
        if map_data and map_data.get("last_clicked"):
            selected_lat = map_data["last_clicked"]["lat"]
            selected_lng = map_data["last_clicked"]["lng"]
            st.success(f"📍 Tọa độ đã ghim: `{selected_lat:.5f}, {selected_lng:.5f}`")
        else:
            st.info("💡 Bạn có thể click chọn vị trí trên bản đồ hoặc dùng tọa độ mặc định.")

        location_name = st.text_input("Tên khu vực / Địa chỉ (Tùy chọn)", placeholder="Ví dụ: Công viên 23/9, Quận 1")
        description = st.text_area("Ghi chú chi tiết (Tùy chọn)", placeholder="Mô tả mức độ rác thải, mùi hôi...")
        uploaded_file = st.file_uploader("📸 2. Tải ảnh thực tế điểm rác thải *", type=["jpg", "jpeg", "png"])
        
        if uploaded_file:
            image = Image.open(uploaded_file)
            st.image(image, caption="Hình ảnh đã tải lên", use_container_width=True)

    with col2:
        st.markdown("### 🤖 3. Phân tích AI & Xử lý")
        st.write("Hệ thống AI sẽ tự động phân loại rác thải và đề xuất giải pháp xử lý ngay lập tức.")
        
        if st.button("🚀 Gửi Báo Cáo & Phân Tích AI", type="primary", use_container_width=True):
            if not uploaded_file:
                st.error("⚠️ Vui lòng tải lên ít nhất 1 hình ảnh phản ánh!")
            else:
                with st.spinner("🌱 AI đang phân tích hình ảnh và đánh giá mức độ..."):
                    try:
                        save_path = UPLOAD_DIR / uploaded_file.name
                        with open(save_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())

                        prompt = (
                            "Bạn là chuyên gia quản lý môi trường đô thị. Hãy phân tích hình ảnh này:\n"
                            "1. Phân loại rác thải (nhựa, hữu cơ, rác cồng kềnh, nguy hại...).\n"
                            "2. Mức độ ô nhiễm (Thấp / Trung bình / Nghiêm trọng).\n"
                            "3. Đề xuất quy trình thu gom và xử lý ngắn/dài hạn cho đơn vị quản lý."
                        )
                        
                        response = client.models.generate_content(
                            model=model_name,
                            contents=[image, prompt]
                        )
                        
                        ai_result = response.text
                        final_loc = location_name if location_name else f"Tọa độ ({selected_lat:.4f}, {selected_lng:.4f})"
                        
                        save_report(final_loc, selected_lat, selected_lng, description, str(save_path), ai_result)
                        
                        st.balloons()
                        st.success("🎉 Cảm ơn bạn đã chung tay bảo vệ môi trường! Báo cáo đã được ghi nhận.")
                        st.markdown("---")
                        st.markdown("#### 📑 Kết quả phân tích từ AI:")
                        st.markdown(ai_result)

                    except Exception as e:
                        st.error(f"Lỗi phân tích: {e}")

with tab2:
    st.markdown("### 🗺️ Tổng hợp các điểm phản ánh trên bản đồ")
    reports = get_all_reports()
    
    if not reports:
        st.info("Chưa có báo cáo nào trên hệ thống.")
    else:
        map_all = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12)
        for r in reports:
            r_id, r_loc, r_lat, r_lng, r_desc, r_img, r_ai, r_time = r
            folium.Marker(
                [r_lat, r_lng],
                popup=f"<b>{r_loc}</b><br>{r_time}",
                tooltip=r_loc,
                icon=folium.Icon(color="green", icon="leaf", prefix="fa")
            ).add_to(map_all)
            
        st_folium(map_all, height=380, width="100%", key="view_map")
        
        st.markdown("---")
        st.markdown("### 📋 Danh sách chi tiết các phản ánh")
        for r in reports:
            r_id, r_loc, r_lat, r_lng, r_desc, r_img, r_ai, r_time = r
            with st.expander(f"📍 {r_loc} — [{r_time}]"):
                c_img, c_info = st.columns([1, 2])
                with c_img:
                    if os.path.exists(r_img):
                        st.image(r_img, use_container_width=True)
                with c_info:
                    st.write(f"**Tọa độ:** `{r_lat:.5f}, {r_lng:.5f}`")
                    st.write(f"**Mô tả của người dân:** {r_desc if r_desc else 'Không có'}")
                    st.markdown(f"**Đánh giá & Đề xuất AI:**\n{r_ai}")
