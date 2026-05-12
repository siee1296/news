"""
SEOJEONG 보도자료 아키텍트 V7.0
======================================================================
V6 → V7 주요 변경사항
1. [API]  Ollama → Google Gemini (1.5/2.5 Pro·Flash 선택)
2. [디자인] 머리말 구분선 제거, 뉴스레터 톤 레이아웃 (헤더/메타/푸터)
3. [사진]  최대 2장 첨부 (1장 풀너비 / 2장 좌우분할)
4. [DB]   브랜드 아이덴티티는 고정 상수로 분리, RAG는 '전개 기조'만
          - 프롬프트 명시 + 자동 마스킹 이중 안전망
5. [폰트] 다국어 글리프 폴백 + 글자 깨짐 사전 검증
6. [추가] F. 사실 자동 검증 (입력에 없는 수치 하이라이트)
          G. 발행 이력 자동 로깅 (history.jsonl)
          PNG + PDF 다운로드
======================================================================
※ 사전 준비
  - pip install google-generativeai streamlit pillow requests reportlab
  - retriever.py, seojeong_db_v2/ 가 같은 폴더에 있어야 함
  - 폰트 파일 (다음 중 가능한 것을 같은 폴더에 두기, 없으면 fallback):
      · NotoSansKR-Regular.otf / NotoSansKR-Bold.otf  (권장)
      · arial unicode ms.otf                          (기존 호환)
"""

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
import io, os, re, json
from datetime import datetime

# Gemini SDK
import google.generativeai as genai
from google.api_core import exceptions as gexc

# retriever.py 가 같은 폴더에 있어야 함
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from retriever import SeojeongRetriever
except Exception:
    SeojeongRetriever = None


# ============================================================
# 1. 경로 및 기본 설정
# ============================================================
CURRENT_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(CURRENT_DIR, "seojeong_db_v2")
HISTORY_PATH = os.path.join(CURRENT_DIR, "press_history.jsonl")

st.set_page_config(page_title="서정대 보도자료 아키텍트 V7", page_icon="🏫", layout="wide")


# ============================================================
# 2. [고정 레이어 A] 대학 아이덴티티 — 항상 프롬프트에 주입
#    이 정보는 절대 RAG가 아니라 코드에서만 가져옴
# ============================================================
BRAND_IDENTITY = """
[서정대학교 고정 브랜드 아이덴티티 - 반드시 이 정보만 사용]

# 공식 표현 (이 표현만 사용. RAG 참고자료의 변형 표현은 무시할 것)
- 공식 슬로건: "세상의 힘이 되다"
- 인재상: "글로컬 전문기술인재"
- 교육 철학: "현장 중심 실무 교육"
- 졸업생 역량: "즉시전력감"
- 포지셔닝: "K-직업교육", "전문직업교육 선도"
- 총장 호칭: "양영희 총장"

# 정량 자랑 포인트 (이 수치만 사용. 사용자 입력에 없는 수치는 만들지 말 것)
- 전국 전문대 재학생 수 1위 (9,043명, 2025.4.1 기준)
- 신입생 충원율 100% (개교 이래 유지)
- 재학생 충원률 291%
- 외국인 유학생 수 전국 1위 (4,000여 명)
- 국가시험 100% 합격 (간호사·응급구조사)
- 13년 연속 합격·취업 성과

# 정부 인증·사업 (이 명칭만 정확히 사용)
- 교육부 교육국제화역량 인증대학
- 법무부 외국인 요양보호사 양성대학
- 산업통상자원부 뿌리산업용접분야 양성대학
- 고용노동부 일학습병행 공동훈련센터 A등급
- 중소기업벤처부 글로벌 인재 취업 선도대학
- 경기도 RISE 사업
- 교육부 HiVE 사업 A등급
"""

# ============================================================
# 2-2. [기조 레이어 B] 보도자료 전개 방식 - DB 표현 학습용
# ============================================================
WRITING_STYLE_GUIDE = """
[보도자료 전개 기조 - 문장 호흡·구조만 적용]

# 공통 구조 원칙
- 리드: "서정대학교(총장 양영희)는 [날짜] [장소]에서 [무엇을] [했다고 밝혔다]" 형식
- 총장 멘트 구조: [성과 인식] + [학생·학과 칭찬] + [앞으로의 약속]
- 마무리: "앞으로도 ~하겠다" 형식의 향후 계획

# 톤·금기사항
- 정중하고 절제된 저널리즘 어조 (~했다, ~라고 밝혔다)
- 한자, 외국 문자 절대 금지
- 부정적 정보·경쟁 대학 비교·미확정 사실 금지
- 광고 어조 형용사("최고의", "유일한" 등) 객관 지표로 대체
- 학과·기관·인물은 정식 명칭 사용
- 마크다운 헤더(#, ##) 사용 금지
"""


# ============================================================
# 3. 분량 프로필
# ============================================================
LENGTH_PROFILES = {
    "단신": {
        "caption": "400~600자",
        "use_case": "행사 알림, 단순 합격 보고, 짧은 단신",
        "char_range": "400~600자",
        "detail_guide": """
[분량 프로필: 단신]
- 총 길이: 400~600자 (한국어 기준, 공백 포함)
- 단락 수: 2~3개
- 구조: 리드 1단락 → 핵심 사실 1단락 → 짧은 멘트 1단락
- 멘트: 총장 또는 책임자 발언 1문장으로 압축
- 정량 정보: 1~2개만 핵심으로
- 미사여구·중복 표현 최소화. 사실 위주로 빠르게 마무리.
""",
        "json_body_hint": "본문 400~600자, 단락 2~3개",
        "max_tokens": 2048,
    },
    "표준": {
        "caption": "900~1300자",
        "use_case": "MOU·협약, 행사, 수상, 일반 보도",
        "char_range": "900~1300자",
        "detail_guide": """
[분량 프로필: 표준]
- 총 길이: 900~1300자 (한국어 기준, 공백 포함)
- 단락 수: 4~5개
- 구조 (5단): 리드 1단락 → 사실 1~2단락 → 의미 1단락 → 총장 멘트 1단락 → 비전 1단락
- 총장 멘트: 2~3문장 (성과 인식 + 칭찬 + 약속 모두 포함)
- 정량 정보: 2~3개 자연스럽게 삽입
- 정부 인증·사업 명칭 1개 이상 언급
""",
        "json_body_hint": "본문 900~1300자, 단락 4~5개, 단락 간 \\n\\n 구분",
        "max_tokens": 4096,
    },
    "특집": {
        "caption": "1800~2800자",
        "use_case": "입시, 종합 홍보, 대형 사업 선정, 연간 성과",
        "char_range": "1800~2800자",
        "detail_guide": """
[분량 프로필: 특집]
- 총 길이: 1800~2800자 (한국어 기준, 공백 포함)
- 단락 수: 8~12개
- 구조: 도입부 리드 → 다음 측면들을 각각 1~2단락씩 다룰 것
  * (a) 핵심 성과 - 수치·순위·기록 강조
  * (b) 정부 인증·평가 - 교육부·법무부·고용부·중기벤처부 등 인증명 정확히
  * (c) 교육 시스템·특성화 학과 - 어떤 학과를, 어떤 방식으로
  * (d) 학생 지원·복지 - 장학금·기숙사·국제학생 지원 등 (해당 시)
  * (e) 지역사회·산학 연계 - 양주시·경기북부·RISE·HiVE 등
  * (f) 글로벌·미래 비전 - 외국인 유학생·해외 협력
- 각 측면 단락은 첫 문장이 주제문이 되도록 작성 (소제목 마크다운은 사용 금지)
- 총장 멘트는 종합 마무리 위치에 풍부하게 (3~5문장)
- 마지막은 향후 계획·신입생 모집·다음 단계 등 미래지향적 단락
- 정량 정보: 4~6개 풍부하게
- 정부 인증·사업: 2~3개 이상 언급
""",
        "json_body_hint": "본문 1800~2800자, 단락 8~12개, 단락 간 \\n\\n 구분. 마크다운 소제목(##)은 절대 사용 금지",
        "max_tokens": 8192,
    },
}


