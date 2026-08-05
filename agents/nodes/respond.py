"""[4] 응답 생성 — 신호를 자연어로 조립. 수치 계산 금지, 렌더링만.

출력 통제(ADR 0006): LLM은 RespondOutput 슬롯(내용)만 채우고, 최종 답변 문자열의
양식은 render_answer()가 결정적으로 조립한다. 문서 발췌(제3자 작성)를 통한 간접
프롬프트 인젝션이 성공해도 산출물이 슬롯 밖으로 나갈 수 없다.
"""
import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel

from agents import llm
from agents.llm import ModelTier
from agents.logging_util import node_logger
from agents.schemas import Citation
from agents.tools.eligibility import remedy_hint

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "respond.md").read_text(
    encoding="utf-8")

# 질의 성격별 지시(respond.md의 {task_block}에 끼운다).
#
# 한 벌의 프롬프트로 성격이 다른 질문을 전부 받으면 지시가 서로 희석된다 —
# "공고 하나만 보고 답하라"와 "관련 없는 공고를 걸러내라"는 같은 턴에 둘 다
# 참일 수 없다. 공통 규칙(그라운딩·출력 위생·슬롯 정의)은 respond.md에 한 번만
# 두고, 이번 턴에 해당하는 지시만 골라 끼운다. 파일을 셋으로 쪼개지 않는 이유는
# 절대 규칙이 3중으로 복제되면 반드시 한쪽만 고쳐져 어긋나기 때문이다.

_TASK_DETAIL = """\
지금 사용자는 특정 공고 하나를 보면서 묻고 있다.

그 공고 한 건에만 집중해서 답하고, 신호에 다른 공고가 섞여 있어도 끌어오지 마라.
질문이 짚은 항목(마감일·금액·자격 요건 등)을 headline에서 먼저 답하고,
items에는 그렇게 답한 근거가 되는 원문 내용을 붙인다.

신호에 그 항목이 없으면 없다고 말해라. 비슷해 보이는 다른 수치로 대신 채우면
사용자가 그걸 답으로 믿는다."""

_TASK_LIST = """\
지금 사용자는 조건에 맞는 공고를 찾아달라고 했다.

검색은 관련 없는 공고도 섞어서 돌려준다. 거르는 건 네 몫이다.
<질의>에 나온 조건(지역·사업 종류·기관)에 맞지 않는 공고는 items에서 빼라.
경기 지역을 물었는데 전북 공고가 신호에 들어와 있으면 그건 버린다.

- headline에 건수를 적을 거면 items에 실제로 넣은 공고 수와 정확히 맞춰라.
- 같은 공고를 두 번 넣지 마라.
- 조건에 맞는 공고가 하나도 없으면 없다고 답하는 게 맞다. 억지로 채우지 마라."""

_TASK_ELIGIBILITY = """\
지금 사용자는 자격이 되는지를 묻고 있다.

headline에서 판정 결과(가능 / 미달)를 먼저 분명히 말한다. 돌려 말하지 마라.
미달이면 어느 항목이 왜 미달인지 신호에 적힌 사유를 필드 단위로 그대로 전달하고,
보완하면 충족할 수 있는 항목이 있으면 그것도 알려준다.
신호에 "→"로 적힌 보완 경로는 **그대로만** 전달하고, 없는 항목에 대해 보완 방법을
지어내지 마라. "판정 보류"라고 적힌 공고를 통과로 말하지 마라.

신호 머리줄에 "자격 '가능' 공고 N건 중 상위 M건"이 있으면, **N을 먼저
밝히고 지금 보여주는 것이 그중 M건임을 말한 뒤 더 볼지 물어라.** M건이 전부인
것처럼 답하면 사용자는 낼 수 있는 공고를 놓친다.
이 M건은 **마감이 임박한 순이 아니다.** 마감일이 신호에 있다고 해서 "마감 임박"·
"마감이 가까운 순" 같은 말을 붙이지 마라. 순서의 기준을 물으면 "자격 판정 근거가
많이 확인된 공고 순"이라고만 답하라.

각 공고는 통과 여부만 되풀이하지 말고 신호에 있는 것을 말해라 — 마감일시,
추정가격, 그리고 충족 축("면허, 실적, 신용")이나 충족 개수("7/9축").

자격 판정과 낙찰 가능성은 다른 이야기다. 자격이 된다고 해서 낙찰을 예측하지 마라.
이건 **하지 말라는 금지**이지, 매번 그 얘기를 덧붙이라는 뜻이 아니다.
"낙찰 가능성은 별도로 검토가 필요합니다" 같은 면책 문구를 caveat에 적지 마라 —
사용자가 묻지 않은 말이고, 판정 결과를 흐린다. 쓸 것이 없으면 caveat은 null이다."""

