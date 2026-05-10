import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont
import textwrap
import io
import qrcode  # 설치한 라이브러리 불러오기

# 1. 앱 설정
st.set_page_config(page_title="서정대학교 글로벌 AI 홍보 시스템", page_icon="🏫", layout="wide")

# 2. 사이드바 설정
with st.sidebar:
    st.header("⚙️ 시스템 설정")
    api_key = st.text_input("🔑 Google API Key", type="password")
    target_language = st.selectbox("출력 언어", ['한국어', 'English', 'Tiếng Việt', 'Монгол хэл', 'Oʻzbekcha'])
    
    st.divider()
    reference_url = st.text_input("🔗 관련 기사/보도자료 링크", placeholder="http://...")
    st.caption("기사 링크를 넣으면 포스터 우측 하단에 QR코드가 생성됩니다.")
    
    st.divider()
    univ_name = "서정대학교 (SEOJEONG UNIV.)"
    univ_slogan = "세계를 품는 글로벌 인재 양성"
    st.info(f"🏛️ 운영: {univ_name}")

# 3. 메인 UI
st.title("🏛️ SEOJEONG 글로벌 홍보 원스톱 시스템")
st.divider()

col1, col2 = st.columns(2)
with col1:
    event_name = st.text_input("📌 행사명", placeholder="예: 제7호 장영실학당 오픈")
    event_date = st.text_input("📅 일시")
with col2:
    event_location = st.text_input("📍 장소")

