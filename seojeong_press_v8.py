"""
SEOJEONG 보도자료 아키텍트 V8.0 (Phase 1+2+3 통합)
======================================================================
V7 → V8 주요 변경사항
1. [Phase 1] 비활성 RAG 잔재 일괄 제거
             - retriever / MASK_PATTERNS / RAG 검색 단계 모두 삭제
             - Few-shot → few_shots/*.md 외부 파일 분리
2. [Phase 2] 브랜드 사실 외부화
             - brand_facts.yaml 분리 (시간 불변 / 시간 가변 / 사업 목록)
             - load_brand_identity() 런타임 로드
             - valid_until 초과 시 Streamlit 경고 자동 표시
3. [Phase 3] 언어별 폰트 라우팅 (키릴 깨짐 해결)
             - load_font_sets() : cjk / latin / cyrillic 세 그룹 분리
             - get_font(size, bold, lang) : 언어에 따라 올바른 폰트 선택
             - render_newsletter 에 lang_code 인자 연결
             - WRITING_STYLE_GUIDE 한국어 전용 규칙 조건부 처리
======================================================================
※ Phase 4·5 예정
  - 카테고리당 다중 예시 + 랜덤 샘플링 (표현 다양성)
  - 부서별 배포 인프라 (API 키 중앙 관리)
======================================================================
※ 사전 준비
  - pip install google-generativeai streamlit pillow requests reportlab pyyaml
  - few_shots/ 폴더가 같은 폴더에 있어야 함
  - brand_facts.yaml 이 같은 폴더에 있어야 함
  - 폰트 파일 (우선순위대로 배치 — 없으면 시스템 폰트 또는 기본 폰트):
      · CJK/한국어  : NotoSansKR-Regular.otf / NotoSansKR-Bold.otf
      · 키릴(몽골어): NotoSans-Regular.ttf / NotoSans-Bold.ttf
      · 라틴계      : 위 파일로 대부분 커버 (없으면 시스템 폰트)
"""

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
import io, os, re, json, random
from datetime import datetime, date

# Gemini SDK
import google.generativeai as genai
from google.api_core import exceptions as gexc

# PyYAML (brand_facts.yaml 로드용) — 없으면 내장 fallback 사용
try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None
    _YAML_AVAILABLE = False


# ============================================================
# 1. 경로 및 기본 설정
# ============================================================
CURRENT_DIR        = os.path.dirname(os.path.abspath(__file__))
FEW_SHOT_DIR       = os.path.join(CURRENT_DIR, "few_shots")
BRAND_FACTS_PATH   = os.path.join(CURRENT_DIR, "brand_facts.yaml")
HISTORY_PATH       = os.path.join(CURRENT_DIR, "press_history.jsonl")


def _brand_mtime() -> float:
    """brand_facts.yaml 수정 시각 반환. 파일 없으면 0.
    load_brand_identity 캐시 키로 사용 → yaml 저장 즉시 새로 로드."""
    try:
        return os.path.getmtime(BRAND_FACTS_PATH)
    except OSError:
        return 0.0

st.set_page_config(
    page_title="서정대 보도자료 아키텍트 V8",
    page_icon="🏫",
    layout="wide",
    menu_items={
        "Get Help":    None,
        "Report a bug": None,
        "About":       "서정대학교 보도자료 자동 생성 시스템 V8.0 — Powered by Gemini",
    },
)


# ============================================================
# 2. [고정 레이어 A] 대학 아이덴티티 — brand_facts.yaml에서 로드
#    파일 없거나 pyyaml 미설치 시 내장 fallback 사용
# ============================================================