_NUM_RE = re.compile(r"\d+(?:[,.]\d+)*")
_DATE_TOKEN_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$")

# 출력 위생 — 악성 문서 발췌가 슬롯으로 흘러들어도 프론트에서 실행 가능한
# 형태(HTML 태그=XSS, 링크/이미지=대화 내용 유출 채널)로 나가지 않게 한다.
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")   # [t](u)·![t](u) → t
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")


class BidItem(BaseModel):
    """답변 한 줄 슬롯 — respond 내부 전용(팀 계약 아님).

    같은 bid_id로 여러 개를 쓰면 그 공고 아래 줄줄이 붙는다. 마감일·추정가격·
    자격 축처럼 성격이 다른 정보를 한 줄에 몰아 쓰면 읽히지 않으므로, 항목을
    나누고 각 줄에 이름(label)을 단다. 구분자와 들여쓰기는 render_answer가
    붙이므로 LLM은 이름과 값만 정한다.
    """
    bid_id: str
    label: str          # "마감", "추정가격" 같은 항목 이름. 없으면 빈 문자열
    text: str


class RespondOutput(BaseModel):
    """LLM이 채우는 내용 슬롯. 전 필드 required — 구조화 출력 스키마에서
    선택 필드를 남기지 않는다(누락 폴백 분기 자체를 없앤다)."""
    headline: str
    items: list[BidItem]
    caveat: str | None


def _squeeze(text: str) -> str:
    """연속 공백을 하나로. bid_table의 공고명에는 원본 그대로 이중 공백이
    들어있는 경우가 있어(예: "스쿨넷서비스  제공 용역") 표시 전에 정리한다."""
    return _WS_RE.sub(" ", text).strip()


def sanitize(text: str) -> str:
    """URL·HTML 태그·마크다운 링크 제거. 프롬프트로 막고(respond.md 절대 규칙)
    여기서 한 번 더 거른다 — 프롬프트는 확률, 이 함수는 보장."""
    cleaned = _MD_LINK_RE.sub(r"\1", text)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = _URL_RE.sub("", cleaned)
    if cleaned != text:
        logger.warning("sanitize: 답변 슬롯에서 링크/태그 제거됨")
    return cleaned.strip()


# label로 쓰면 안 되는 이름. 공고 이름은 render_answer가 이미 윗줄에 쓰므로,
# 이 이름을 단 항목은 같은 값을 한 번 더 적는 줄이 된다.
_REDUNDANT_LABELS = {"공고명", "공고이름", "공고번호", "사업명", "공고", "bidid"}
_LABEL_NOISE_RE = re.compile(r"[\s_·:]")


def _is_redundant(item: "BidItem", bid_label: str) -> bool:
    """윗줄의 공고 이름을 되풀이하는 항목인가.

    프롬프트로 금지해도 label을 "공고명"으로 바꿔 우회하는 경우가 관측됐다.
    프롬프트는 확률, 여기는 보장이다(ADR 0006 ③).
    """
    if _LABEL_NOISE_RE.sub("", item.label).lower() in _REDUNDANT_LABELS:
        return True
    return sanitize(_squeeze(item.text)) == bid_label