# ============================================================
# 4. 카테고리별 Few-shot 예시
# ============================================================
FEW_SHOT_EXAMPLES = {
    "입시, 모집": """
주제목: 서정대, 13년 연속 합격·취업 성과… 전문직업교육 선도
부제목: 신입생 충원율 100% 유지, 국가시험 합격률 100% 달성

본문: 서정대학교(총장 양영희)는 '세상의 힘이 되다'라는 슬로건 아래 13년 연속 국가시험 합격과 취업 성과를 이어가고 있다고 밝혔다.

서정대는 2025년 4월 1일 기준 9,043명의 재학생이 재학 중이며, 학령기·성인·국제학생으로 구성된 다양한 특성의 학생들을 위한 맞춤형 교육 기반을 갖춰 운영하고 있다. 개교 이래 신입생 충원율 100%를 이어오고 있으며, 2025년도 간호사 및 응급구조사 국가시험에서도 100% 합격률을 기록했다.

이는 서정대가 교육부 교육국제화역량 인증대학, 산업통상자원부 뿌리산업용접분야 양성대학, 고용노동부 일학습병행공동훈련센터 등 정부 부처로부터 검증받은 교육 인프라를 갖추고 있기에 가능한 성과로 평가된다.

양영희 총장은 "이러한 성과는 학생 개개인의 노력과 교직원의 헌신, 그리고 산업체와의 긴밀한 협력이 어우러진 결과"라며 "앞으로도 K-직업교육의 새로운 기준을 제시하며 학생들과 함께 성장해 나가겠다"고 밝혔다.
""",
    "학생": """
주제목: 서정대학교 호텔외식조리과, 2025 챌린지컵 국제요리경연대회 교육부장관상 수상
부제목: 27명 참가 금메달 18개·은메달 9개… 글로벌 조리 인재 양성 성과

본문: 서정대학교(총장 양영희)는 지난 5월 9일부터 10일까지 수원컨벤션센터에서 열린 '2025 대한민국챌린지컵 국제요리경연대회'에서 호텔외식조리과 학생 27명이 참가해 교육부장관상을 비롯한 금메달 18개와 은메달 9개를 수상했다고 밝혔다.

이번 대회는 마스터셰프 한국협회와 Turkiye TASFED 등이 공동 주최한 국제요리 경연대회로, 서정대 학생들은 전시요리부문에서 화려한 비주얼의 감각적인 요리를 선보였다. 특히 한국의 대표 식재료를 활용해 세계인의 입맛을 사로잡을 독창적인 조화로움과 뛰어난 플레이팅 감각을 인정받아 교육부장관상을 수상했다.

양영희 총장은 "이번 대회에서 우리 학생들이 뛰어난 성과를 거둔 것은 학생 개개인의 열정과 끊임없는 노력, 그리고 김호경 학과장과 이동욱 교수의 헌신적 지도가 어우러진 값진 결과"라며 "앞으로도 글로벌 무대에서 경쟁력 있는 조리 인재를 양성할 수 있도록 실무 중심의 교육과 다양한 국제교류 프로그램을 지속적으로 확대해 나가겠다"고 말했다.
""",
    "교육, 행정": """
주제목: 서정대, 경기도 최초 '고교-대학 학점인정 프로그램' 완료
부제목: 고등학생이 대학 학점 미리 취득… 교육 단절 해소 새 모델 제시

본문: 서정대학교(총장 양영희)는 경기도 최초로 추진된 '고교-대학 학점인정 프로그램'을 성공적으로 완료했다고 밝혔다.

이번 프로그램은 고등학생이 대학 강의를 미리 수강하고 학점을 취득한 뒤, 향후 서정대 입학 시 정규 학점으로 인정받을 수 있도록 한 제도다. 고등교육과 대학교육 간의 단절을 해소하고 직업교육의 연속성을 확보하는 새로운 교육 모델로 평가받고 있다.

서정대는 현장 중심 실무 교육을 토대로 학생들이 진로를 조기에 설계하고, 대학 진학 후 즉시 전공 학습에 몰입할 수 있도록 지원하고 있다. 이번 프로그램에는 인근 고등학교 학생들이 참여해 반려동물, 호텔외식조리, 간호 등 서정대의 특성화 학과 강의를 수강했다.

양영희 총장은 "이번 프로그램은 고등학생들이 자신의 적성과 진로를 미리 탐색하고, 글로컬 전문기술인재로 성장할 수 있는 기반을 마련한 의미 있는 시도"라며 "앞으로도 지역 교육 생태계와 함께 성장하는 대학으로서 직업교육의 새로운 기준을 제시해 나가겠다"고 말했다.
""",
    "지역사회, 산학협력": """
주제목: 서정대-웅진씽크빅, AI 교육 협력 확대 위한 MOU 체결
부제목: AI 기반 교육과정 공동 개발… 미래형 직업교육 가속화

본문: 서정대학교(총장 양영희)는 웅진씽크빅과 인공지능(AI) 교육 협력 확대를 위한 업무협약(MOU)을 체결했다고 밝혔다.

이번 협약은 AI 기반 교육과정 공동 개발, 학생 실습 및 인턴십 연계, 산학협력 프로젝트 운영 등을 주요 골자로 한다. 양 기관은 빠르게 변화하는 산업 환경에 대응할 수 있는 실무형 인재 양성을 위해 긴밀히 협력할 계획이다.

서정대는 현장 중심 실무 교육을 통해 졸업생이 즉시전력감으로 산업 현장에 투입될 수 있도록 지원하고 있으며, 이번 협약을 통해 AI 시대에 적합한 교육 콘텐츠와 실습 환경을 한층 강화할 수 있을 것으로 기대된다.

양영희 총장은 "이번 협약은 산업체와 대학이 협력해 미래형 직업교육을 함께 설계하는 의미 있는 첫걸음"이라며 "앞으로도 산학협력을 통해 학생들에게 실질적인 성장 기회를 제공하고, K-직업교육의 새로운 기준을 만들어 나가겠다"고 밝혔다.
""",
    "국제, 글로벌": """
주제목: 서정대, 제4회 총장배 국제학생 용접기능대회 성료
부제목: 베트남 유학생 대상 수상… 뿌리산업 글로벌 인재 양성 가시화

본문: 서정대학교(총장 양영희)는 20일 '제4회 서정대학교총장배 국제학생 용접기능대회'를 성황리에 개최했다고 밝혔다.

이번 대회는 대한민국 뿌리산업의 미래를 짊어질 외국인 유학생들의 기술 역량을 점검하고 격려하기 위해 마련됐다. 글로벌산업공학과 뿌리기술반 재학생들이 참가해 그동안 갈고닦은 용접 기술을 발휘했으며, 산업 현장의 실무 능력을 정확히 평가하기 위해 NCS(국가직무능력표준) 기준을 엄격히 적용해 진행됐다. 심사 결과 베트남 유학생 트린 반 바이 학생이 대상을 차지하는 등 총 9명이 수상했다.

서정대는 전국 전문대학 중 가장 많은 4,000여 명의 외국인 유학생이 재학 중이며, 교육부 교육국제화역량 인증대학으로 선정되는 등 글로벌 교육 인프라를 갖추고 있다. 졸업생은 숙련기능인력(E-7-4) 비자를 취득하고 국내 유수 기업에 취업하는 성과를 내고 있다.

양영희 총장은 "오늘 우리 학생들이 보여준 열정은 단순한 기술 연마를 넘어, 낯선 땅에서 자신의 꿈을 실현해 나가는 아름다운 도전"이라며 "앞으로도 국가 뿌리산업 발전과 지역사회 활성화에 이바지할 수 있도록 글로벌 인재 양성의 요람 역할을 다하겠다"고 강조했다.
""",
    "대학 성과": """
주제목: 서정대학교, HiVE사업 종합평가 최우수 'A등급' 획득
부제목: 3개년 사업 성과 인정… 지역 기반 고등직업교육 모델 제시

본문: 서정대학교(총장 양영희)는 고등직업교육거점지구(HiVE 1유형) 사업의 3개년(2022~2024) 종합평가에서 최우수 등급인 'A등급'을 받았다고 밝혔다.

이번 성과는 서정대가 지역사회와의 협력을 통해 고등직업교육의 새로운 모델을 제시하며 지역 발전에 기여한 점을 인정받은 결과다. HiVE 사업은 교육부와 한국연구재단이 주관하며, 전문대학과 기초자치단체가 협력해 지역 특화 분야를 선정하고 교육과정과 연계 운영하는 지역 기반형 고등직업교육 거점 조성 사업이다.

서정대는 양주시 및 연천군과 컨소시엄을 구성해 반려동물, 휴먼케어서비스, 그린식품가공 분야를 지역 특화 분야로 선정하고 지역 인재 양성과 정주 여건 강화를 추진해 왔다. 특히 산·학·관·민·정 협력 체계를 기반으로 한 SJ HiVE 이슈&진단 정책포럼을 통해 정책 과제를 도출하고 조례 제·개정까지 연계한 패스트트랙 구조를 마련한 점에서 높은 평가를 받았다.

양영희 총장은 "이번 A등급 성과는 대학과 지자체가 함께 지역 현안에 대응하고 특화 분야 관련 실무 중심 인재 양성에 힘쓴 결과"라며 "앞으로도 HiVE 사업의 주요 성과가 경기도 RISE 계획의 프로젝트 및 단위과제로 연계돼 발전할 수 있도록 최선을 다하겠다"고 말했다.
""",
}


