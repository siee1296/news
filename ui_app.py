import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont
import textwrap
import io
import os
from datetime import datetime

# 1. 앱 설정
st.set_page_config(page_title="서정대학교 글로벌 뉴스레터 생성기", page_icon="🏫", layout="wide")

# 세션 상태(임시 서류함) 초기화
if 'generated_data' not in st.session_state:
    st.session_state['generated_data'] = None

# 2. 사이드바 설정
with st.sidebar:
    st.header("⚙️ 시스템 설정")
    raw_api_key = st.text_input("🔑 Google API Key", type="password")
    api_key = raw_api_key.strip() if raw_api_key else None
    target_language = st.selectbox("출력 언어", ['한국어', 'English', 'Tiếng Việt', 'М온골 хэл', 'Oʻzbekcha'])
    
    st.divider()
    target_url = st.text_input("🔗 연결할 웹사이트 링크", placeholder="https://...")
    
    st.divider()
    st.info("🏛️ 운영: 서정대학교 (SEOJEONG UNIV.)")

# 3. 메인 UI
st.title("🏫 SEOJEONG 글로벌 뉴스레터 생성기")
st.caption("AI가 한국어/영어 뉴스레터를 깨짐 없이 디자인해 드립니다.")
st.divider()

col1, col2 = st.columns(2)
with col1:
    event_name = st.text_input("📌 행사명 (한글 입력)")
    event_date = st.text_input("📅 일시 (본문에 녹여냅니다)")
with col2:
    event_location = st.text_input("📍 장소 (본문에 녹여냅니다)")