def render_answer(out: RespondOutput,
                  bid_names: dict[str, str] | None = None) -> str:
    """양식은 코드가 소유한다 — 한 줄 결론 → 공고별 항목 → 참고 순, 결정적.

    공고명은 한 줄을 차지하고 항목은 그 아래 들여쓴다. 한 공고에 마감일·
    추정가격·충족 축이 함께 붙는데 한 줄로 이어 붙이면 읽히지 않는다.
    같은 공고가 연달아 나오면 이름을 다시 쓰지 않는다.

        결론 문장입니다.
        - ○○ 도로시설개량공사-레미콘:
          · 마감 : 2026-07-31 10:00
          · 추정가격 : 250,873,091원
        참고: …

    공고 이름은 bid_names에 있으면 공고명, 없으면 bid_id로 폴백한다. 사용자에게
    공고번호는 읽을 이유가 없는 식별자이고, 필요한 쪽(백엔드·프론트)은
    citations에서 bid_id를 그대로 받는다.

    항목 이름은 LLM이 정하고 구분자(" : ")와 들여쓰기는 여기서 붙인다. 공고
    이름을 되풀이하는 항목은 버린다(_is_redundant). 공고 머리줄은 살아남은
    항목이 있을 때만 쓰므로, 전부 버려진 공고는 빈 껍데기로 남지 않는다.
    """
    names = bid_names or {}
    lines = [sanitize(out.headline)]
    prev_bid = None
    for i in out.items:
        bid_label = sanitize(_squeeze(names.get(i.bid_id) or i.bid_id))
        if _is_redundant(i, bid_label):
            logger.warning("render: 공고 이름을 되풀이하는 항목 제거 (label=%r)",
                           i.label)
            continue
        if bid_label != prev_bid:
            lines.append(f"- {bid_label}:")
            prev_bid = bid_label
        text = sanitize(i.text)
        item_label = sanitize(_squeeze(i.label))
        lines.append(f"  · {item_label} : {text}" if item_label
                     else f"  · {text}")
    if out.caveat:
        lines.append(f"참고: {sanitize(out.caveat)}")
    return "\n".join(line for line in lines if line)


# verdict(게이트 3-state) → 표시 라벨. verdict가 없으면(stub 등) passed로 폴백.
_VERDICT_LABEL = {
    "가능": "자격 통과",
    "불가": "자격 미달",
    "보완가능": "보완하면 가능",
    "확인필요": "확인 필요",
}


def _review_note(r) -> str:
    """'확인필요'가 어느 갈래인지 한 줄로 (N-4b).

    판정 함수 v2.2~v2.3의 '확인필요 캡' 2종(게이트 축 0개인데 supp 미충족 /
    유형별 기대 게이트 결측)으로 나온 행은 **미충족 축이 아니라 '없는 축'이
    사유**라, failed_reasons가 비는 것이 정상이다. 그대로 두면 신호가
    "확인 필요 (충족 5/5축 | 충족: …)"로만 나가 LLM이 '문제 없음'으로 읽는다 —
    실제로는 정반대(공고에서 요건을 못 뽑아 판정을 보류한 것)다.

    갈래를 나누는 근거: need_review_count > 0 은 축 자체가 확인필요인 정상
    경로. 0인데 게이트 축이 하나도 없으면 요건 추출 자체가 실패한 것(D-22
    계열), 0인데 게이트 축은 있으면 유형별 기대 축이 빠진 것(D-23 계열).
    D-22/D-23을 더 쪼개려면 공고 유형이 결과 계약에 있어야 하는데 지금은
    없으므로 여기까지가 데이터로 말할 수 있는 분해다.
    """
    if r.need_review_count:
        return f"확인 필요한 축 {r.need_review_count}개"
    if not any(a.axis_class == "gate" for a in r.axes):
        return "공고에서 자격 조건을 추출하지 못해 판정 보류"
    return "요건 정보가 일부 없어 판정 보류"