_BRAND_IDENTITY_FALLBACK = """
[서정대학교 브랜드 아이덴티티]
⚠ brand_facts.yaml을 확인하세요.

슬로건: "세상의 힘이 되다"
비전: "지역사회에 기여하는 학생중심 현장실무 대학교"
미션: 미래 산업현장에서 존경받는 전문직업인력 육성
총장: 양영희 총장

정량 정보:
- 전국 전문대 재학생 수 1위
- 신입생 충원율 100% (개교 이래 유지)
- 외국인 유학생 수 전국 1위
- 국가시험 100% 합격 (간호사·응급구조사)
- 14년 연속 합격·취업 성과

정부 인증: 교육부 교육국제화역량 인증대학 외
"""


def _load_brand_data():
    """brand_facts.yaml을 로드해 dict 반환. 실패 시 None."""
    if not _YAML_AVAILABLE:
        return None
    if not os.path.exists(BRAND_FACTS_PATH):
        return None
    try:
        with open(BRAND_FACTS_PATH, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f)
    except Exception:
        return None


@st.cache_data
def load_brand_identity(category: str = "", _mtime: float = 0):
    """brand_facts.yaml에서 프롬프트용 아이덴티티 문자열 조립.
    _mtime: 파일 수정 시각을 캐시 키로 사용 → yaml 저장 즉시 반영.
    category가 주어지면 해당 카테고리 강조 포인트를 함께 주입."""

    data = _load_brand_data()

    if data is None:
        if not _YAML_AVAILABLE:
            st.warning("⚠️ pyyaml 미설치. `pip install pyyaml` 후 재시작하세요.")
        else:
            st.warning("⚠️ brand_facts.yaml 없음. 내장 fallback 사용.")
        return _BRAND_IDENTITY_FALLBACK

    # ─── 만료 검사 ───
    try:
        valid_until_str = data.get("meta", {}).get("valid_until", "")
        if valid_until_str and date.today() > date.fromisoformat(str(valid_until_str)):
            st.warning(
                f"⚠️ brand_facts.yaml **valid_until({valid_until_str}) 초과.** "
                "재학생 수·유학생 수·연속 성과 연수 등을 갱신해 주세요."
            )
    except Exception:
        pass

    identity  = data.get("identity", {})
    stats     = data.get("stats", {})
    certs     = data.get("certifications", [])
    banned    = data.get("banned_phrases", [])
    overuse   = data.get("avoid_overuse", [])
    as_of     = data.get("meta", {}).get("as_of", "")

    cert_lines     = "\n".join(f"- {c}" for c in certs)
    banned_lines   = "\n".join(
        f'  - "{b["phrase"]}" → 대신: "{b["replace_with"]}"  ({b["reason"]})'
        for b in banned
    )
    overuse_lines  = ", ".join(f'"{w}"' for w in overuse)
    ideology_lines = "\n".join(f"- {i}" for i in identity.get("founding_ideology", []))
    strategy_lines = "\n".join(f"- {s}" for s in identity.get("strategy_directions", []))

    # ─── 카테고리별 강조 포인트 (있을 때만) ───
    cat_section = ""
    if category:
        cat_data = data.get("category_focus", {}).get(category, {})
        if cat_data:
            frame       = cat_data.get("message_frame", "")
            emph_lines  = "\n".join(f"  · {e}" for e in cat_data.get("emphasis", []))
            cat_section = f"""
# 이번 카테고리 메시지 방향 [{category}]
메시지 프레임: {frame}
강조 포인트 (사실관계에 맞는 1~2개만 자연스럽게 활용, 전부 쓰지 말 것):
{emph_lines}"""

    return f"""[서정대학교 브랜드 아이덴티티 - 반드시 이 정보만 사용]

# 설립 정체성
- 슬로건: "{identity.get('slogan', '')}"
- 비전: "{identity.get('vision', '')}"
- 미션: "{identity.get('mission', '')}"
- 총장 호칭: "{identity.get('president', '')}"

# 설립 교육이념
{ideology_lines}

# 중장기 전략방향 (보도자료 문맥에 자연스럽게 연결)
{strategy_lines}

# 정량 사실 (이 수치만 사용. 입력에 없는 수치는 창작 금지)
- {stats.get('enrollment_rank', '')} ({stats.get('enrollment_count', '')}, {stats.get('enrollment_date', as_of + ' 기준')})
- {stats.get('admission_fill_rate', '')}
- {stats.get('retention_fill_rate', '')}
- {stats.get('intl_students', '')}
- {stats.get('national_exam', '')}
- {stats.get('consecutive_achievement', '')}

# 정부 인증·사업 (이 명칭만 정확히 사용)
{cert_lines}

# ⛔ 절대 사용 금지 — 아래 표현은 어떤 상황에서도 쓰지 말 것
{banned_lines}

# ⚠️ 과다 사용 주의 — 보도자료 전체에서 각 표현은 1회 이하로 절제
{overuse_lines}
{cat_section}
"""


