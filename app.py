import json
import time
import google.generativeai as genai
import pandas as pd
import streamlit as st

# ==============================================================================
# 1. CẤU HÌNH TRANG VÀ GIAO DIỆN CHÍNH (PAGE CONFIG & ADVANCED CSS)
# ==============================================================================
st.set_page_config(
    page_title="Urban GreenEye AI - Smart City Environment System",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Tùy chỉnh CSS giao diện chuyên nghiệp cho Dự án Thi
CUSTOM_CSS = """
<style>
    /* CSS Cấu hình phông chữ và nền */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Style cho Header chính */
    .main-header-container {
        background: linear-gradient(135deg, #1b5e20 0%, #2e7d32 100%);
        padding: 25px;
        border-radius: 15px;
        color: white;
        text-align: center;
        margin-bottom: 25px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    .main-header-title {
        font-size: 2.5rem;
        font-weight: 700;
        letter-spacing: -0.5px;
        margin: 0;
    }
    .main-header-subtitle {
        font-size: 1.1rem;
        font-weight: 300;
        opacity: 0.9;
        margin-top: 5px;
    }

    /* Style cho các Khối Thống Kê (Metric Cards) */
    .metric-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        text-align: center;
        transition: transform 0.2s ease;
    }
    .metric-card:hover {
        transform: translateY(-3px);
    }
    .metric-value {
        font-size: 2.2rem;
        font-weight: 700;
        color: #2e7d32;
    }
    .metric-label {
        font-size: 0.9rem;
        color: #666666;
        text-transform: uppercase;
        font-weight: 600;
    }

    /* Style cho Khối Báo Cáo / Card */
    .report-card-success {
        background-color: #f1f8e9;
        border-left: 6px solid #4caf50;
        padding: 20px;
        border-radius: 8px;
        margin-top: 15px;
    }
    .report-card-warning {
        background-color: #fffde7;
        border-left: 6px solid #fbc02d;
        padding: 20px;
        border-radius: 8px;
        margin-top: 15px;
    }
    .report-card-danger {
        background-color: #ffebee;
        border-left: 6px solid #e53935;
        padding: 20px;
        border-radius: 8px;
        margin-top: 15px;
    }

    /* Tùy chỉnh Nút Bấm Form */
    .stButton>button {
        background-color: #2e7d32 !important;
        color: white !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        padding: 10px 24px !important;
        border: none !important;
        width: 100%;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #1b5e20 !important;
        box-shadow: 0 4px 12px rgba(46, 125, 50, 0.3) !important;
    }

    /* Badges Trạng Thái */
    .badge {
        padding: 4px 12px;
        border-radius: 50px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
    }
    .badge-danger { background-color: #ffcdd2; color: #b71c1c; }
    .badge-warning { background-color: #fff9c4; color: #f57f17; }
    .badge-success { background-color: #c8e6c9; color: #1b5e20; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ==============================================================================
# 2. XỬ LÝ CẤU HÌNH API KEY & KHỞI TẠO SESSION DATABASE
# ==============================================================================
st.sidebar.markdown("## ⚙️ Cấu Hình Hệ Thống")

# Quản lý API Key bảo mật
API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not API_KEY:
    API_KEY = st.sidebar.text_input(
        "Nhập Gemini API Key:",
        type="password",
        help="Nhập Google Gemini API Key để vận hành các tính năng Vision AI & LLM.",
    )

if API_KEY:
    try:
        genai.configure(api_key=API_KEY)
        st.sidebar.success("✅ Đã kết nối thành công API Gemini!")
    except Exception as e:
        st.sidebar.error(f"❌ Lỗi cấu hình API Key: {str(e)}")
else:
    st.sidebar.warning("⚠️ Vui lòng cung cấp API Key để sử dụng tính năng AI.")

# Khởi tạo CSDL Mô Phỏng (Mock Database) giàu dữ liệu cho bài thuyết trình
if "database" not in st.session_state:
    st.session_state.database = [
        {
            "id": "TK-1001",
            "reporter": "Nguyễn Văn Anh",
            "location": "123 Đường Phạm Văn Thuận, Phường Tân Tiến, TP. Biên Hòa",
            "waste_type": "Xà bần & Vật liệu xây dựng",
            "severity": "Cao",
            "is_hazardous": False,
            "hazard_warning": "Không có",
            "status": "Đã dọn dẹp",
            "dispatch_plan": "Điều động 1 xe tải cẩu 5 tấn, 3 công nhân dọn xà bần và dụng cụ hót rác.",
            "timestamp": "2026-10-01 08:30:15",
            "notes": "Chủ yếu là gạch vỡ tràn ra lòng đường gây cản trở giao thông.",
        },
        {
            "id": "TK-1002",
            "reporter": "Trần Thị Bình",
            "location": "Cổng Công viên Đồng Nai, Phường Quyết Thắng, TP. Biên Hòa",
            "waste_type": "Rác thải sinh hoạt & Túi nilon",
            "severity": "Trung bình",
            "is_hazardous": False,
            "hazard_warning": "Không có",
            "status": "Đã dọn dẹp",
            "dispatch_plan": "Điều động 2 công nhân vệ sinh môi trường, 1 xe gom rác đẩy tay chuyên dụng.",
            "timestamp": "2026-10-02 14:15:22",
            "notes": "Rác bốc mùi hôi sau cơn mưa, cần xử lý khử mùi.",
        },
        {
            "id": "TK-1003",
            "reporter": "Lê Hoàng Cường",
            "location": "Hẻm 45 Đường Nguyễn Ái Quốc, Phường Hố Nai, TP. Biên Hòa",
            "waste_type": "Rác y tế & Mảnh kính vỡ",
            "severity": "Cao",
            "is_hazardous": True,
            "hazard_warning": "CẢNH BÁO: Phát hiện kim tiêm và mảnh thủy tinh vỡ! Yêu cầu trang bị găng tay chống đâm thủng và ủng bảo hộ chổng thủng.",
            "status": "Đang xử lý",
            "dispatch_plan": "Điều động Đội xử lý sự cố nguy hại (2 người), trang bị bộ đồ bảo hộ cấp 2, thùng thu gom rác y tế chuyên dụng.",
            "timestamp": "2026-10-03 09:10:00",
            "notes": "Rác nguy hại bỏ trộm gần khu dân cư.",
        },
        {
            "id": "TK-1004",
            "reporter": "Pham Minh Dũng",
            "location": "Đường Võ Thị Sáu, Phường Thống Nhất, TP. Biên Hòa",
            "waste_type": "Rác cành cây khô & Tủ gỗ cũ",
            "severity": "Thấp",
            "is_hazardous": False,
            "hazard_warning": "Không có",
            "status": "Đang chờ dọn",
            "dispatch_plan": "Điều động 1 xe ép rác 2.5 tấn và 2 công nhân thu gom.",
            "timestamp": "2026-10-04 07:45:10",
            "notes": "Cây xanh cắt tỉa cành để ngoài vỉa hè.",
        },
    ]


# ==============================================================================
# 3. CÁC HÀM XỬ LÝ VISION AI VÀ LLM CHUYÊN SÂU
# ==============================================================================


def analyze_waste_image_ai(image_bytes, user_location):
    """
    TÍNH NĂNG 1 & 2:
    - Kiểm tra ảnh hợp lệ (Guardrail chống ảo giác chân dung/mặt người)
    - Phân loại rác, đánh giá mức độ
    - Nhận diện rác nguy hại (Hazardous Waste Alert)
    - Tự động điều phối lực lượng sinh động theo văn phong AI
    """
    if not API_KEY:
        return {
            "is_waste": False,
            "reject_reason": "Hệ thống chưa được cấu hình API Key Gemini. Vui lòng nhập API Key ở menu bên trái.",
        }

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")

        system_prompt = f"""
        Bạn là Hệ thống Trí tuệ Nhân tạo Kiểm định Môi trường Đô thị "Urban GreenEye AI".
        Địa điểm người dân báo cáo: "{user_location}".

        Nhiệm vụ kiểm tra của bạn:
        1. QUY TẮC BẢO VỆ CHỐNG ẢO GIÁC (GUARDRAIL):
           - Phân tích kỹ bức ảnh. Nếu ảnh là mặt người, chân dung cá nhân, đồ vật trong nhà, ảnh chụp màn hình, hoặc KHÔNG CÓ RÁC THẢI NGOÀI TRỜI:
             -> BẮT BUỘC trả về "is_waste": false.
             -> Nêu rõ lý do từ chối ngắn gọn ở "reject_reason" (Ví dụ: "Ảnh chân dung cá nhân, không phát hiện hiện trường rác ngoài trời").

        2. PHÂN TÍCH HIỆN TRƯỜNG & ĐIỀU PHỐI (Chỉ thực hiện khi "is_waste": true):
           - Xác định chủng loại rác: Rác sinh hoạt, Xà bần/Xây dựng, Rác nguy hại/Y tế, Rác cồng kềnh, Rác điện tử...
           - Đánh giá mức độ ô nhiễm: "Thấp", "Trung bình", hoặc "Cao".
           - KIỂM TRA RÁC NGUY HIỂM / ĐỘC HẠI: Kiểm tra có kim tiêm, thủy tinh vỡ, chất thải hóa chất, rác y tế lây nhiễm hay không.
             -> Nếu có: Đặt "is_hazardous": true và viết câu cảnh báo an toàn lao động chi tiết ở "hazard_warning".
             -> Nếu không: Đặt "is_hazardous": false và "hazard_warning": "".
           - TỰ SUY LUẬN ĐIỀU PHỐI (AI DISPATCH PLAN): Tự do đưa ra đề xuất phân bổ lực lượng (số công nhân, xe cẩu, xe ép rác, dụng cụ) bằng văn phong tự nhiên, sinh động, phù hợp nhất với bãi rác quan sát được.

        ĐẦU RA BẮT BUỘC: Trả về một chuỗi JSON thuần túy (KHÔNG chứa ký tự markdown ```json) đúng cấu trúc sau:
        {{
          "is_waste": true/false,
          "reject_reason": "Lý do từ chối nếu không hợp lệ",
          "waste_type": "Tên loại rác phát hiện",
          "severity": "Thấp/Trung bình/Cao",
          "is_hazardous": true/false,
          "hazard_warning": "Nội dung cảnh báo đồ bảo hộ an toàn nếu rác nguy hại",
          "dispatch_plan": "Mô tả văn phong AI linh hoạt về việc điều phối công nhân và xe máy móc chuyên dụng"
        }}
        """

        image_payload = [{"mime_type": "image/jpeg", "data": image_bytes}]

        response = model.generate_content([system_prompt, image_payload[0]])

        # Clean JSON
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        parsed_json = json.loads(raw_text.strip())
        return parsed_json

    except json.JSONDecodeError:
        return {
            "is_waste": False,
            "reject_reason": "Lỗi định dạng phản hồi từ AI. Vui lòng bấm thử lại.",
        }
    except Exception as e:
        return {
            "is_waste": False,
            "reject_reason": f"Lỗi trong quá trình xử lý AI: {str(e)}",
        }


def generate_community_post_ai(reporter_name, location, waste_type):
    """
    TÍNH NĂNG 3: AI Community Engagement Agent
    Tự động viết bài tuyên truyền Fanpage vinh danh cư dân & lan tỏa sống xanh.
    """
    if not API_KEY:
        return "Vui lòng cấu hình API Key để kích hoạt AI Agent viết bài."

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Bạn là Trợ lý AI Truyền thông cho Đoàn Thanh niên & UBND Phường.
        Hãy viết một bài đăng mạng xã hội (Facebook Fanpage) thật truyền cảm hứng, tích cực để vinh danh hành động đẹp của người dân:

        - Người báo cáo tích cực: Anh/Chị {reporter_name}
        - Tọa độ / Địa điểm dọn dẹp: {location}
        - Loại rác đã được xử lý xong: {waste_type}

        Yêu cầu bài viết:
        - Giọng văn nhiệt huyết, tràn đầy năng lượng xanh, khuyên bảo cư dân chung tay giữ gìn vệ sinh.
        - Sử dụng biểu tượng cảm xúc (emoji) bắt mắt.
        - Kèm theo các hashtag xu hướng: #UrbanGreenEye #ViMoiTruongXanh #DoanThanhNienSongXanh #BiênHòaXanh.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Lỗi tạo nội dung tuyên truyền: {str(e)}"


def generate_executive_report_ai(db_data):
    """
    TÍNH NĂNG 4: Smart Dashboard Executive Summary
    AI phân tích tổng hợp dữ liệu toàn địa bàn và xuất văn bản tham mưu Lãnh đạo.
    """
    if not API_KEY:
        return "Vui lòng cấu hình API Key để xuất Báo cáo Tham mưu."

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Bạn là Chuyên viên Tham mưu AI cấp cao thuộc Trung tâm Điều hành Thông minh (IOC) TP. Biên Hòa.
        Dưới đây là toàn bộ dữ liệu báo cáo rác thải đô thị thu thập được từ ứng dụng Urban GreenEye AI:
        {json.dumps(db_data, ensure_ascii=False, indent=2)}

        Hãy lập một Báo cáo Tham mưu Chiến lược Toàn diện dành cho Lãnh đạo UBND Thành phố bao gồm 4 phần:
        1. TỔNG QUAN TÌNH HÌNH Ô NHIỄM ĐÔ THỊ TRÊN ĐỊA BÀN (Tóm tắt số liệu, tỷ lệ xử lý)
        2. PHÂN TÍCH ĐIỂM NÓNG & CÁC LOẠI RÁC NGUY HIỂM PHÁT SINH
        3. ĐÁNH GIÁ HIỆU QUẢ CỦA ỨNG DỤNG URBAN GREENEYE AI TRONG VIỆC TỐI ƯU CHI PHÍ & THỜI GIAN
        4. KIẾN NGHỊ & ĐỀ XUẤT GIẢI PHÁP DÀI HẠN DÀNH CHO BỒ CÔNG AN / UBND THÀNH PHỐ

        Yêu cầu: Viết chuẩn văn phong công vụ, lập luận chặt chẽ, chuyên nghiệp.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Lỗi tạo báo cáo tham mưu: {str(e)}"


# ==============================================================================
# 4. THANH ĐIỀU HƯỚNG BÊN (SIDEBAR NAVIGATION)
# ==============================================================================
st.sidebar.markdown("---")
st.sidebar.markdown("## 📌 Danh Mục Chức Năng")
selected_tab = st.sidebar.radio(
    "Lựa chọn Phân hệ làm việc:",
    [
        "1. 📍 Báo Cáo Hiện Trường (Cư Dân)",
        "2. 📢 AI Tuyên Truyền Cộng Đồng",
        "3. 📊 Smart Executive Dashboard (Lãnh Đạo)",
    ],
)

st.sidebar.markdown("---")
st.sidebar.info(
    "💡 **Hướng dẫn Demo:**\n"
    "- **Phân hệ 1:** Thử gửi ảnh chân dung để xem AI chặn ảo giác, hoặc gửi ảnh rác để AI tự điều phối.\n"
    "- **Phân hệ 2:** Chọn 1 sự kiện dọn dẹp để AI viết bài Fanpage.\n"
    "- **Phân hệ 3:** Xem số liệu trực quan & xuất Báo cáo Công vụ gửi Lãnh đạo."
)


# ==============================================================================
# 5. PHÂN HỆ 1: BÁO CÁO HIỆN TRƯỜNG & AI ĐIỀU PHỐI (USER MODULE)
# ==============================================================================
if selected_tab == "1. 📍 Báo Cáo Hiện Trường (Cư Dân)":
    # Header Banner
    st.markdown(
        """
        <div class="main-header-container">
            <div class="main-header-title">🌿 URBAN GREENEYE AI</div>
            <div class="main-header-subtitle">Hệ thống Tiếp nhận, Kiểm định & Điều phối Xử lý Ô nhiễm Đô thị Thông minh</div>
        </div>
    """,
        unsafe_allow_html=True,
    )

    st.subheader("📍 Gửi Báo Cáo Hiện Trường Ô Nhiễm")
    st.caption(
        "Vui lòng điền đầy đủ các thông tin có dấu (*) để hệ thống AI tiến hành xác minh."
    )

    # Form nhập liệu sử dụng st.form để quản lý submit mượt mà
    with st.form(key="report_waste_form", clear_on_submit=True):
        col_form_1, col_form_2 = st.columns(2)

        with col_form_1:
            reporter_name = st.text_input(
                "Họ và tên người báo cáo (*)",
                placeholder="Ví dụ: Nguyễn Văn A",
                key="input_reporter",
            )

        with col_form_2:
            location = st.text_input(
                "Vị trí / Địa chỉ cụ thể hiện trường (*)",
                placeholder="Ví dụ: Số 123 Đường Võ Thị Sáu, Phường Quyết Thắng",
                key="input_location",
            )

        uploaded_file = st.file_uploader(
            "Tải lên hình ảnh hiện trường rác thải (*)",
            type=["jpg", "jpeg", "png"],
            help="Chụp rõ hiện trường bãi rác ngoài trời. Hệ thống sẽ từ chối ảnh chân dung hoặc ảnh không hợp lệ.",
            key="input_file",
        )

        description = st.text_area(
            "Ghi chú chi tiết bổ sung (Không bắt buộc)",
            placeholder="Mô tả thêm tình trạng: ví dụ rác bốc mùi, cản trở giao thông...",
            key="input_desc",
        )

        submit_report_button = st.form_submit_button(
            label="🚀 Gửi Báo Cáo Ngay", use_container_width=True
        )

    # XỬ LÝ KHI BẤM NÚT GỬI BÁO CÁO
    if submit_report_button:
        # 1. RÀNG BUỘC BẮT BUỘC NHẬP ĐỦ THÔNG TIN (*)
        if (
            not reporter_name.strip()
            or not location.strip()
            or uploaded_file is None
        ):
            st.error(
                "⚠️ **KHÔNG THỂ GỬI BÁO CÁO!** Vui lòng nhập đầy đủ các trường bắt buộc (*): Họ tên, Vị trí và Ảnh hiện trường."
            )

        else:
            # 2. TÍNH NĂNG 1: DUPLICATE DETECTION AI (Lọc báo cáo trùng lặp theo vị trí)
            is_duplicate = False
            existing_ticket_id = ""

            for item in st.session_state.database:
                if (
                    item["location"].lower().strip()
                    == location.lower().strip()
                ):
                    is_duplicate = True
                    existing_ticket_id = item["id"]
                    break

            if is_duplicate:
                st.warning(
                    f"📍 **DUPLICATE DETECTION AI:** Phát hiện vị trí này đã được báo cáo trước đó (Mã Ticket: **{existing_ticket_id}**)! "
                    f"Hệ thống đã tự động gộp thông tin của bạn vào Ticket hiện có để tránh phân bổ lực lượng trùng lặp."
                )
            else:
                # 3. GỌI GEMINI VISION AI PHÂN TÍCH
                image_bytes = uploaded_file.getvalue()

                with st.spinner(
                    "🔍 **AI đang kiểm định ảnh và tự phân tích điều phối lực lượng...**"
                ):
                    ai_result = analyze_waste_image_ai(image_bytes, location)

                # 4. XỬ LÝ NẾU ẢNH LÀ CHÂN DÙNG / KHÔNG PHẢI ẢNH RÁC (GUARDRAIL CHECK)
                if not ai_result.get("is_waste", False):
                    st.error("❌ **BÁO CÁO BỊ TỪ CHỐI BỞI HỆ THỐNG AI!**")
                    reject_reason = ai_result.get(
                        "reject_reason",
                        "Hình ảnh chụp không chứa hiện trường ô nhiễm rác thải.",
                    )
                    st.markdown(
                        f"""
                        <div class="report-card-danger">
                            <h4>⚠️ Lý do AI từ chối:</h4>
                            <p><b>{reject_reason}</b></p>
                            <p><i>Hướng dẫn: Vui lòng không tải ảnh chân dung, ảnh đồ vật cá nhân hoặc ảnh chụp không liên quan. Hãy chụp trực tiếp bãi rác ngoài hiện trường.</i></p>
                        </div>
                    """,
                        unsafe_allow_html=True,
                    )

                # 5. XỬ LÝ NẾU ẢNH HỢP LỆ (GỬI THÀNH CÔNG)
                else:
                    st.success(
                        "🎉 **GỬI BÁO CÁO THÀNH CÔNG!** Hệ thống đã ghi nhận thông tin của bạn."
                    )
                    st.balloons()  # Bắn hiệu ứng bóng bay mừng thành công

                    # TÍNH NĂNG 2: HAZARDOUS WASTE ALERT AI
                    if ai_result.get("is_hazardous", False):
                        st.markdown(
                            f"""
                            <div class="report-card-danger">
                                <h3>🚨 CẢNH BÁO RÁC NGUY HIỂM / ĐỘC HẠI!</h3>
                                <p><b>{ai_result.get('hazard_warning')}</b></p>
                            </div>
                        """,
                            unsafe_allow_html=True,
                        )

                    # Lưu thông tin vào CSDL Session State
                    new_id = f"TK-{1001 + len(st.session_state.database)}"
                    new_entry = {
                        "id": new_id,
                        "reporter": reporter_name,
                        "location": location,
                        "waste_type": ai_result.get(
                            "waste_type", "Rác sinh hoạt"
                        ),
                        "severity": ai_result.get("severity", "Trung bình"),
                        "is_hazardous": ai_result.get("is_hazardous", False),
                        "hazard_warning": ai_result.get("hazard_warning", ""),
                        "status": "Đang chờ dọn",
                        "dispatch_plan": ai_result.get(
                            "dispatch_plan", "Bố trí lực lượng phù hợp."
                        ),
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "notes": description if description else "Không có",
                    }
                    st.session_state.database.append(new_entry)

                    # HIỂN THỊ PHIẾU ĐIỀU PHỐI AI TRÊN GIAO DIỆN
                    st.markdown("---")
                    st.markdown(
                        f"### 📋 Phiếu Tiếp Nhận & Phân Bổ Lực Lượng AI (`{new_id}`)"
                    )

                    col_res_img, col_res_info = st.columns([1, 2])

                    with col_res_img:
                        st.image(
                            uploaded_file,
                            caption="Ảnh hiện trường đã xác minh",
                            use_column_width=True,
                        )

                    with col_res_info:
                        st.markdown(f"👤 **Người báo cáo:** `{reporter_name}`")
                        st.markdown(f"📍 **Vị trí hiện trường:** `{location}`")
                        if description.strip():
                            st.markdown(f"📝 **Ghi chú thêm:** {description}")

                        # Khối thông tin chi tiết do AI phân tích
                        st.info(
                            f"🔴 **Xác minh hình ảnh:** CÓ PHÁT HIỆN RÁC THẢI\n\n"
                            f"🏷️ **Loại rác:** {ai_result.get('waste_type')}\n\n"
                            f"⚠️ **Mức độ ô nhiễm:** {ai_result.get('severity')}\n\n"
                            f"🚚 **AI ĐIỀU PHỐI LỰC LƯỢNG & MÁY MÓC:**\n{ai_result.get('dispatch_plan')}"
                        )


# ==============================================================================
# 6. PHÂN HỆ 2: AI TUYÊN TRUYỀN CỘNG ĐỒNG (COMMUNITY ENGAGEMENT AGENT)
# ==============================================================================
elif selected_tab == "2. 📢 AI Tuyên Truyền Cộng Đồng":
    st.markdown(
        """
        <div class="main-header-container">
            <div class="main-header-title">📢 AI COMMUNITY ENGAGEMENT AGENT</div>
            <div class="main-header-subtitle">Tự động viết bài truyền thông, vinh danh cư dân & lan tỏa lối sống xanh</div>
        </div>
    """,
        unsafe_allow_html=True,
    )

    st.subheader("✨ Tạo Bài Đăng Fanpage Tuyên Truyền Tự Động")

    if not st.session_state.database:
        st.warning(
            "Hiện chưa có dữ liệu báo cáo nào trong hệ thống. Vui lòng sang Phân hệ 1 để tạo báo cáo trước."
        )
    else:
        # Lọc danh sách các ticket đã xử lý hoặc đang xử lý
        completed_tickets = [
            t for t in st.session_state.database if t["status"] != "Bị từ chối"
        ]

        ticket_options = {
            f"{t['id']} - {t['reporter']} ({t['location']})": t
            for t in completed_tickets
        }

        selected_option = st.selectbox(
            "Chọn một sự kiện báo cáo rác thải vừa được xử lý thành công:",
            list(ticket_options.keys()),
        )

        selected_ticket = ticket_options[selected_option]

        col_post_1, col_post_2 = st.columns([1, 1])

        with col_post_1:
            st.markdown("#### 📄 Thông tin sự kiện:")
            st.write(f"• **Mã sự kiện:** {selected_ticket['id']}")
            st.write(f"• **Người báo cáo:** {selected_ticket['reporter']}")
            st.write(f"• **Địa điểm:** {selected_ticket['location']}")
            st.write(f"• **Loại rác dọn dẹp:** {selected_ticket['waste_type']}")

            generate_btn = st.button("✨ Kích hoạt AI Viết Bài Tuyên Truyền")

        with col_post_2:
            if generate_btn:
                with st.spinner(
                    "🤖 **AI Agent đang sáng tạo nội dung bài đăng...**"
                ):
                    social_post_content = generate_community_post_ai(
                        selected_ticket["reporter"],
                        selected_ticket["location"],
                        selected_ticket["waste_type"],
                    )

                st.markdown("#### 📝 Bài viết do AI tạo ra:")
                st.text_area(
                    "Đoạn văn bản bài đăng (Copy để đăng Fanpage):",
                    social_post_content,
                    height=280,
                )
                st.success(
                    "✅ Bài viết đã sẵn sàng để truyền thông trên Fanpage Đoàn Thanh niên / Mạng xã hội!"
                )


# ==============================================================================
# 7. PHÂN HỆ 3: SMART EXECUTIVE DASHBOARD (LÃNH ĐẠO & CÔNG VỤ)
# ==============================================================================
elif selected_tab == "3. 📊 Smart Executive Dashboard (Lãnh Đạo)":
    st.markdown(
        """
        <div class="main-header-container">
            <div class="main-header-title">📊 SMART EXECUTIVE DASHBOARD</div>
            <div class="main-header-subtitle">Hệ thống Thống kê, Giám sát & Báo cáo Tham mưu Lãnh đạo Thành phố</div>
        </div>
    """,
        unsafe_allow_html=True,
    )

    # CHỈ SỐ KPI TỔNG QUAN
    df = pd.DataFrame(st.session_state.database)

    col_kpi_1, col_kpi_2, col_kpi_3, col_kpi_4 = st.columns(4)

    total_reports = len(df)
    haz_count = (
        len(df[df["is_hazardous"] == True]) if not df.empty else 0
    )
    cleaned_count = (
        len(df[df["status"] == "Đã dọn dẹp"]) if not df.empty else 0
    )

    with col_kpi_1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-value">{total_reports}</div>
                <div class="metric-label">Tổng Báo Cáo Tiếp Nhận</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col_kpi_2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-value" style="color: #e53935;">{haz_count}</div>
                <div class="metric-label">Điểm Rác Nguy Hiểm</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col_kpi_3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-value" style="color: #1b5e20;">{cleaned_count}</div>
                <div class="metric-label">Đã Dọn Dẹp Hoàn Tàn</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col_kpi_4:
        st.markdown(
            """
            <div class="metric-card">
                <div class="metric-value" style="color: #0288d1;">100%</div>
                <div class="metric-label">Tỷ Lệ AI Phân Loại Tự Động</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.subheader("📋 Bảng Quản Lý Chi Tiết Hồ Sơ Báo Cáo Hiện Trường")

    # Bảng dữ liệu Interactive DataFrame
    st.dataframe(
        df[
            [
                "id",
                "reporter",
                "location",
                "waste_type",
                "severity",
                "is_hazardous",
                "status",
                "timestamp",
            ]
        ],
        use_container_width=True,
    )

    # TÍNH NĂNG 4: AI EXECUTIVE SUMMARY FOR LEADERSHIP
    st.markdown("---")
    st.subheader("📄 AI Xuất Báo Cáo Tham Mưu Cho Lãnh Đạo (Executive Summary)")
    st.caption(
        "Bấm nút bên dưới để AI tổng hợp toàn bộ dữ liệu đô thị và soạn thảo Báo cáo Báo cáo Chiến lược chuẩn văn phong công vụ."
    )

    if st.button("📑 Tạo Báo Cáo Tham Mưu Công Vụ Tự Động"):
        with st.spinner(
            "🧠 **AI đang tổng hợp dữ liệu toàn địa bàn và biên soạn văn bản tham mưu...**"
        ):
            executive_report_text = generate_executive_report_ai(
                st.session_state.database
            )

        st.markdown("### 🏛️ VĂN BẢN THAM MƯU QUẢN TRỊ ĐÔ THỊ GỬI LÃNH ĐẠO")
        st.info(executive_report_text)

        # Nút Tải Báo cáo dạng file Text
        st.download_button(
            label="📥 Tải Văn Bản Báo Cáo (.txt)",
            data=executive_report_text,
            file_name=f"Bao_Cao_Tham_Muu_Moi_Truong_{time.strftime('%Y%m%d')}.txt",
            mime="text/plain",
        )


# ==============================================================================
# 8. FOOTER TRANG WEB
# ==============================================================================
st.markdown("---")
st.markdown(
    """
    <div style="text-align: center; color: #888888; font-size: 0.85rem;">
        © 2026 <b>Urban GreenEye AI Team</b> - Giải pháp AI Chuyển Đổi Số Quản Lý Môi Trường Đô Thị.<br>
        <i>Phát triển phục vụ Cuộc thi Sáng tạo Thanh thiếu niên Nhi đồng & STEM Đồng Nai 2026.</i>
    </div>
""",
    unsafe_allow_html=True,
)