# ============================================================
# 5. Retriever 초기화 (캐시)
# ============================================================
@st.cache_resource
def get_retriever():
    if SeojeongRetriever is None:
        return None
    if os.path.exists(DB_PATH):
        try:
            return SeojeongRetriever(db_path=DB_PATH)
        except Exception as e:
            st.warning(f"Retriever 초기화 실패: {e}\n검색 기능 없이 실행됩니다.")
    return None

retriever = get_retriever()


# ============================================================
# 6. 폰트 로딩 — 다국어 폴백
# ============================================================
@st.cache_resource
def load_fonts():
    """언어별로 글리프 커버리지가 좋은 폰트 우선순위로 로드.
    1순위: 사용자가 폴더에 둔 NotoSansKR / NotoSans / arial unicode
    2순위: 리눅스 시스템에 설치된 Noto CJK
    3순위: 기본 PIL 폰트 (최후)"""
    candidates_regular = [
        # 같은 폴더 우선
        os.path.join(CURRENT_DIR, "NotoSansKR-Regular.otf"),
        os.path.join(CURRENT_DIR, "NotoSansKR-Regular.ttf"),
        os.path.join(CURRENT_DIR, "NotoSans-Regular.ttf"),
        os.path.join(CURRENT_DIR, "arial unicode ms.otf"),
        # 시스템 폰트
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        # macOS
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        # Windows
        "C:/Windows/Fonts/malgun.ttf",
    ]
    candidates_bold = [
        os.path.join(CURRENT_DIR, "NotoSansKR-Bold.otf"),
        os.path.join(CURRENT_DIR, "NotoSansKR-Bold.ttf"),
        os.path.join(CURRENT_DIR, "NotoSans-Bold.ttf"),
        os.path.join(CURRENT_DIR, "arial unicode ms.otf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "C:/Windows/Fonts/malgunbd.ttf",
    ]
    def first_exists(paths):
        for p in paths:
            if os.path.exists(p):
                return p
        return None
    return first_exists(candidates_regular), first_exists(candidates_bold)


REG_FONT_PATH, BOLD_FONT_PATH = load_fonts()


def get_font(size, bold=False):
    """폰트 객체 반환 (fallback 포함)."""
    path = BOLD_FONT_PATH if bold and BOLD_FONT_PATH else REG_FONT_PATH
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def check_glyph_coverage(text, font):
    """폰트가 텍스트의 모든 문자를 그릴 수 있는지 검증. 누락 문자 리스트 반환."""
    missing = set()
    for ch in text:
        if ch.isspace() or ch in "\n\r\t":
            continue
        try:
            bbox = font.getbbox(ch)
            # 글리프가 없으면 폭이 0이거나 .notdef 글리프가 반환됨
            if bbox is None or (bbox[2] - bbox[0]) == 0:
                missing.add(ch)
        except Exception:
            missing.add(ch)
    return missing


# ============================================================
# 7. 텍스트 처리 헬퍼
# ============================================================
def clean_text_final(text, lang="Korean"):
    """마크다운 제거. 한자는 한국어 출력일 때만 제거."""
    if not text:
        return ""
    text = re.sub(r'\*\*|__', '', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    if lang == "Korean":
        # 한국어 보도자료에선 한자 금지
        text = re.sub(r'[\u4e00-\u9fff]+', '', text)
    return text.strip()


def _is_cjk_char(ch):
    """CJK 한자/한글/히라가나·가타가나 여부."""
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF or   # CJK 통합한자
        0x3400 <= cp <= 0x4DBF or   # CJK 확장 A
        0xAC00 <= cp <= 0xD7AF or   # 한글
        0x3040 <= cp <= 0x309F or   # 히라가나
        0x30A0 <= cp <= 0x30FF      # 가타가나
    )


def wrap_text(text, font, max_width):
    """언어별 줄바꿈:
    - CJK 문자 비중이 30% 이상이면 문자 단위 줄바꿈 (한국어·일본어·중국어)
    - 그 외(영어·베트남어·우즈벡·몽골)는 단어 단위 줄바꿈"""
    lines = []
    paragraphs = text.split('\n')
    for p in paragraphs:
        if not p.strip():
            lines.append("")
            continue

        # CJK 비중 측정
        non_space = [c for c in p if not c.isspace()]
        cjk_ratio = (sum(1 for c in non_space if _is_cjk_char(c)) / len(non_space)) if non_space else 0

        if cjk_ratio >= 0.3:
            # 문자 단위 줄바꿈 (CJK)
            line = ""
            for char in list(p):
                if font.getbbox(line + char)[2] <= max_width:
                    line += char
                else:
                    lines.append(line)
                    line = char
            if line:
                lines.append(line)
        else:
            # 단어 단위 줄바꿈 (라틴 계열)
            words = p.split(' ')
            line = ""
            for word in words:
                trial = (line + " " + word) if line else word
                if font.getbbox(trial)[2] <= max_width:
                    line = trial
                else:
                    # 한 단어가 너무 길면 강제로 잘라야 함
                    if not line:
                        # 단어를 글자 단위로 쪼개기
                        sub = ""
                        for ch in word:
                            if font.getbbox(sub + ch)[2] <= max_width:
                                sub += ch
                            else:
                                lines.append(sub)
                                sub = ch
                        line = sub
                    else:
                        lines.append(line)
                        line = word
            if line:
                lines.append(line)
    return lines


# ============================================================
# 8. RAG context 자동 마스킹 (DB 분리 #2)
# ============================================================
# 마스킹할 패턴: 슬로건/인재상/정량/인증명 등
MASK_PATTERNS = [
    # 슬로건·인재상·핵심 표현
    (r'세상의\s*힘이\s*되다',           '[브랜드슬로건]'),
    (r'글로컬\s*전문기술인재',          '[브랜드인재상]'),
    (r'현장\s*중심\s*실무\s*교육',      '[브랜드철학]'),
    (r'즉시전력감',                     '[브랜드역량]'),
    (r'K-?\s*직업교육',                 '[브랜드포지셔닝]'),
    (r'전문직업교육\s*선도',            '[브랜드포지셔닝]'),
    # 정량 지표
    (r'\d{1,3}(,\d{3})+\s*명',          '[정량_재학생수]'),
    (r'\d{1,3}\s*%\s*(합격|충원)',      '[정량_비율]'),
    (r'\d+\s*년\s*연속',                '[정량_연속성과]'),
    (r'\d{1,2},\d{3}\s*명',             '[정량_인원]'),
    (r'4,000여?\s*명',                  '[정량_유학생수]'),
    # 정부 인증·사업
    (r'교육부\s*교육국제화역량\s*인증대학', '[인증명]'),
    (r'법무부\s*외국인\s*요양보호사\s*양성대학', '[인증명]'),
    (r'산업통상자원부\s*뿌리산업용접분야\s*양성대학', '[인증명]'),
    (r'고용노동부\s*일학습병행', '[인증명]'),
    (r'중소기업벤처부\s*글로벌\s*인재\s*취업\s*선도대학', '[인증명]'),
    (r'경기도\s*RISE\s*사업', '[인증명]'),
    (r'(교육부\s*)?HiVE\s*(사업)?\s*A?등급?', '[인증명]'),
]

def mask_rag_context(text):
    """RAG 참고 자료에서 브랜드 표현·정량·인증명을 마스킹.
    모델이 '표현·문장 흐름·구조'만 학습하고, 사실 정보는 BRAND_IDENTITY와
    사용자 입력에서만 가져오도록 유도."""
    if not text:
        return text
    for pat, rep in MASK_PATTERNS:
        text = re.sub(pat, rep, text)
    return text


# ============================================================
# 9. Gemini 호출
# ============================================================
# 자동 시도할 모델 우선순위 (위에서부터 시도, 실패 시 다음 모델)
AUTO_MODEL_FALLBACK = [
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

def call_gemini(prompt, api_key, temperature=0.5,
                max_output_tokens=2048, force_json=True):
    """Gemini API 자동 호출. 모델 폴백 + 빈 응답 재시도."""
    genai.configure(api_key=api_key)

    last_error = None
    for model_name in AUTO_MODEL_FALLBACK:
        for use_json_mime in ([True, False] if force_json else [False]):
            generation_config = {
                "temperature": temperature,
                "top_p": 0.9,
                "max_output_tokens": max_output_tokens,
            }
            if use_json_mime:
                generation_config["response_mime_type"] = "application/json"
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config=generation_config,
                )
                resp = model.generate_content(prompt)
                text = resp.text.strip() if resp.text else ""
                if text:
                    return text, model_name
                # 빈 응답 → JSON mime 없이 재시도
                last_error = f"{model_name}: 빈 응답"
                continue
            except gexc.NotFound:
                last_error = f"{model_name}: 모델 없음(404)"
                break   # 이 모델은 없으니 다음 모델로
            except Exception as e:
                last_error = f"{model_name}: {e}"
                break

    raise RuntimeError(f"모든 모델 시도 실패. 마지막 오류: {last_error}")


def parse_press_json(raw, lang="Korean"):
    def strip_fences(text):
        """Gemini가 ```json ... ``` 마크다운으로 감싸서 반환할 때 제거."""
        text = text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)
        return text.strip()

    def fix_newlines(text):
        """\\n\\n → 실제 줄바꿈으로 변환."""
        if not text:
            return text
        text = text.replace('\\n\\n', '\n\n').replace('\\n', '\n')
        return text

    cleaned = strip_fences(raw)
    try:
        data = json.loads(cleaned)
        return {
            "title":    clean_text_final(data.get("title", ""), lang),
            "subtitle": clean_text_final(data.get("subtitle", ""), lang),
            "body":     clean_text_final(fix_newlines(data.get("body", "")), lang),
        }
    except json.JSONDecodeError:
        # 마지막 수단: 줄 단위 fallback
        lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
        # 혹시 JSON 형태 키-값이 남아있으면 제거
        def strip_json_key(line):
            m = re.match(r'^"(title|subtitle|body)"\s*:\s*"?(.*?)"?,?\s*$', line)
            return m.group(2) if m else line
        lines = [strip_json_key(l) for l in lines if l not in ('{', '}')]
        return {
            "title":    clean_text_final(lines[0], lang) if lines else "",
            "subtitle": clean_text_final(lines[1], lang) if len(lines) > 1 else "",
            "body":     fix_newlines("\n\n".join(clean_text_final(l, lang) for l in lines[2:])),
        }