def _eligibility_block(state) -> str:
    """자격 판정 신호.

    판정 라벨만으로는 답변이 "통과입니다"를 되풀이할 수밖에 없다. 충족 축과
    충족 개수(satisfied/required)를 함께 실어 "무엇을 확인해서 통과인지"를
    말할 수 있게 한다 — 이 값들은 이미 state까지 와 있는데 지금까지 버려졌다.

    전체 건수(eligible_total)도 머리줄에 싣는다. 대화에는 상위 몇 건만 실리므로,
    이 수가 없으면 답변이 "이게 전부"인 것처럼 읽힌다.
    """
    results = state["eligibility"]
    if not results:
        return "(자격 판정 없음)"

    # 남은 건수까지 적어준다. 답변이 "나머지 N건도 보시겠습니까?"로 이어지는 게
    # 자연스러운데, N은 뺄셈이라 신호에 없으면 grounding 위반으로 잡혀 매 턴
    # 재생성이 한 번씩 더 돈다(실측: 164-5=159가 위반으로 걸림).
    total = state.get("eligible_total") or 0
    shown = len(results)
    # ⚠ "마감 임박"이라고 쓰지 않는다. 기본 정렬은 2026-07-30부터 우선순위
    # 기준(P1-lite: 게이트 축 수 → supp 충족 축 수 → 추정가격)이고 마감 순이
    # 아니다(match_results.possible_bids). 여기 문구가 정렬과 어긋나면 챗봇이
    # 말하는 순서와 실제 순서가 갈린다 — 실제로 그 상태로 배포된 적이 있다.
    lines = [f"자격 '가능' 공고 {total}건 중 상위 {shown}건 "
             f"(나머지 {total - shown}건)"
             if total > shown else f"자격 판정 {shown}건"]

    for r in results:
        label = _VERDICT_LABEL.get(r.verdict) if r.verdict else None
        if label is None:                       # verdict 없음(stub) → 기존 2분법
            label = "자격 통과" if r.passed else "자격 미달"
        detail = []
        if r.required_count:
            detail.append(f"충족 {r.satisfied_count}/{r.required_count}축")
        met = [a.axis for a in r.axes if a.status == "충족"]
        if met:
            detail.append("충족: " + ", ".join(met))
        if r.verdict == "확인필요":                      # N-4b
            detail.append(_review_note(r))
        if r.failed_reasons:
            # N-5: 미달 사유 뒤에 보완 경로를 붙인다. 문구는 도구 층의 정적
            # 테이블(remedy_hint)이 고정한 것으로, LLM이 만들지 않는다 —
            # 자격 안내는 틀리면 없느니만 못하다. supp 축과 '미등록' 보유값만
            # 힌트를 받고 게이트 축은 None이라 그대로 지나간다.
            parts = []
            for f in r.failed_reasons:
                line_ = f"{f.field}: 요구 {f.required} / 보유 {f.actual}"
                hint = remedy_hint(f.field, f.actual)
                if hint:
                    line_ += f" → {hint}"
                parts.append(line_)
            detail.append("; ".join(parts))
        line = f"- {r.bid_id}: {label}"
        if detail:
            line += f" ({' | '.join(detail)})"
        lines.append(line)
    return "\n".join(lines)


def _scores_block(state) -> str:
    lines = []
    for s in state["scores"]:
        lines.append(f"- {s.bid_id}: 자격 매칭도 {s.total}점")
        lines.extend(f"  - {i.axis} {i.points}점: {i.note}" for i in s.breakdown)
    return "\n".join(lines) or "(매칭도 없음)"


def _chunks_block(state) -> str:
    return "\n".join(f"[{c.bid_id}#{c.chunk_idx}] {c.text}"
                     for c in state["chunks"]) or "(발췌 없음)"


def _bids_block(state) -> str:
    """공고 목록 신호. 답변에서 공고를 공고명으로 지칭하기 위한 근거다.

    bid_search가 찾은 요약(bid_briefs)이 있으면 그것을 쓴다 — `검색` 갈래는
    문서 발췌 없이 이 목록만으로 답하므로, 마감일·금액·수요기관이 여기 실려야
    답변에 나올 수 있다(값은 bid_table 원문이며 노드가 계산하지 않는다).
    없으면 bid_id → 공고명 대응표로 폴백한다.

    건수와 항목 번호를 함께 싣는다. _TASK_LIST가 "headline에 건수를 적을 거면
    items 수와 맞춰라"라고 건수를 요구하는데, 그 숫자가 신호에 없으면
    check_grounding이 위반으로 잡는다 — 지시가 요구하는 값을 검증기가 막는
    모순이 된다(실측 2026-07-30: 위반 토큰 ['3','1'] 중 '1'이 건수였고 재생성
    2회 실패로 폴백까지 갔다). 번호를 매기면 1..N이 신호 안의 사실이 되므로,
    허용 집합을 넓히지 않고도 "3건 중 1건" 같은 표현이 그라운딩된다.
    """
    briefs = state.get("bid_briefs") or []
    if briefs:
        lines = [f"공고 {len(briefs)}건"]
        for i, b in enumerate(briefs, start=1):
            parts = [p for p in (b.institution, f"마감 {b.close_at}" if b.close_at
                                 else "", f"추정가격 {b.price}" if b.price else "",
                                 b.method) if p]
            label = _squeeze(b.name) or b.bid_id
            lines.append(f"{i}. {b.bid_id}: {label}"
                         + (f" | {' | '.join(parts)}" if parts else ""))
        return "\n".join(lines)

    names = state.get("bid_names") or {}
    if not names:
        return "(공고명 정보 없음)"
    lines = [f"공고 {len(names)}건"]
    lines += [f"{i}. {bid}: {name}"
              for i, (bid, name) in enumerate(sorted(names.items()), start=1)]
    return "\n".join(lines)