def get_banned_phrases():
    """banned_phrases 목록 반환 (백스톱 치환용). [(phrase, replace_with), ...]"""
    data = _load_brand_data()
    if data is None:
        return []
    return [(b["phrase"], b["replace_with"]) for b in data.get("banned_phrases", [])]

# ============================================================
# 2-2. [기조 레이어 B] 보도자료 전개 방식
# ============================================================
WRITING_STYLE_GUIDE = """
[보도자료 전개 기조 - 문장 호흡·구조만 적용]

# 공통 구조 원칙
- 리드: "서정대학교(총장 양영희)는 [날짜] [장소]에서 [무엇을] [했다고 밝혔다]" 형식
- 총장 멘트 구조: [성과 인식] + [학생·학과 칭찬] + [앞으로의 약속]
- 마무리: "앞으로도 ~하겠다" 형식의 향후 계획

# 톤·원칙
- 정중하고 절제된 저널리즘 어조 (~했다, ~라고 밝혔다)
- 부정적 정보·경쟁 대학 비교·미확정 사실 금지
- 광고 어조 형용사("최고의", "유일한" 등) 객관 지표로 대체
- 학과·기관·인물은 정식 명칭 사용
- 마크다운 헤더(#, ##) 사용 금지

# 한국어 출력 전용 추가 규칙
- 한자 사용 절대 금지
- 외국어 단어 삽입 금지 (고유명사 제외)
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
        "max_tokens": 1024,
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
        "max_tokens": 6144,
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
        "max_tokens": 4096,
    },
}


# ============================================================
# 4. 카테고리별 Few-shot 예시 — Phase 4: 다중 파일 랜덤 샘플링
# ============================================================
# 카테고리 → 파일명 prefix 매핑
# 파일 패턴: {prefix}.md 또는 {prefix}_숫자.md
# 예: 학생.md / 학생_01.md / 학생_02.md → 셋 중 매 실행마다 1개 랜덤 선택
FEW_SHOT_PREFIXES = {
    "입시, 모집":         "입시_모집",
    "학생":               "학생",
    "교육, 행정":         "교육_행정",
    "지역사회, 산학협력": "지역사회_산학협력",
    "국제, 글로벌":       "국제_글로벌",
    "대학 성과":          "대학_성과",
}

# 모든 .md 파일이 누락된 비상시 사용할 최후 fallback
_FALLBACK_FEW_SHOT = """주제목: 서정대학교, 산학협력으로 실무 인재 양성 강화
부제목: 현장 중심 교육과정 확대… 전문직업인력 배출 가속화