# ============================================================
# 10. 프롬프트 빌더 — 레이어 A/B 명시 분리
# ============================================================
def build_generation_prompt(category, length_mode, event_name, event_date,
                             event_location, key_intent, context, lang):
    example = FEW_SHOT_EXAMPLES.get(category, FEW_SHOT_EXAMPLES["학생"])
    profile = LENGTH_PROFILES[length_mode]

    # context가 있으면 마스킹 적용
    masked_context = mask_rag_context(context) if context else ""

    return f"""당신은 서정대학교 홍보실의 수석 에디터입니다.
다음 두 종류의 정보를 명확히 구분하여 사용하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【레이어 A: 고정 사실 — 이 정보만 사용. 절대 변경·창작 금지】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{BRAND_IDENTITY}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【레이어 B: 전개 기조 — 문장 흐름·톤만 참고】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{WRITING_STYLE_GUIDE}

{profile['detail_guide']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【모범 예시 — 같은 카테고리. 톤·문장 호흡만 참고, 사실 복사 금지】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
카테고리: {category}
{example}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【과거 보도자료 참고 — 매우 중요한 사용 규칙】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
아래 자료는 '문장 호흡·전개 방식·단락 구성'만 학습하기 위한 것입니다.
- ⛔ 자료의 수치, 사업명, 날짜, 학과명 등 사실 정보를 그대로 복사하지 마세요.
- ⛔ [브랜드슬로건], [정량_*], [인증명] 등으로 마스킹된 부분은 표현 예시일 뿐,
     실제 보도자료에는 레이어 A의 정확한 표현만 사용하세요.
- ✅ 문장의 리듬, 단락 전환 방식, 인용 구조만 학습하세요.

{masked_context if masked_context else "(참고 데이터 없음)"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【이번 보도자료 작성 요청】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 카테고리: {category}
- 분량 프로필: {length_mode} ({profile['char_range']})
- 핵심 키워드: {event_name}
- 시점: {event_date}
- 장소: {event_location}
- 상세 사실관계: {key_intent}
- 출력 언어: {lang}

⚠️ 사실 정보의 출처 우선순위:
   1순위: 위 '상세 사실관계' 입력
   2순위: 레이어 A 고정 아이덴티티 (슬로건·인재상·정량 등)
   ⛔ 그 외의 수치·사업명·인물명·날짜는 절대 만들지 말 것

[출력 형식 — 반드시 JSON만. 다른 텍스트 일절 금지]
{{
  "title": "주제목 (수치·따옴표 활용 강렬한 한 줄)",
  "subtitle": "부제목 (주제목 보강 1~2줄, 40~60자)",
  "body": "{profile['json_body_hint']}"
}}
"""