def _task_block(state: dict) -> str:
    """이번 턴 질의의 성격에 맞는 지시를 고른다.

    자격 판정이 신호에 있으면 그게 답의 중심이다. 그 다음은 스코프 폭으로
    가른다 — bid_ids가 1건이면 사용자가 그 공고 하나를 보고 있는 것이고,
    비어 있으면 검색 결과 여러 건을 훑는 상황이다.
    """
    route = state.get("route")
    if route == "자격" or (state["eligibility"] and not state["chunks"]):
        return _TASK_ELIGIBILITY
    if route == "검색":
        return _TASK_LIST
    bid_ids = (state.get("resolved_filters") or {}).get("bid_ids") or []
    return _TASK_DETAIL if len(bid_ids) == 1 else _TASK_LIST


def check_grounding(answer: str, signals: str) -> list[str]:
    """답변의 수치가 신호에 없으면 위반 목록으로 반환.

    신호 토큰은 날짜(2024.07.22)·천단위 콤마(10,000,000)처럼 여러 구획이
    하나의 정규식 토큰으로 묶인다. 답변이 그 구획 일부(날짜)나 콤마 제거형
    (정규화된 금액)을 인용해도 그라운딩 위반으로 보지 않도록, 허용 집합을
    원본 토큰 ∪ 콤마 제거형 ∪ (날짜형 토큰 한정) `.` 분해 구획으로 넓힌다.

    구획 분해는 `YYYY.M.D` 형태(`_DATE_TOKEN_RE`)의 날짜 토큰에만 적용한다.
    모든 토큰에 적용하면 "2.5배"의 "5"나 "87.745%"의 "87", "1,234,567"의
    "234" 같은 소수·금액 내부 자릿수가 무관한 답변 수치와 우연히 겹쳐
    그라운딩 검증을 무력화한다 — 날짜가 아닌 토큰은 분해하지 않는다.

    앞자리 0을 뗀 형태도 허용한다. bid_table의 마감일시가 "2026-07-30 10:00"로
    실려 신호 토큰이 "07"인데, 답변은 "7월 30일"로 쓰는 것이 자연스럽다. 이걸
    위반으로 잡으면 정상 답변이 재생성·폴백까지 떠밀린다.
    """
    signal_tokens = _NUM_RE.findall(signals)
    allowed = set(signal_tokens)
    for tok in signal_tokens:
        allowed.add(tok.replace(",", ""))
        if _DATE_TOKEN_RE.match(tok):
            allowed.update(tok.split("."))
        if tok.isdigit():
            allowed.add(tok.lstrip("0") or "0")      # "07" → "7"

    violations = []
    for tok in _NUM_RE.findall(answer):
        if tok in allowed or tok.replace(",", "") in allowed:
            continue
        violations.append(tok)
    return violations


# 요약에 이름을 실을 공고 수. 다음 턴이 "그 사업"을 풀기에 충분하면 되고,
# 길어지면 라우터 프롬프트만 무거워진다.
_SUMMARY_NAMES = 3


def build_summary(state) -> str:
    """last_summary·recent_turns의 한 줄 — 결정적 템플릿(LLM 안 씀).

    다음 턴의 rewrite 노드가 "그 사업"·"그중에서"를 실제 이름으로 풀 근거다.
    그래서 건수만이 아니라 공고명을 함께 싣는다. 갈래도 적는다 — "더 보여줘"처럼
    질의만으로는 무엇을 이어가는지 알 수 없는 경우에 필요하다.

    **접두사를 붙이지 않는다.** 이 함수는 한 턴을 서술하기만 하고, "언제인지"는
    읽는 쪽이 붙인다(rewrite._history_block의 `N턴 전` 라벨). 최근 10턴이 쌓이는데
    전부 "직전 턴:"으로 시작하면 어느 게 언제인지 구분되지 않는다. router.py는
    이미 자기 접두사("직전 턴 요약: ")를 붙이고 있어, 여기서 떼면 중복도 없어진다.

    LLM을 쓰지 않는 것이 중요하다. 공고 문서의 악성 문구가 요약을 타고 다음 턴
    프롬프트로 전파되는 2차 인젝션 경로를 막는 것이 이 함수가 결정적인 이유다
    (ADR 0006). 공고명은 bid_table 원문이라 같은 성격의 입력이므로 sanitize를
    거쳐 싣는다.

    공고 순서는 판정 → 발췌 → 검색 요약 순으로 고정한다(집합을 쓰면 순서가
    실행마다 달라져 "결정적 템플릿"이 아니게 된다).

    bid_ids는 조건이 아니라 공고 스코프이므로 조건 목록에서 뺀다. scope·
    bid_search가 이 키를 항상 채우기 때문에, 안 빼면 모든 요약이
    "조건: bid_ids"가 되어 의미 없는 토큰이 실린다.
    """
    bids = list(dict.fromkeys(
        [r.bid_id for r in state["eligibility"]] +
        [c.bid_id for c in state["chunks"]] +
        [b.bid_id for b in (state.get("bid_briefs") or [])]))
    names = state.get("bid_names") or {}

    label = state.get("route") or "공고"
    summary = f"{label} {len(bids)}건 안내"

    shown = [sanitize(_squeeze(names[b])) for b in bids[:_SUMMARY_NAMES]
             if names.get(b)]
    if shown:
        summary += " — " + ", ".join(shown)
        rest = len(bids) - len(shown)
        if rest > 0:
            summary += f" 외 {rest}건"

    keys = sorted(k for k in (state.get("resolved_filters") or {})
                  if k != "bid_ids")
    if keys:
        summary += f" (조건: {', '.join(keys)})"
    return summary


