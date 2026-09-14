"""
옵션 정규화 계약 테스트 (PER-182).

이 규칙의 값은 "몇 개가 합쳐졌나"가 아니라 **무엇이 절대 합쳐지면 안 되나**에 있다.
과대병합은 21호 근거를 13호 질문에 붙이는 사고(§7-1 치명 '잘못된 귀속')이므로,
아래 실측 사례를 회귀 테스트로 고정한다.

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import collections
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from option_norm import (  # noqa: E402
    KIND_PACK,
    KIND_SHADE,
    KIND_UNSTATED,
    OptionIndex,
    UnknownOptionScopeError,
)


def index_of(product_id: str, forms: dict[str, int]) -> OptionIndex:
    return OptionIndex.from_forms({product_id: collections.Counter(forms)})


class SameShadeMerges(unittest.TestCase):
    """같은 색상이 판촉 포장만 달리해 흩어진 경우 — 합쳐져야 한다."""

    def test_판촉_묶음과_사은품을_걷어낸다(self):
        # 3CE 캐시미어 허그 립스틱 '허쉬 레드' 실측 3형태
        idx = index_of("p001", {
            "허쉬 레드(뉴트럴 베스트)": 58,
            "[기획] 허쉬 레드+파우치": 17,
            "[기획] 허쉬 레드+미니립스틱": 7,
        })
        keys = idx.shade_keys("p001")
        self.assertEqual(keys, {"허쉬레드": 82}, "한 색이 세 조각으로 남으면 안 된다")

    def test_띄어쓰기_차이는_같은_색이다(self):
        idx = index_of("p002", {"[올리브영단독] 03 베어 그레이프": 40, "[두유][기획]03베어그레이프": 51})
        self.assertEqual(list(idx.shade_keys("p002")), ["3|베어그레이프"])

    def test_호수가_본문에_붙어_있어도_읽는다(self):
        # 더샘 컨실러 실측 — '01코렉트 베이지' 와 '01 코렉트 베이지'
        idx = index_of("p009", {"[브라이트너기획] 01코렉트 베이지": 132, "01 코렉트 베이지": 86})
        self.assertEqual(list(idx.shade_keys("p009")), ["1|코렉트베이지"])

    def test_콜라보_묶음은_코퍼스가_판촉으로_판정한다(self):
        # 래퍼-B: '[갸루키티]' 를 떼면 이미 있는 색상 이름이 된다
        idx = index_of("p021", {"[갸루키티]30호 클래시누드블러링": 141, "30 클래시누드블러링": 71})
        self.assertEqual(idx.shade_keys("p021"), {"30|클래시누드블러링": 212})
        self.assertIn("갸루키티", idx.wrappers("p021"))

    def test_호수_없는_표기는_유일한_호수로_합친다(self):
        idx = index_of("p018", {"39 로즈코티지": 13, "로즈코티지": 6})
        self.assertEqual(idx.shade_keys("p018"), {"39|로즈코티지": 19})


class DifferentShadesStaySeparate(unittest.TestCase):
    """합쳐지면 잘못된 귀속이 되는 경우 — 반드시 갈려 있어야 한다."""

    def test_호수만_다른_색을_합치지_않는다(self):
        # 헤라 블랙 쿠션: 이름이 'N1' 로 같고 호수만 다르다. 호수를 버리면 6색이 1색이 된다
        idx = index_of("p017", {
            "21N1(본품+리필)": 36, "17N1(본품+리필)": 16, "13N1(본품+리필)": 26,
        })
        self.assertEqual(
            sorted(idx.shade_keys("p017")), ["13|N1", "17|N1", "21|N1"],
        )

    def test_같은_이름에_호수가_둘이면_합치지_않는다(self):
        idx = index_of("p052", {"21호 아이보리": 126, "[숄더백 기획]2호 아이보리": 1})
        self.assertEqual(sorted(idx.shade_keys("p052")), ["21|아이보리", "2|아이보리"])

    def test_이름이_포함관계여도_다른_색이다(self):
        # 정샘물 쿠션: '페어' 와 '페어라이트' 는 서로 다른 호수다
        idx = index_of("p014", {"[본품+리필] 페어": 26, "[본품+리필] 페어라이트": 57})
        self.assertEqual(sorted(idx.shade_keys("p014")), ["페어", "페어라이트"])

    def test_함께_담긴_다른_제품의_호수와_섞이지_않는다(self):
        # 에뛰드 마스카라 '02 브라운' 과, 같이 파는 픽서의 '02 블랙' 은 다른 물건이다
        idx = index_of("p020", {"(1+1/기획) 02 브라운": 14, "닥터 마스카라 픽서 02 블랙": 20})
        self.assertEqual(len(idx.shade_keys("p020")), 2)

    def test_못_알아본_괄호는_식별자로_남긴다(self):
        # 닥터지 수딩크림 실측 — 용기(튜브/jar)가 다르면 합치지 않는다.
        # 모르는 묶음을 지우는 쪽이 과대병합이므로 남기는 것이 기본값이다
        idx = index_of("p041", {"70ml(튜브)+30ml(튜브)": 18, "70ml(jar)+30ml(튜브)": 1})
        self.assertEqual(len(idx.shade_keys("p041")), 2)

    def test_용량뿐인_괄호는_구성이라_합쳐진다(self):
        # '(8구)' 는 용량·수량뿐이라 판촉으로 본다. 색상이 아니라 구성이 합쳐지는 것이고,
        # 색상 질문은 이런 키를 범위로 걸 수 없다 (resolve_scope 에러)
        idx = index_of("p025", {"팔레트 케이스(8구)": 7, "팔레트 케이스(4구)": 5})
        self.assertEqual(list(idx.shade_keys("p025")), ["팔레트케이스"])


class PackAndUnstated(unittest.TestCase):
    """색상이 아닌 옵션 — 용량/구성과 미기재는 서로 다른 상태다."""

    def test_용량_구성만_있으면_색상이_아니다(self):
        idx = index_of("p026", {"50ml (+35ml)": 364, "50ml+50ml": 1, "본품+리필": 97})
        self.assertEqual(idx.shade_keys("p026"), {}, "용량은 색상 키가 되지 않는다")
        self.assertEqual(idx.shade_key("p026", "50ml (+35ml)"), (None, KIND_PACK))

    def test_미기재와_용량은_다른_종류다(self):
        idx = index_of("p026", {"본품+리필": 97})
        self.assertEqual(idx.shade_key("p026", None), (None, KIND_UNSTATED))
        self.assertEqual(idx.shade_key("p026", "   "), (None, KIND_UNSTATED))
        self.assertEqual(idx.shade_key("p026", "본품+리필"), (None, KIND_PACK))

    def test_색상축이_없는_제품을_구분한다(self):
        idx = index_of("p026", {"50ml (+35ml)": 364})
        self.assertFalse(idx.has_shade_axis("p026"))


class ScopeResolution(unittest.TestCase):
    """질문의 옵션 범위는 리뷰와 **같은 정규화**를 거쳐야 한다."""

    def test_자연어_표기를_색상_키로_옮긴다(self):
        idx = index_of("p017", {"21N1(본품+리필)": 36, "17N1(본품+리필)": 16})
        self.assertEqual(idx.resolve_scope("p017", "21N1"), "21|N1")
        self.assertEqual(idx.resolve_scope("p017", "[기획] 21N1"), "21|N1")

    def test_관측되지_않은_색상은_에러다(self):
        idx = index_of("p017", {"21N1(본품+리필)": 36})
        with self.assertRaises(UnknownOptionScopeError):
            idx.resolve_scope("p017", "99Z9")

    def test_색상이_아닌_범위는_에러다(self):
        idx = index_of("p026", {"50ml (+35ml)": 364})
        with self.assertRaises(UnknownOptionScopeError):
            idx.resolve_scope("p026", "50ml")


class Determinism(unittest.TestCase):
    def test_입력_순서가_결과를_바꾸지_않는다(self):
        forms = {"[기획] 허쉬 레드+파우치": 17, "허쉬 레드(뉴트럴 베스트)": 58, "[OY단독]허쉬 로즈": 5}
        a = index_of("p001", forms).shade_keys("p001")
        b = index_of("p001", dict(reversed(list(forms.items())))).shade_keys("p001")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