def build_critique_prompt(draft, category, length_mode):
    profile = LENGTH_PROFILES[length_mode]
    return f"""당신은 서정대학교 홍보실의 수석 에디터입니다. 초안을 검토하고 개선판을 작성하세요.

{BRAND_IDENTITY}

{WRITING_STYLE_GUIDE}

{profile['detail_guide']}

[검토할 초안]
주제목: {draft['title']}
부제목: {draft['subtitle']}
본문:
{draft['body']}

[검토 체크리스트]
1. 분량이 {profile['char_range']} 범위 안에 있는가? (부족하면 보강, 넘으면 압축)
2. 첫 문장이 "서정대학교(총장 양영희)는 [날짜] [장소]에서 ~했다고 밝혔다" 형식인가?
3. 양영희 총장 멘트가 있고 [성과 인식 + 칭찬 + 약속] 구조인가?
4. 레이어 A의 핵심 표현(슬로건·인재상 등)이 자연스럽게 들어갔는가?
5. 정량 정보가 분량 프로필에 맞게 충분한가?
6. 마지막이 "앞으로도 ~하겠다" 형식의 비전 문장인가?
7. 한자, 부정적 표현, 미확정 사실, 경쟁 대학 비교, 마크다운 헤더(##)가 없는가?
8. 카테고리({category}) 메시지 프레임에 맞는가?
9. 특집인 경우 6개 측면(성과/인증/시스템/지원/지역/글로벌)이 골고루 다뤄졌는가?

개선판을 JSON으로만 출력하세요.
{{
  "title": "...",
  "subtitle": "...",
  "body": "..."
}}
"""


# ============================================================
# 11. 사실 자동 검증 (추가기능 F)
# ============================================================
def fact_verification(body, key_intent, event_date="", event_name="",
                      brand_identity=BRAND_IDENTITY):
    """본문에 등장하는 수치·기관명·연도가 입력 또는 브랜드 상수에 존재하는지 검증.
    의심스러운 항목 리스트 반환."""
    suspicious = []
    allowed_sources = "\n".join([
        key_intent or "",
        event_date or "",
        event_name or "",
        brand_identity,
    ])

    # (a) 본문에서 숫자가 포함된 표현 추출 (예: 9,043명, 100%, 13년, 4,000여 명, 2025년 등)
    number_patterns = [
        r'\d{1,3}(?:,\d{3})+\s*명',     # 9,043명
        r'\d{1,3}\s*%',                 # 100%
        r'\d+\s*년\s*연속',             # 13년 연속
        r'\d+\s*여?\s*명',              # 27명 / 4000여 명
        r'\d{4}\s*년',                  # 2025년
        r'\d+\s*개월?',                 # 3개월
        r'\d+\s*위',                    # 1위
        r'\d+\s*억\s*원?',              # 100억
    ]
    found = set()
    for pat in number_patterns:
        for m in re.findall(pat, body):
            found.add(m.strip())

    # 정규화 후 출처에 있는지 확인
    for item in found:
        # 공백 제거 비교
        norm_item = re.sub(r'\s+', '', item)
        norm_source = re.sub(r'\s+', '', allowed_sources)
        if norm_item not in norm_source:
            suspicious.append(("수치", item))

    # (b) 인증·사업 표현 검증 (대학명 + 부처명 + 사업명)
    cert_pattern = r'(교육부|법무부|산업통상자원부|고용노동부|중소기업벤처부|경기도)\s*[^,.。\s]{2,30}(인증대학|양성대학|훈련센터|선도대학|사업|등급)'
    for m in re.finditer(cert_pattern, body):
        full = m.group(0).strip()
        norm_full = re.sub(r'\s+', '', full)
        norm_source = re.sub(r'\s+', '', allowed_sources)
        if norm_full not in norm_source:
            suspicious.append(("기관·사업명", full))

    return suspicious


