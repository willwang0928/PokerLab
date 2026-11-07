import pytest

from pokerlab.cards import parse_cards
from pokerlab.ranges import HandRange, RangeError, parse_range


@pytest.mark.parametrize(
    "text,count",
    [
        ("AA", 6),
        ("AKs", 4),
        ("AKo", 12),
        ("AK", 16),
        ("ka", 16),  # case-insensitive, either order
        ("22+", 78),
        ("TT+", 30),
        ("A2s+", 48),  # A2s..AKs
        ("KTo+", 36),  # KTo, KJo, KQo
        ("99-66", 24),
        ("66-99", 24),
        ("A5s-A2s", 16),
        ("T9s-65s", 20),
        ("65s-T9s", 20),
        ("AhKh", 1),
        ("any", 1326),
        ("AA, KK  AKs", 16),
        ("AA, AA, AsAh", 6),  # duplicates collapse
        ("QQ+, AKs, AhKh", 22),  # AhKh is already in AKs
    ],
)
def test_combo_counts(text, count):
    assert len(parse_range(text)) == count


def test_expansions_are_exact():
    assert {c[0].code + c[1].code for c in parse_range("KTo+")} >= {"KsTh", "KhJs", "KcQd"}
    assert all(a.rank == 13 and b.rank in (10, 11, 12) for a, b in parse_range("KTo+"))
    assert all(a.suit == b.suit for a, b in parse_range("T9s-65s"))
    assert {(a.rank, b.rank) for a, b in parse_range("T9s-65s")} == {
        (10, 9),
        (9, 8),
        (8, 7),
        (7, 6),
        (6, 5),
    }
    assert all(a.suit != b.suit for a, b in parse_range("AKo"))


def test_card_removal():
    r = parse_range("AA").without(parse_cards("As"))
    assert len(r) == 3  # AhAd, AhAc, AdAc
    assert len(parse_range("AKs").without(parse_cards("Ah Kd"))) == 2
    assert len(parse_range("any").without(parse_cards("As Kd 2c"))) == 49 * 48 // 2


def test_membership_is_order_independent():
    r = parse_range("AKs")
    a, k = parse_cards("As Ks")
    assert (a, k) in r and (k, a) in r


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "AAA", "A1s", "AKx", "AsAs", "AKs-QJo", "AKs-QTs", "99-AKs", "AA+K", "Zz"],
)
def test_rejects_bad_tokens(bad):
    with pytest.raises(RangeError):
        HandRange.parse(bad)