key_intent = st.text_area("💡 핵심 내용", height=150)
uploaded_images = st.file_uploader("📸 사진 첨부 (최대 2장)", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

# 4. 생성 버튼
if st.button(f"🚀 {target_language} 뉴스레터 생성", use_container_width=True):
    if not api_key: st.error("🔑 API Key를 입력하세요!"); st.stop()
    
    with st.spinner("📧 글자가 깨지지 않도록 글로벌 폰트를 점검하며 디자인 중입니다..."):
        try:
            # --- [AI 텍스트 생성 구역] ---
            genai.configure(api_key=api_key)
            
            # 404 에러 예방 로직 (유동적 모델 매칭)
            model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            selected_model = next((m for m in model_list if '1.5-flash' in m), model_list[0])
            model = genai.GenerativeModel(selected_model)
            
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            prompt = f"""
            작성언어: {target_language}
            역할: 대학 홍보팀 뉴스레터 에디터. 
            데이터: 행사명({event_name}), 일시({event_date}), 장소({event_location}), 내용({key_intent}), 관련링크({target_url})
            
            지시사항:
            1. 제목: 행사명을 간결하고 매력적인 {target_language} 뉴스레터 제목으로 번역.
            2. 본문: 친절한 뉴스레터 어투로 '2문단' 이내 작성. 일시/장소 정보를 문장 안에 자연스럽게 녹여내어 서술형으로 작성.
            3. 전달문구: SNS(카톡/이메일)로 이미지를 보낼 때 함께 적을 '3줄 요약 문구'를 {target_language}로 작성. 마지막에 링크({target_url})를 포함.
            4. 출력 형식: [제목]내용 [본문]내용 [전달문구]내용
            """
            
            response = model.generate_content(prompt)
            full_content = response.text.replace("**", "").replace("#", "")

            # 안전한 제목/본문/전달문구 분리 로직
            if "[전달문구]" in full_content:
                parts = full_content.split("[전달문구]")
                ai_share = parts[1].strip()
                title_body_part = parts[0]
            else:
                ai_share = f"👉 {target_url}"
                title_body_part = full_content
            
            if "[본문]" in title_body_part:
                ai_title = title_body_part.split("[본문]")[0].replace("[제목]", "").strip()
                ai_text = title_body_part.split("[본문]")[1].strip()
            else:
                ai_title = event_name
                ai_text = title_body_part

            # --- [이미지 디자인 생성 구역] ---
            img_width = 850
            temp_canvas = Image.new('RGB', (img_width, 5000), color='white')
            draw = ImageDraw.Draw(temp_canvas)
            
            # --- [핵심 수정: 무결점 폰트 로딩 로직] ---
            # 윈도우 폰트 폴더 경로를 명시적으로 정의합니다.
            font_folder = "C:/Windows/Fonts/"
            
            # 한국어와 영어를 모두 지원하는 대중적인 폰트 파일명을 정의합니다.
            font_headline_name = "malgunbd.ttf" # 헤드라인용 굵은 맑은 고딕
            font_content_name = "malgun.ttf" # 본문용 일반 맑은 고딕
            font_foreign_name = "arial.ttf" # 외국어 출력 시 가끔 필요한 Arial

            try:
                # [안전장치 1] os.path.join을 사용하여 경로를 더 정확하게 결합합니다.
                f_headline = ImageFont.truetype(os.path.join(font_folder, font_headline_name), 45)
                f_univ = ImageFont.truetype(os.path.join(font_folder, font_content_name), 28)
                f_content = ImageFont.truetype(os.path.join(font_folder, font_content_name), 22)
                f_date = ImageFont.truetype(os.path.join(font_folder, font_content_name), 16)
            except Exception as e_font_malgun:
                try:
                    # [안전장치 2] 만약 맑은 고딕 로드 실패 시, 더 흔한 Arial로 시도합니다.
                    f_headline = ImageFont.truetype(os.path.join(font_folder, font_foreign_name), 45)
                    f_univ = ImageFont.truetype(os.path.join(font_folder, font_foreign_name), 28)
                    f_content = ImageFont.truetype(os.path.join(font_folder, font_foreign_name), 22)
                    f_date = ImageFont.truetype(os.path.join(font_folder, font_foreign_name), 16)
                    st.warning("⚠️ '맑은 고딕' 폰트를 찾을 수 없어 'Arial' 폰트를 사용합니다. 일부 한국어가 깨질 수 있습니다.")
                except Exception as e_font_arial:
                    # [안전장치 3] 이마저도 실패 시, 시스템 기본 폰트로 폴백합니다. (글자 깨짐은 피할 수 없지만 실행은 됩니다.)
                    f_headline = ImageFont.load_default()
                    f_univ = ImageFont.load_default()
                    f_content = ImageFont.load_default()
                    f_date = ImageFont.load_default()
                    st.warning("⚠️ 시스템 폰트 로드 실패. 한국어 글자가 깨져서 나올 수 있습니다.")
            # ----------------------------------------

            # 디자인 시작
            draw.rectangle([(0, 0), (img_width, 130)], fill="#0A1E3F")
            try:
                logo_img = Image.open("logo.png")
                logo_h = 70
                logo_w = int(logo_img.width * (logo_h / logo_img.height))
                logo_img = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
                temp_canvas.paste(logo_img, (40, 30), logo_img if logo_img.mode == 'RGBA' else None)
                draw.text((40 + logo_w + 20, 50), "SEOJEONG UNIVERSITY", font=f_univ, fill="white")
            except:
                draw.text((40, 50), "SEOJEONG UNIVERSITY", font=f_univ, fill="white")
            
            draw.text((img_width - 180, 145), f"Issue Date: {today_str}", font=f_date, fill="#777777")
            
            curr_y = 190
            h_wrapped = textwrap.fill(ai_title, width=18 if target_language == '한국어' else 42)
            draw.text((40, curr_y), h_wrapped, font=f_headline, fill="#111111", spacing=12)
            
            curr_y = draw.textbbox((40, curr_y), h_wrapped, font=f_headline, spacing=12)[3] + 30
            draw.line([(40, curr_y), (150, curr_y)], fill="#0A1E3F", width=8)
            curr_y += 50

            if uploaded_images:
                p_width = (img_width - 100) // 2 if len(uploaded_images) >= 2 else (img_width - 80)
                for idx, img_file in enumerate(uploaded_images[:2]):
                    p = Image.open(img_file)
                    p_h = int(p.height * (p_width/p.width))
                    p = p.resize((p_width, p_h))
                    temp_canvas.paste(p, (40 if idx == 0 else 40 + p_width + 20, curr_y))
                    if len(uploaded_images) == 1 or idx == 1: curr_y += p_h + 40

            curr_y += 20
            paragraphs = [p for p in ai_text.split('\n') if p.strip()]
            wrapped_body = ""
            for p in paragraphs[:2]:
                wrapped_body += textwrap.fill(p, width=65) + "\n\n"
            
            draw.text((40, curr_y), wrapped_body, font=f_content, fill="#444444", spacing=15)
            
            b_bbox = draw.textbbox((40, curr_y), wrapped_body, font=f_content, spacing=15)
            final_canvas = temp_canvas.crop((0, 0, img_width, b_bbox[3] + 80))

            # --- [핵심: 결과물을 세션 서류함에 저장] ---
            buf = io.BytesIO()
            final_canvas.save(buf, format="PNG")
            
            st.session_state['generated_data'] = {
                'image': buf.getvalue(),
                'share_text': ai_share,
                'url': target_url
            }

        except Exception as e: st.error(f"❌ 오류: {e}")

# --- [화면 출력 구역] ---
if st.session_state['generated_data']:
    data = st.session_state['generated_data']
    
    st.success("✅ 불필요한 박스를 제거하고 폰트를 정상 적용했습니다!")
    st.divider()
    st.image(data['image'], use_container_width=True)
    
    st.subheader("📢 발송용 메시지 세트")
    st.text_area("📋 복사해서 사용하세요", data['share_text'], height=120)
    
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 이미지 다운로드", data['image'], "Newsletter.png", use_container_width=True)
    with c2:
        if data['url']: st.link_button("🌐 링크 바로가기", data['url'], use_container_width=True)