# ============================================================
# 12. 발행 이력 로깅 (추가기능 G)
# ============================================================
def log_history(record):
    """JSONL 포맷으로 누적 기록."""
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        st.warning(f"로깅 실패(무시 가능): {e}")


def load_history(n=20):
    """최근 n건 이력 로드."""
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        records = [json.loads(l) for l in lines if l.strip()]
        return records[-n:][::-1]   # 최신순
    except Exception:
        return []


# ============================================================
# 13. PDF 생성 (PIL 이미지를 PDF로 저장)
# ============================================================
def image_to_pdf_bytes(pil_image):
    buf = io.BytesIO()
    # PIL은 RGB 모드일 때 PDF 저장이 가장 안정적
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    pil_image.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


# ============================================================
# 14. 이미지 렌더링 — 뉴스레터 디자인
# ============================================================
def render_newsletter(final, lang, category, length_mode,
                      event_date, uploaded_images, dept_name="홍보실"):
    """뉴스레터 톤의 보도자료 PNG 생성."""
    W = 900
    # 분량별 초기 캔버스 높이 (충분히 크게 — 마지막에 crop)
    H_MAP = {"단신": 6000, "표준": 10000, "특집": 25000}
    H = H_MAP.get(length_mode, 25000)
    canvas = Image.new('RGB', (W, H), 'white')
    draw   = ImageDraw.Draw(canvas)

    # 폰트
    f_brand    = get_font(22, bold=True)
    f_meta     = get_font(15)
    f_title    = get_font(40, bold=True)
    f_subtitle = get_font(22)
    f_body     = get_font(20)
    f_footer   = get_font(14)

    MARGIN_X = 60
    BODY_W = W - 2 * MARGIN_X

    # ─── 헤더 ───
    HEADER_H = 110
    # 로고
    logo_path = os.path.join(CURRENT_DIR, "logo.png")
    logo_x = MARGIN_X
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA").resize(
                (60, 60), Image.Resampling.LANCZOS)
            canvas.paste(logo, (logo_x, 30), logo)
            text_x = logo_x + 75
        except Exception:
            text_x = logo_x
    else:
        text_x = logo_x

    draw.text((text_x, 38),
              "SEOJEONG UNIVERSITY",
              font=f_brand, fill="#0A1E3F")
    draw.text((text_x, 65),
              "서정대학교 보도자료  |  Press Release",
              font=f_meta, fill="#666666")

    # 헤더 하단 구분선 (얇게)
    draw.line([(MARGIN_X, HEADER_H + 10), (W - MARGIN_X, HEADER_H + 10)],
              fill="#0A1E3F", width=2)

    curr_y = HEADER_H + 30

    # ─── 제목 ───
    for line in wrap_text(final['title'], f_title, BODY_W):
        draw.text((MARGIN_X, curr_y), line, font=f_title, fill="#111111")
        curr_y += 54
    curr_y += 10

    # ─── 부제목 (접두사 없음, 깔끔하게) ───
    for line in wrap_text(final['subtitle'], f_subtitle, BODY_W):
        draw.text((MARGIN_X, curr_y), line, font=f_subtitle, fill="#555555")
        curr_y += 32
    curr_y += 35

    # ─── 사진 (최대 2장) ───
    images_to_render = []
    for img_file in (uploaded_images or [])[:2]:
        try:
            images_to_render.append(Image.open(img_file).convert("RGB"))
        except Exception as e:
            st.warning(f"이미지 로드 실패: {e}")

    if len(images_to_render) == 1:
        img = images_to_render[0]
        new_w = BODY_W
        new_h = int(img.height * (new_w / img.width))
        img_r = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        canvas.paste(img_r, (MARGIN_X, curr_y))
        curr_y += new_h + 35

    elif len(images_to_render) == 2:
        gap = 15
        each_w = (BODY_W - gap) // 2
        # 두 이미지를 같은 높이로 맞춰서 가로로 배치
        target_h = min(
            int(images_to_render[0].height * (each_w / images_to_render[0].width)),
            int(images_to_render[1].height * (each_w / images_to_render[1].width)),
        )
        for i, img in enumerate(images_to_render):
            new_w = each_w
            new_h = int(img.height * (new_w / img.width))
            img_r = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            # 높이 맞춤 (위쪽 정렬, 차이는 흰 배경)
            paste_x = MARGIN_X + i * (each_w + gap)
            canvas.paste(img_r, (paste_x, curr_y))
        curr_y += target_h + 35

    # ─── 본문 ───
    paragraphs = final['body'].split('\n\n')
    para_list = [p.strip() for p in paragraphs if p.strip()]
    for idx, para in enumerate(para_list):
        for line in wrap_text(para, f_body, BODY_W):
            # 캔버스 초과 시 자동 확장
            if curr_y + 40 > canvas.height:
                extra = Image.new('RGB', (W, 5000), 'white')
                new_canvas = Image.new('RGB', (W, canvas.height + 5000), 'white')
                new_canvas.paste(canvas, (0, 0))
                canvas = new_canvas
                draw = ImageDraw.Draw(canvas)
            draw.text((MARGIN_X, curr_y), line, font=f_body, fill="#2C2C2C")
            curr_y += 36
        if idx < len(para_list) - 1:
            curr_y += 10
            draw.line([(MARGIN_X, curr_y), (MARGIN_X + 40, curr_y)],
                      fill="#CCCCCC", width=1)
            curr_y += 20

    # ─── 푸터 ───
    curr_y += 30
    draw.line([(MARGIN_X, curr_y), (W - MARGIN_X, curr_y)],
              fill="#CCCCCC", width=1)
    curr_y += 15
    footer_text = f"작성부서  {dept_name}    |    www.seojeong.ac.kr    |    발행일 {event_date}"
    draw.text((MARGIN_X, curr_y), footer_text, font=f_footer, fill="#999999")
    curr_y += 40

    # ─── 최종 crop ───
    return canvas.crop((0, 0, W, curr_y))