key_intent = st.text_area("💡 핵심 내용 (필수)", height=100)
uploaded_images = st.file_uploader("📸 사진 첨부 (최대 2장)", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

# 4. 실행 로직
if st.button(f"🚀 {target_language} 공식 포스터 생성", use_container_width=True):
    if not api_key: st.error("🔑 API Key를 입력하세요!"); st.stop()
    
    with st.spinner("🤖 AI가 기사를 분석하고 포스터를 디자인 중입니다..."):
        try:
            # [1단계] AI 텍스트 생성
            genai.configure(api_key=api_key)
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            model_name = next((m for m in available_models if '1.5-flash' in m), available_models[0])
            model = genai.GenerativeModel(model_name)
            
            ref_info = f"참고 링크: {reference_url}" if reference_url else ""
            prompt = f"""
            작성언어: {target_language}
            {ref_info}
            역할: 대학 홍보 전문가. 위 데이터를 바탕으로 '2문단' 이내의 정중한 기사를 작성하세요.
            데이터: 행사명({event_name}), 일시({event_date}), 장소({event_location}), 내용({key_intent})
            """
            response = model.generate_content(prompt)
            ai_text = response.text.replace("**", "").replace("#", "")

            # [2단계] 이미지 디자인 생성
            img_width = 800
            temp_canvas = Image.new('RGB', (img_width, 5000), color='white')
            draw = ImageDraw.Draw(temp_canvas)
            
            # 폰트 설정 (헤드라인 강화)
            try:
                # 윈도우 헤드라인 폰트 시도, 안되면 맑은고딕 굵게
                try: f_headline = ImageFont.truetype("C:/Windows/Fonts/Hymalp.ttf", 42)
                except: f_headline = ImageFont.truetype("malgunbd.ttf", 40)
                
                f_univ = ImageFont.truetype("malgun.ttf", 28)
                f_slogan = ImageFont.truetype("malgun.ttf", 16)
                f_content = ImageFont.truetype("malgun.ttf", 20)
                f_footer = ImageFont.truetype("malgun.ttf", 14)
            except: st.error("⚠️ 폰트 로딩 에러"); st.stop()

            # 헤더 디자인
            draw.rectangle([(0, 0), (img_width, 120)], fill="#FFFFFF")
            draw.line([(0, 120), (img_width, 120)], fill="#EEEEEE", width=1)
            
            try:
                logo_img = Image.open("logo.png")
                logo_h = 70
                logo_w = int(logo_img.width * (logo_h / logo_img.height))
                logo_img = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
                temp_canvas.paste(logo_img, (40, 25), logo_img if logo_img.mode == 'RGBA' else None)
                draw.text((40 + logo_w + 20, 30), "서정대학교", font=f_univ, fill="#0A1E3F")
                draw.text((40 + logo_w + 20, 75), "SEOJEONG UNIVERSITY", font=f_slogan, fill="#555555")
            except:
                draw.text((40, 30), univ_name, font=f_univ, fill="#0A1E3F")

            # 헤드라인 그리기
            draw.text((40, 170), f"📢 {event_name}", font=f_headline, fill="#000000")
            draw.line([(40, 235), (img_width-40, 235)], fill="#0A1E3F", width=3)
            
            curr_y = 270
            
            # 사진 배치 (최대 2장)
            if uploaded_images:
                p_width = (img_width - 90) // 2 if len(uploaded_images) >= 2 else (img_width - 80)
                for idx, img_file in enumerate(uploaded_images[:2]):
                    p = Image.open(img_file)
                    p_h = int(p.height * (p_width/p.width))
                    p = p.resize((p_width, p_h))
                    p_x = 40 if idx == 0 else 40 + p_width + 10
                    temp_canvas.paste(p, (p_x, curr_y))
                    if len(uploaded_images) == 1 or idx == 1: curr_y += p_h + 40

            # 본문 작성
            wrapped = ""
            text_w = 40 if target_language == '한국어' else 55
            for p_text in ai_text.split('\n'):
                if p_text.strip(): wrapped += textwrap.fill(p_text, width=text_w) + "\n\n"
            
            draw.text((60, curr_y), wrapped, font=f_content, fill="#333333", spacing=12)
            
            # 텍스트 끝 지점 측정
            bbox = draw.textbbox((60, curr_y), wrapped, font=f_content)
            end_y = bbox[3] + 50
            
            # [핵심] QR코드 생성 및 배치 로직
            if reference_url:
                # 네이비 컬러 QR 생성
                qr = qrcode.QRCode(version=1, box_size=3, border=2)
                qr.add_data(reference_url)
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="#0A1E3F", back_color="white").convert('RGB')
                
                # 우측 하단 배치
                qr_w, qr_h = qr_img.size
                temp_canvas.paste(qr_img, (img_width - qr_w - 60, end_y))
                draw.text((img_width - qr_w - 180, end_y + (qr_h//2) - 10), "기사 원문 보기 ▶", font=f_footer, fill="#555555")
                end_y += qr_h + 30 # QR코드 높이만큼 여백 추가
            
            # 하단 시그니처 & 크롭
            draw.rectangle([(0, end_y), (img_width, end_y+100)], fill="#F8F9FA")
            draw.line([(40, end_y), (img_width-40, end_y)], fill="#0A1E3F", width=3)
            draw.text((40, end_y+35), univ_slogan, font=f_content, fill="#0A1E3F")
            
            final_canvas = temp_canvas.crop((0, 0, img_width, end_y + 100))

            # [3단계] 출력
            st.success(f"✅ 서정대학교 공식 글로벌 포스터가 완성되었습니다!")
            buf = io.BytesIO()
            final_canvas.save(buf, format="PNG")
            st.image(buf.getvalue(), use_container_width=True)
            
            # 다운로드 및 링크 버튼 유지
            c1, c2 = st.columns(2)
            with c1: st.download_button("📥 이미지 다운로드", buf.getvalue(), f"Seojeong_News.png", "image/png", use_container_width=True)
            with c2: 
                if reference_url: st.link_button("📰 기사 원문 링크로 이동", reference_url, use_container_width=True)
                else: st.button("🔗 연결 링크 없음", disabled=True, use_container_width=True)

        except Exception as e: st.error(f"❌ 오류 발생: {e}")