def _fallback_answer(state) -> str:
    """그라운딩 재생성까지 실패하면 생성문 대신 신호 원문만 안내한다.
    발췌는 신호 그 자체라 정의상 그라운딩 위반이 불가능하다(안전 우선).

    `검색` 갈래는 청크를 만들지 않으므로 발췌만 보면 빈 껍데기가 나간다 —
    그때는 공고 목록 신호를 그대로 안내한다.
    """
    chunks = "\n".join(f"- {sanitize(c.text)}" for c in state["chunks"])
    if chunks:
        return ("답변 생성 중 확인되지 않은 수치가 반복 감지되어, 확인된 공고 "
                "원문 발췌를 그대로 안내합니다.\n" + chunks)
    return ("답변 생성 중 확인되지 않은 수치가 반복 감지되어, 확인된 공고 "
            "정보를 그대로 안내합니다.\n" + _bids_block(state))


@node_logger("respond")
def respond_node(state: dict) -> dict:
    # bids_block(공고명)도 신호에 포함한다 — 공고명 속 숫자("2공구", "6호기" 등)가
    # 답변에 인용될 때 grounding 검증에서 위반으로 오탐되지 않게 하기 위함.
    signals = "\n".join([_bids_block(state), _eligibility_block(state),
                         _scores_block(state), _chunks_block(state)])
    prompt = _PROMPT.format(
        task_block=_task_block(state),
        bids_block=_bids_block(state),
        eligibility_block=_eligibility_block(state),
        scores_block=_scores_block(state),
        chunks_block=_chunks_block(state),
        # 사용자가 실제로 한 말. rewrite가 재구성한 질의로 답하면 사용자가
        # 하지 않은 말에 답하는 것처럼 읽힌다.
        query=state.get("original_query") or state["query"],
    )
    messages = [{"role": "user", "content": prompt}]

    answer = None
    for attempt in (1, 2):                 # 위반 시 위반 목록을 붙여 1회 재생성
        raw = llm.invoke(ModelTier.SYNTHESIS, messages=messages,
                         max_tokens=1500,
                         output_schema=RespondOutput.model_json_schema())
        candidate = render_answer(RespondOutput.model_validate(raw),
                                  state.get("bid_names"))
        violations = check_grounding(candidate, signals)
        if not violations:
            answer = candidate
            break
        logger.warning("grounding 위반 탐지(attempt=%d): %s", attempt, violations)
        messages = messages + [
            {"role": "assistant",
             "content": json.dumps(raw, ensure_ascii=False)},
            {"role": "user",
             "content": "다음 수치는 신호에 없다: " + ", ".join(violations)
                        + ". 신호에 있는 수치만 사용해 다시 작성하라."},
        ]
    if answer is None:
        logger.warning("grounding 재생성 실패 — 원문 발췌 폴백으로 대체")
        answer = _fallback_answer(state)

    citations = [Citation(bid_id=c.bid_id, file_id=c.file_id,
                          chunk_idx=c.chunk_idx, text=c.text)
                 for c in state["chunks"]]
    return {"answer": answer, "citations": citations}