# ============================================================
# 15. 사이드바
# ============================================================
with st.sidebar:
    st.header("🔑 Gemini API 설정")

    api_key_input = st.text_input(
        "Gemini API Key",
        type="password",
        value=st.session_state.get("gemini_api_key", ""),
        help="Google AI Studio에서 발급한 API 키. 세션에만 보관됩니다.",
        placeholder="AIza...",
    )
    if api_key_input:
        st.session_state["gemini_api_key"] = api_key_input

    st.divider()
    st.header("🌍 글로벌/보도 설정")

    lang_map = {
        '한국어':       'Korean',
        'English':      'English',
        'Tiếng Việt':   'Vietnamese',
        'Монгол хэл':   'Mongolian',
        'Oʻzbekcha':    'Uzbek',
    }
    selected_lang = st.selectbox("출력 언어", list(lang_map.keys()))

    st.divider()
    category = st.selectbox("카테고리 분류", list(FEW_SHOT_EXAMPLES.keys()))

    st.divider()
    st.subheader("📏 분량 프로필")
    length_mode = st.radio(
        "분량",
        options=list(LENGTH_PROFILES.keys()),
        index=1,
        horizontal=True,
        captions=[LENGTH_PROFILES[k]["caption"] for k in LENGTH_PROFILES.keys()],
        label_visibility="collapsed",
    )
    st.caption(f"용도: {LENGTH_PROFILES[length_mode]['use_case']}")

    st.divider()
    st.subheader("⚙️ 품질 옵션")
    use_2pass = st.toggle(
        "2-pass 자가 검토",
        value=False,
        help="모델이 초안을 검토하고 다시 작성. 시간 약 2배, 품질 향상.",
    )
    use_fact_check = st.toggle(
        "사실 자동 검증",
        value=True,
        help="본문 수치·기관명이 입력에 있는지 자동 점검 (의심 항목 표시).",
    )
    temperature = st.slider(
        "창의성(Temperature)",
        min_value=0.1, max_value=1.0, value=0.5, step=0.1,
        help="낮을수록 정형·일관적, 높을수록 다양·창의적.",
    )

    st.info("💡 V7.0 - Gemini · 뉴스레터 디자인 · 사실 검증 · 발행 이력")


# ============================================================
# 16. 메인 UI
# ============================================================
st.title("🏛️ SEOJEONG 보도자료 아키텍트 V7.0")
st.caption("Gemini · 뉴스레터 디자인 · 사진 2장 · 다국어 폰트 · 사실 검증 · 발행 이력")

tab_compose, tab_history = st.tabs(["📝 보도자료 작성", "📚 발행 이력"])