본문: 서정대학교(총장 양영희)는 산학협력 기반 교육을 강화한다고 밝혔다. 이번 계획은 현장 실무 역량 향상을 핵심으로 한다. 양영희 총장은 "앞으로도 전문직업교육의 새로운 기준을 만들어 나가겠다"고 밝혔다.
"""


def _parse_few_shot_markdown(text):
    """마크다운에서 '# 주제목', '# 부제목', '# 본문' 섹션을 뽑아
    프롬프트에 넣을 수 있는 한 덩어리 텍스트로 재구성.
    파일 형식이 깨져 있으면 원문 그대로 반환."""
    sections = {"주제목": "", "부제목": "", "본문": ""}
    current = None
    lines = []
    for raw in text.splitlines():
        m = re.match(r'^#\s*(주제목|부제목|본문)\s*$', raw.strip())
        if m:
            if current and lines:
                sections[current] = "\n".join(lines).strip()
            current = m.group(1)
            lines = []
        elif current is not None:
            lines.append(raw)
    if current and lines:
        sections[current] = "\n".join(lines).strip()
    if not any(sections.values()):
        return text.strip()
    return (
        f"주제목: {sections['주제목']}\n"
        f"부제목: {sections['부제목']}\n\n"
        f"본문: {sections['본문']}"
    )


def _scan_few_shot_candidates(prefix):
    """prefix 패턴에 맞는 few-shot 파일 목록 반환.
    허용 패턴: {prefix}.md  또는  {prefix}_숫자.md"""
    if not os.path.isdir(FEW_SHOT_DIR):
        return []
    pat = re.compile(rf'^{re.escape(prefix)}(_\d+)?\.md$')
    return sorted(
        os.path.join(FEW_SHOT_DIR, f)
        for f in os.listdir(FEW_SHOT_DIR)
        if pat.match(f)
    )


def load_few_shot(category):
    """카테고리 prefix로 파일 후보를 모아 랜덤 1개 선택해 파싱된 문자열 반환.
    후보가 없으면 '학생' prefix로 재시도, 그것도 없으면 내장 fallback.
    반환: (parsed_text, chosen_filename)"""
    prefix = FEW_SHOT_PREFIXES.get(category)
    candidates = _scan_few_shot_candidates(prefix) if prefix else []

    # 후보 없으면 '학생' 예시로 fallback
    if not candidates:
        fallback_prefix = FEW_SHOT_PREFIXES.get("학생", "학생")
        candidates = _scan_few_shot_candidates(fallback_prefix)

    if candidates:
        chosen = random.choice(candidates)
        try:
            with open(chosen, "r", encoding="utf-8") as f:
                return _parse_few_shot_markdown(f.read()), os.path.basename(chosen)
        except Exception:
            pass
    return _FALLBACK_FEW_SHOT, "fallback"


# ============================================================
# 5. 폰트 로딩 — 언어 그룹별 분리 (Phase 3)
# ============================================================
# 언어 코드 → 문자 체계 그룹 매핑
def _lang_script(lang_code: str) -> str:
    """언어 코드를 폰트 그룹(cjk / cyrillic / latin)으로 변환."""
    if lang_code in ("Korean",):
        return "cjk"
    elif lang_code in ("Mongolian",):
        return "cyrillic"
    else:                          # English, Vietnamese, Uzbek 등 라틴 계열
        return "latin"


@st.cache_resource
def load_font_sets():
    """cjk / latin / cyrillic 세 그룹에 대해 각각 regular/bold 경로를 찾아 반환.
    폴더 우선순위: 앱과 같은 폴더 → 리눅스 시스템 Noto → macOS → Windows → None"""

    def first_exists(paths):
        for p in paths:
            if os.path.exists(p):
                return p
        return None

    # ── CJK (한국어·한자·히라가나 포함) ──
    cjk_reg = first_exists([
        os.path.join(CURRENT_DIR, "NotoSansKR-Regular.otf"),
        os.path.join(CURRENT_DIR, "NotoSansKR-Regular.ttf"),
        os.path.join(CURRENT_DIR, "NotoSans-Regular.ttf"),  # CJK 포함 버전도 가능
        os.path.join(CURRENT_DIR, "arial unicode ms.otf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "C:/Windows/Fonts/malgun.ttf",
    ])
    cjk_bold = first_exists([
        os.path.join(CURRENT_DIR, "NotoSansKR-Bold.otf"),
        os.path.join(CURRENT_DIR, "NotoSansKR-Bold.ttf"),
        os.path.join(CURRENT_DIR, "NotoSans-Bold.ttf"),
        os.path.join(CURRENT_DIR, "arial unicode ms.otf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "C:/Windows/Fonts/malgunbd.ttf",
    ])

    # ── 키릴 (몽골어 등 Cyrillic 문자 포함) ──
    # NotoSans (KR이 아닌 plain)은 라틴+키릴+데바나가리 등 다국어 지원
    cyrillic_reg = first_exists([
        os.path.join(CURRENT_DIR, "NotoSans-Regular.ttf"),     # 키릴 포함 권장
        os.path.join(CURRENT_DIR, "NotoSans-Regular.otf"),
        os.path.join(CURRENT_DIR, "FreeSans.ttf"),             # GPL, 키릴 포함
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",     # 키릴 포함
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ])
    cyrillic_bold = first_exists([
        os.path.join(CURRENT_DIR, "NotoSans-Bold.ttf"),
        os.path.join(CURRENT_DIR, "NotoSans-Bold.otf"),
        os.path.join(CURRENT_DIR, "FreeSansBold.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ])

    # ── 라틴 (영어·베트남어·우즈벡어 등) ──
    # 베트남어는 라틴 확장 문자(U+0100~) 포함 — NotoSans가 커버
    latin_reg = first_exists([
        os.path.join(CURRENT_DIR, "NotoSans-Regular.ttf"),
        os.path.join(CURRENT_DIR, "NotoSansKR-Regular.otf"),   # 라틴도 커버
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # 라틴 포함
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ])
    latin_bold = first_exists([
        os.path.join(CURRENT_DIR, "NotoSans-Bold.ttf"),
        os.path.join(CURRENT_DIR, "NotoSansKR-Bold.otf"),
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ])

    return {
        "cjk":      {"regular": cjk_reg,      "bold": cjk_bold},
        "latin":    {"regular": latin_reg,     "bold": latin_bold},
        "cyrillic": {"regular": cyrillic_reg,  "bold": cyrillic_bold},
    }


FONT_SETS = load_font_sets()


def get_font(size, bold=False, lang="Korean"):
    """언어에 맞는 폰트 그룹을 선택해 ImageFont 객체 반환.
    폰트 파일 없으면 PIL 기본 폰트 fallback."""
    script = _lang_script(lang)
    group  = FONT_SETS.get(script) or FONT_SETS.get("cjk") or {}
    key    = "bold" if (bold and group.get("bold")) else "regular"
    path   = group.get(key)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # 최후 fallback: 시스템 기본 폰트
    return ImageFont.load_default()


def check_glyph_coverage(text, lang="Korean"):
    """언어에 맞는 폰트로 텍스트의 글리프 커버리지 검증. 누락 문자 셋 반환."""
    font = get_font(20, lang=lang)
    missing = set()
    for ch in text:
        if ch.isspace() or ch in "\n\r\t":
            continue
        try:
            bbox = font.getbbox(ch)
            if bbox is None or (bbox[2] - bbox[0]) == 0:
                missing.add(ch)
        except Exception:
            missing.add(ch)
    return missing


# ============================================================
# 6. 텍스트 처리 헬퍼
# ============================================================
def clean_text_final(text, lang="Korean"):
    """마크다운 제거. 한국어 출력 시 한자 제거.
    banned_phrases 백스톱: yaml에 등록된 금지 표현을 대체어로 치환."""
    if not text:
        return ""
    text = re.sub(r'\*\*|__', '', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    if lang == "Korean":
        text = re.sub(r'[\u4e00-\u9fff]+', '', text)

    # ── 백스톱: 금지 표현 강제 치환 ──
    for phrase, replace_with in get_banned_phrases():
        if phrase:
            text = text.replace(phrase, replace_with)

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
# 7. Gemini 호출
# ============================================================
# 자동 시도할 모델 우선순위 (위에서부터 시도, 실패 시 다음 모델)
AUTO_MODEL_FALLBACK = [
    "gemini-2.0-flash",        # 1순위: thinking 없음, 안정적
    "gemini-2.0-flash-lite",   # 백업
    "gemini-2.5-pro",          # 고품질 (RPM 150)
    "gemini-flash-latest",     # 별칭 (SDK가 최신 매핑)
    "gemini-2.5-flash",        # 마지막 — thinking 모드라 토큰 낭비 가능
]

def call_gemini(prompt, api_key, temperature=0.5,
                max_output_tokens=2048, force_json=True):
    """Gemini API 자동 호출. 모델 폴백."""
    genai.configure(api_key=api_key)

    last_error = None
    for model_name in AUTO_MODEL_FALLBACK:
        generation_config = {
            "temperature": temperature,
            "top_p": 0.9,
            # 2.5-flash thinking 대비: 큰 폭으로 늘려 thinking 후에도 본문 생성 가능
            "max_output_tokens": max_output_tokens * 4 if "2.5-flash" in model_name else max_output_tokens,
        }
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config,
            )
            resp = model.generate_content(prompt)
            text = resp.text.strip() if resp.text else ""
            if text and len(text) > 100:   # 정상 응답 (최소 100자 이상)
                return text, model_name
            last_error = f"{model_name}: 너무 짧은 응답 ({len(text)}자)"
        except gexc.NotFound:
            last_error = f"{model_name}: 모델 없음(404)"
        except Exception as e:
            last_error = f"{model_name}: {type(e).__name__} {e}"

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
        # JSON이 잘렸을 때 — 각 필드를 정규식으로 따로 추출 (DOTALL · greedy)
        # title/subtitle은 한 줄로 끝나는 경우가 많고, body는 마지막까지 흐름
        def extract_field(field_name, text, is_last=False):
            if is_last:
                # body: 다음 필드가 없거나 JSON 끝일 수 있음 → 끝까지 모두 가져옴
                pattern = rf'"{field_name}"\s*:\s*"(.*?)(?:"\s*[,}}]|\Z)'
            else:
                # title, subtitle: 다음 `",` 또는 `"\n` 까지
                pattern = rf'"{field_name}"\s*:\s*"(.*?)"\s*,'
            m = re.search(pattern, text, re.DOTALL)
            return m.group(1) if m else ""

        title    = extract_field("title", cleaned, is_last=False)
        subtitle = extract_field("subtitle", cleaned, is_last=False)
        body     = extract_field("body", cleaned, is_last=True)
        return {
            "title":    clean_text_final(title, lang),
            "subtitle": clean_text_final(subtitle, lang),
            "body":     clean_text_final(fix_newlines(body), lang),
        }


# ============================================================
# 8. 프롬프트 빌더 — 레이어 A/B 명시 분리
# ============================================================
def build_generation_prompt(category, length_mode, event_name, event_date,
                             event_location, key_intent, lang):
    example, _chosen = load_few_shot(category)
    profile          = LENGTH_PROFILES[length_mode]
    brand_identity   = load_brand_identity(category=category, _mtime=_brand_mtime())

    # 한국어 전용 금기사항을 프롬프트에 조건부 삽입
    ko_only_rule = (
        "\n⚠️ 한국어 출력 추가 규칙: 한자·외국어 단어 절대 사용 금지 (고유명사 제외)"
        if lang == "Korean" else ""
    )

    return f"""당신은 서정대학교 홍보실의 수석 에디터입니다.