with tab_compose:
    col1, col2 = st.columns(2)
    with col1:
        event_name = st.text_input(
            "📌 핵심 제목 키워드",
            placeholder="예: 장영실학당 개소 및 국제 교육 확산",
        )
        event_date = st.text_input(
            "📅 보도 시점",
            value=datetime.now().strftime("%Y년 %m월 %d일"),
        )
    with col2:
        event_location = st.text_input(
            "📍 장소/부서",
            placeholder="예: 서정대학교 본관 세미나실",
        )
        dept_name = st.text_input(
            "🏢 작성부서",
            value="홍보실",
            placeholder="예: 홍보실, 국제교육원, 입학처",
            help="출력물 하단 푸터에 표시됩니다.",
        )
        uploaded_images = st.file_uploader(
            "📸 보도 사진 (최대 2장)",
            type=['png', 'jpg', 'jpeg'],
            accept_multiple_files=True,
        )
        if uploaded_images and len(uploaded_images) > 2:
            st.warning("⚠️ 사진은 최대 2장까지만 사용됩니다. 처음 2장이 적용됩니다.")
            uploaded_images = uploaded_images[:2]

    input_hint_map = {
        "단신": "핵심 사실 위주로 간결히. 누가/언제/어디서/무엇을 정도면 충분.",
        "표준": "참석자·진행순서·정량정보·인용멘트 등을 충실히. 100자 이상 권장.",
        "특집": "여러 측면을 풍부하게. 성과·인증·시스템·학생 지원·지역 연계·미래 비전 등 다각도로 200자 이상 권장.",
    }
    key_intent = st.text_area(
        "💡 상세 사실 관계(Fact)",
        height=180 if length_mode == "특집" else 130,
        placeholder=input_hint_map[length_mode],
        help=f"현재 분량 모드: {length_mode} - {input_hint_map[length_mode]}",
    )

    # ────────────────────────────────────────────────
    # 생성 로직
    # ────────────────────────────────────────────────
    if st.button("🚀 보도자료 발행", width='stretch', type="primary"):
        # 0) API 키 점검
        api_key = st.session_state.get("gemini_api_key", "").strip()
        if not api_key:
            st.error("Gemini API 키를 사이드바에 입력해주세요.")
            st.stop()

        if not event_name:
            st.error("핵심 키워드를 입력해주세요.")
            st.stop()

        min_input_map = {"단신": 20, "표준": 50, "특집": 100}
        min_len = min_input_map[length_mode]
        if not key_intent or len(key_intent) < min_len:
            st.warning(
                f"{length_mode} 분량은 상세 사실관계가 최소 {min_len}자 이상 권장됩니다. "
                f"(현재 {len(key_intent or '')}자) 입력이 빈약하면 본문도 빈약해지거나 "
                f"모델이 사실을 지어낼 수 있습니다."
            )
            st.stop()

        if not REG_FONT_PATH:
            st.error("폰트 파일을 찾을 수 없습니다. NotoSansKR-Regular.otf 또는 "
                     "arial unicode ms.otf 를 같은 폴더에 두세요.")
            st.stop()

        progress = st.progress(0, text="시작...")

        try:
            # (1) RAG 검색
            k_map = {"단신": 2, "표준": 3, "특집": 5}
            k = k_map[length_mode]

            progress.progress(15, text=f"과거 보도자료 {k}건 검색 중 (하이브리드)...")
            context = ""
            if retriever:
                docs = retriever.search(
                    query       = event_name,
                    category    = category,
                    length_type = length_mode if length_mode == "특집" else None,
                    k           = k,
                    use_mmr     = (length_mode == "특집"),
                )
                context_parts = []
                cut = {"단신": 400, "표준": 800, "특집": 1200}[length_mode]
                for i, d in enumerate(docs, 1):
                    title  = d.metadata.get("title", "")
                    date   = d.metadata.get("date", "")
                    header = f"[참고 {i}] {title} ({date})" if title else f"[참고 {i}]"
                    context_parts.append(f"{header}\n{d.page_content[:cut]}")
                context = "\n\n".join(context_parts)

            # (2) 1차 생성
            progress.progress(35, text=f"1차 본문 작성 중 ({length_mode})...")
            gen_prompt = build_generation_prompt(
                category=category,
                length_mode=length_mode,
                event_name=event_name,
                event_date=event_date,
                event_location=event_location,
                key_intent=key_intent,
                context=context,
                lang=lang_map[selected_lang],
            )
            max_tok = LENGTH_PROFILES[length_mode]["max_tokens"]
            raw1, used_model = call_gemini(
                gen_prompt, api_key=api_key,
                temperature=temperature, max_output_tokens=max_tok,
                force_json=True,
            )
            st.caption(f"사용 모델: {used_model}")
            draft = parse_press_json(raw1, lang=lang_map[selected_lang])

            # (3) 2차 검토(선택)
            if use_2pass:
                progress.progress(65, text="자가 검토 후 개선판 작성 중...")
                critique_prompt = build_critique_prompt(draft, category, length_mode)
                raw2, _ = call_gemini(
                    critique_prompt, api_key=api_key,
                    temperature=temperature * 0.8, max_output_tokens=max_tok,
                    force_json=True,
                )
                final = parse_press_json(raw2, lang=lang_map[selected_lang])
            else:
                final = draft

            if not final["title"] or not final["body"]:
                with st.expander("🔍 디버그: 모델 원본 응답 확인", expanded=True):
                    st.code(raw1[:2000], language="json")
                st.error(
                    "생성 결과가 비어 있습니다. 다음을 확인해 보세요:\n"
                    "- 상세 사실관계 입력이 충분한지 (더 구체적으로)\n"
                    "- 위 디버그 박스에서 모델이 뭘 반환했는지\n"
                    "- 잠시 후 다시 시도"
                )
                st.stop()

            # (4) 분량 검증 표시
            actual_len = len(final["body"])
            target_range = LENGTH_PROFILES[length_mode]["char_range"]
            st.caption(f"📊 본문 분량: 실제 {actual_len}자 / 목표 {target_range}")

            # (5) 사실 자동 검증 (F)
            if use_fact_check and lang_map[selected_lang] == "Korean":
                suspicious = fact_verification(
                    final["body"],
                    key_intent=key_intent,
                    event_date=event_date,
                    event_name=event_name,
                )
                if suspicious:
                    with st.expander(f"⚠️ 사실 검증: 입력에 없는 항목 {len(suspicious)}건 발견 (확인 필요)", expanded=True):
                        for kind, item in suspicious:
                            st.markdown(f"- **{kind}**: `{item}`")
                        st.caption("→ 위 항목들이 사용자 입력 또는 브랜드 상수에 없습니다. "
                                   "잘못된 정보일 수 있으니 확인해 주세요.")
                else:
                    st.success("✅ 사실 검증 통과: 의심 항목 없음")

            progress.progress(80, text="이미지 렌더링 중...")

            with st.expander("📝 텍스트로 먼저 확인", expanded=False):
                st.markdown(f"### {final['title']}")
                st.markdown(f"*{final['subtitle']}*")
                st.write(final['body'])

            # (6) 글자 깨짐 사전 검증
            check_font = get_font(20)
            all_text = final['title'] + final['subtitle'] + final['body']
            missing = check_glyph_coverage(all_text, check_font)
            if missing:
                st.warning(
                    f"⚠️ 현재 폰트에 글리프가 없는 문자 {len(missing)}개 발견: "
                    f"`{''.join(sorted(missing))[:30]}...` "
                    "→ NotoSansKR 폰트를 같은 폴더에 배치하면 해결됩니다."
                )

            # (7) 이미지 렌더링 (뉴스레터)
            final_img = render_newsletter(
                final=final,
                lang=selected_lang,
                category=category,
                length_mode=length_mode,
                event_date=event_date,
                uploaded_images=uploaded_images or [],
                dept_name=dept_name or "홍보실",
            )

            png_buf = io.BytesIO()
            final_img.save(png_buf, format="PNG")
            pdf_bytes = image_to_pdf_bytes(final_img)

            # session_state에 저장 → 다운로드 버튼 클릭 후 재실행돼도 유지
            st.session_state["last_png"] = png_buf.getvalue()
            st.session_state["last_pdf"] = pdf_bytes
            st.session_state["last_filename_base"] = (
                f"Seojeong_Press_{length_mode}_{datetime.now().strftime('%Y%m%d_%H%M')}"
            )

            progress.progress(95, text="이력 저장 중...")

            # (8) 발행 이력 로깅 (G)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record = {
                "timestamp":  ts,
                "model":      used_model,
                "category":   category,
                "length":     length_mode,
                "language":   lang_map[selected_lang],
                "event_name": event_name,
                "event_date": event_date,
                "title":      final['title'],
                "subtitle":   final['subtitle'],
                "body":       final['body'],
                "actual_len": actual_len,
                "suspicious_count": len(suspicious) if use_fact_check and lang_map[selected_lang] == "Korean" else 0,
            }
            log_history(record)

            progress.progress(100, text="완료!")
            st.divider()
            st.image(st.session_state["last_png"], width='stretch')

        except gexc.PermissionDenied:
            st.error("API 키가 거부되었습니다. 키가 올바른지, 결제 설정이 되어 있는지 확인하세요.")
        except gexc.InvalidArgument as e:
            st.error(f"요청 형식 오류: {e}")
        except gexc.ResourceExhausted:
            st.error("API 호출 한도 초과. 잠시 후 다시 시도하세요.")
        except gexc.DeadlineExceeded:
            st.error("Gemini 응답 시간 초과. 특집 분량은 시간이 오래 걸릴 수 있습니다.")
        except Exception as e:
            st.error(f"생성 오류: {type(e).__name__}: {e}")

    # ── 다운로드 버튼: try 블록 바깥에 두어 재실행 후에도 유지 ──
    if st.session_state.get("last_png"):
        fname = st.session_state.get("last_filename_base", "Seojeong_Press")
        cdl1, cdl2 = st.columns(2)
        with cdl1:
            st.download_button(
                "PNG 다운로드",
                st.session_state["last_png"],
                f"{fname}.png",
                mime="image/png",
                width='stretch',
            )
        with cdl2:
            st.download_button(
                "PDF 다운로드",
                st.session_state["last_pdf"],
                f"{fname}.pdf",
                mime="application/pdf",
                width='stretch',
            )


with tab_history:
    st.subheader("📚 최근 발행 이력")
    st.caption(f"저장 위치: `{HISTORY_PATH}`")

    history = load_history(n=30)
    if not history:
        st.info("아직 발행 이력이 없습니다. 보도자료를 생성하면 자동으로 누적됩니다.")
    else:
        for i, rec in enumerate(history, 1):
            with st.expander(
                f"{i}. [{rec.get('timestamp','')}] {rec.get('title','(제목 없음)')[:50]}"
                f"  ·  {rec.get('category','')} / {rec.get('length','')} / {rec.get('language','')}",
                expanded=False,
            ):
                st.markdown(f"**모델**: `{rec.get('model','')}`  |  "
                            f"**핵심 키워드**: {rec.get('event_name','')}  |  "
                            f"**보도 시점**: {rec.get('event_date','')}")
                st.markdown(f"**부제**: {rec.get('subtitle','')}")
                st.markdown(f"**분량**: {rec.get('actual_len',0)}자  |  "
                            f"**의심 항목**: {rec.get('suspicious_count',0)}건")
                st.write(rec.get('body',''))