다음 두 종류의 정보를 명확히 구분하여 사용하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【레이어 A: 고정 사실 — 이 정보만 사용. 절대 변경·창작 금지】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{brand_identity}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【레이어 B: 전개 기조 — 문장 흐름·톤만 참고】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{WRITING_STYLE_GUIDE}{ko_only_rule}

{profile['detail_guide']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【모범 예시 — 같은 카테고리. 톤·문장 호흡만 참고, 사실 복사 금지】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
카테고리: {category}
{example}

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
    profile        = LENGTH_PROFILES[length_mode]
    brand_identity = load_brand_identity(category=category, _mtime=_brand_mtime())
    return f"""당신은 서정대학교 홍보실의 수석 에디터입니다. 초안을 검토하고 개선판을 작성하세요.

{brand_identity}

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
# 8-2. 사후 백스톱 치환 — 모델이 금지 표현을 무시했을 때 강제 교체
# ============================================================
def apply_banned_replacements(text: str) -> tuple[str, list[str]]:
    """get_banned_phrases() 목록을 순회하며 금지 표현을 대체어로 치환.
    반환: (수정된 텍스트, 실제 치환된 항목 목록)"""
    replaced = []
    for phrase, replace_with in get_banned_phrases():
        if phrase in text:
            text = text.replace(phrase, replace_with)
            replaced.append(phrase)
    return text, replaced


def apply_banned_to_final(final: dict) -> tuple[dict, list[str]]:
    """title/subtitle/body 전체에 백스톱 치환 적용."""
    all_replaced = []
    for key in ("title", "subtitle", "body"):
        if final.get(key):
            final[key], rep = apply_banned_replacements(final[key])
            all_replaced.extend(rep)
    return final, list(set(all_replaced))


# ============================================================
# 9. 사실 자동 검증 (추가기능 F)
# ============================================================
def fact_verification(body, key_intent, event_date="", event_name="",
                      brand_identity=None):
    """본문에 등장하는 수치·기관명·연도가 입력 또는 브랜드 상수에 존재하는지 검증.
    의심스러운 항목 리스트 반환."""
    if brand_identity is None:
        brand_identity = load_brand_identity(_mtime=_brand_mtime())
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
# 10. 발행 이력 로깅 (추가기능 G)
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
# 11. PDF 생성 (PIL 이미지를 PDF로 저장)
# ============================================================
def image_to_pdf_bytes(pil_image):
    buf = io.BytesIO()
    # PIL은 RGB 모드일 때 PDF 저장이 가장 안정적
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    pil_image.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


# ============================================================
# 12. 이미지 렌더링 — 뉴스레터 디자인
# ============================================================
def render_newsletter(final, lang, lang_code, category, length_mode,
                      event_date, uploaded_images, dept_name="홍보실"):
    """뉴스레터 톤의 보도자료 PNG 생성. lang_code는 폰트 그룹 선택에 사용."""
    W = 900
    H_MAP = {"단신": 6000, "표준": 10000, "특집": 25000}
    H = H_MAP.get(length_mode, 25000)
    canvas = Image.new('RGB', (W, H), 'white')
    draw   = ImageDraw.Draw(canvas)

    # 언어에 맞는 폰트 세트
    f_brand    = get_font(22, bold=True,  lang=lang_code)
    f_meta     = get_font(15,             lang=lang_code)
    f_title    = get_font(40, bold=True,  lang=lang_code)
    f_subtitle = get_font(22,             lang=lang_code)
    f_body     = get_font(20,             lang=lang_code)
    f_footer   = get_font(14,             lang=lang_code)

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
# 13. 사이드바
# ============================================================
with st.sidebar:
    st.header("🔑 Gemini API 설정")

    # Streamlit Cloud: secrets에 GEMINI_API_KEY가 있으면 자동 적용
    _secret_key = ""
    try:
        _secret_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        pass  # 로컬 실행 시 secrets 없어도 무시

    if _secret_key:
        st.session_state["gemini_api_key"] = _secret_key
        st.success("✅ API 키가 설정되어 있습니다.", icon="🔒")
    else:
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
    category = st.selectbox("카테고리 분류", list(FEW_SHOT_PREFIXES.keys()))

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

    st.info("💡 V8.0 — brand_facts.yaml 갱신 + 키릴 폰트 라우팅 적용")


# ============================================================
# 14. 메인 UI
# ============================================================
st.title("🏛️ SEOJEONG 보도자료 아키텍트 V8.0")
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

        # 선택 언어에 맞는 폰트 있는지 사전 점검
        _lang_code_check = lang_map[selected_lang]
        _script_check    = _lang_script(_lang_code_check)
        _group_check     = FONT_SETS.get(_script_check, {})
        if not _group_check.get("regular"):
            st.error(
                f"선택 언어({selected_lang})에 맞는 폰트를 찾을 수 없습니다. "
                "NotoSans-Regular.ttf(또는 NotoSansKR-Regular.otf)를 앱 폴더에 배치해 주세요."
            )
            st.stop()

        progress = st.progress(0, text="시작...")

        try:
            # Phase 4: 어떤 예시가 선택됐는지 미리 확인 (디버그용)
            _, _chosen_shot = load_few_shot(category)
            st.caption(f"🎲 예시 파일: `{_chosen_shot}`")

            # (1) 1차 생성
            progress.progress(20, text=f"1차 본문 작성 중 ({length_mode})...")
            # 표준·특집은 다양성을 위해 temperature를 살짝 높게 조정
            _effective_temp = min(temperature + 0.1, 1.0) if length_mode != "단신" else temperature
            gen_prompt = build_generation_prompt(
                category=category,
                length_mode=length_mode,
                event_name=event_name,
                event_date=event_date,
                event_location=event_location,
                key_intent=key_intent,
                lang=lang_map[selected_lang],
            )
            max_tok = LENGTH_PROFILES[length_mode]["max_tokens"]
            raw1, used_model = call_gemini(
                gen_prompt, api_key=api_key,
                temperature=_effective_temp, max_output_tokens=max_tok,
                force_json=True,
            )
            st.caption(f"사용 모델: {used_model}")
            draft = parse_press_json(raw1, lang=lang_map[selected_lang])

            # (2) 2차 검토(선택)
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

            # 백스톱: 금지 표현 강제 치환 (4겹 방어 최후 단계)
            final, backstop_hits = apply_banned_to_final(final)
            if backstop_hits:
                st.warning(
                    f"⚠️ 금지 표현 자동 교체: **{', '.join(backstop_hits)}** "
                    "→ brand_facts.yaml의 대체어로 변경됐습니다."
                )

            # (3) 분량 검증 표시
            actual_len = len(final["body"])
            target_range = LENGTH_PROFILES[length_mode]["char_range"]
            st.caption(f"📊 본문 분량: 실제 {actual_len}자 / 목표 {target_range}")

            # (4) 사실 자동 검증 (F)
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

            # (5) 글자 깨짐 사전 검증 — 언어에 맞는 폰트로 점검
            lang_code = lang_map[selected_lang]
            all_text  = final['title'] + final['subtitle'] + final['body']
            missing   = check_glyph_coverage(all_text, lang=lang_code)
            if missing:
                script = _lang_script(lang_code)
                font_hint = {
                    "cjk":      "NotoSansKR-Regular.otf",
                    "cyrillic": "NotoSans-Regular.ttf  (키릴 문자 포함 버전)",
                    "latin":    "NotoSans-Regular.ttf",
                }.get(script, "NotoSans-Regular.ttf")
                st.warning(
                    f"⚠️ 선택한 언어({selected_lang}) 폰트에 글리프가 없는 문자 "
                    f"{len(missing)}개 발견: `{''.join(sorted(missing))[:30]}` "
                    f"→ **{font_hint}** 파일을 앱 폴더에 배치하면 해결됩니다."
                )

            # (6) 이미지 렌더링 (뉴스레터)
            final_img = render_newsletter(
                final=final,
                lang=selected_lang,
                lang_code=lang_code,
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

            # (7) 발행 이력 로깅 (G)
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